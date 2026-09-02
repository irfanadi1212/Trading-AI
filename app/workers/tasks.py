"""
app/workers/tasks.py
========================
Task asinkron: (1) menjalankan CrewAI untuk analisa market, dan
(2) mengirim notifikasi Telegram -- dipisah queue agar tidak
saling bersaing resource.
"""

import hashlib
import json
import logging

import redis as redis_lib
from celery.exceptions import SoftTimeLimitExceeded
from celery.result import AsyncResult

from app.workers.celery_app import celery_app
from app.core.config import get_settings
from app.services.crew_agents import build_crew
from app.services.telegram_notifier import format_trading_plan_message, send_telegram_message

logger = logging.getLogger("celery_tasks")
settings = get_settings()

redis_client = redis_lib.Redis.from_url(settings.REDIS_URL, decode_responses=True)

CACHE_TTL_SECONDS = 300  # 5 menit -- cegah duplikasi request beruntun
LOW_CONFIDENCE_MARKERS = ["NO VALID SETUP", "insufficient evidence", "undetermined"]


def _cache_key(symbol: str, interval_htf: str, interval_ltf: str) -> str:
    raw = f"{symbol}|{interval_htf}|{interval_ltf}"
    return "analysis_cache:" + hashlib.sha256(raw.encode()).hexdigest()


def _evaluate_confidence(result_text: str) -> str:
    """Klasifikasi hasil trading plan berdasarkan marker eksplisit dari synthesizer_agent."""
    lowered = result_text.lower()
    if any(marker.lower() in lowered for marker in LOW_CONFIDENCE_MARKERS):
        return "LOW_CONFIDENCE_OR_NO_SETUP"
    if "high confluence" in lowered:
        return "HIGH_CONFIDENCE"
    return "STANDARD_CONFIDENCE"


@celery_app.task(
    bind=True,
    max_retries=3,
    default_retry_delay=10,
    queue="analysis_heavy",
)
def run_market_analysis(self, symbol: str, interval_htf: str, interval_ltf: str):
    """Entry point task: dipanggil dari app/api/routes.py."""
    cache_key = _cache_key(symbol, interval_htf, interval_ltf)

    cached = redis_client.get(cache_key)
    if cached:
        logger.info(f"Cache hit untuk {symbol} {interval_htf}/{interval_ltf}, skip re-analysis.")
        payload = json.loads(cached)
        payload["from_cache"] = True
        return payload

    try:
        logger.info(f"Memulai analisa CrewAI: {symbol} HTF={interval_htf} LTF={interval_ltf}")

        crew = build_crew(symbol=symbol, interval_htf=interval_htf, interval_ltf=interval_ltf)
        raw_result = crew.kickoff()
        result_text = str(raw_result)

        confidence_status = _evaluate_confidence(result_text)

        payload = {
            "symbol": symbol,
            "interval_htf": interval_htf,
            "interval_ltf": interval_ltf,
            "confidence_status": confidence_status,
            "trading_plan": result_text,
            "from_cache": False,
        }

        redis_client.setex(cache_key, CACHE_TTL_SECONDS, json.dumps(payload))
        logger.info(f"Analisa selesai: {symbol} -> status={confidence_status}")
        return payload

    except SoftTimeLimitExceeded:
        logger.error(f"Soft time limit terlampaui untuk {symbol}. Analisa dibatalkan.")
        return {
            "symbol": symbol,
            "confidence_status": "TIMEOUT",
            "trading_plan": None,
            "error": "Analisa melebihi batas waktu (soft limit).",
        }

    except Exception as exc:
        retry_count = self.request.retries
        backoff = 10 * (4 ** retry_count)  # 10s -> 40s -> 160s
        logger.warning(
            f"Analisa gagal untuk {symbol} (percobaan ke-{retry_count + 1}): {exc}. "
            f"Retry dalam {backoff} detik."
        )
        try:
            raise self.retry(exc=exc, countdown=backoff)
        except self.MaxRetriesExceededError:
            logger.error(f"Analisa {symbol} gagal permanen setelah 3x percobaan: {exc}")
            return {
                "symbol": symbol,
                "confidence_status": "FAILED",
                "trading_plan": None,
                "error": f"Gagal setelah 3x percobaan: {str(exc)}",
            }


@celery_app.task(
    bind=True,
    max_retries=2,
    default_retry_delay=5,
    queue="notifications_light",
)
def send_telegram_notification(self, task_id: str):
    """
    Task ringan (queue notifications_light) agar pengiriman Telegram
    tidak bersaing resource dengan task analisa berat (analysis_heavy).
    """
    try:
        analysis_result = AsyncResult(task_id, app=celery_app)

        if analysis_result.status != "SUCCESS":
            logger.warning(
                f"Tidak bisa kirim notifikasi: task {task_id} berstatus "
                f"{analysis_result.status} (belum SUCCESS)."
            )
            return {"sent": False, "reason": f"Task status is {analysis_result.status}, not SUCCESS."}

        result_payload = analysis_result.result
        message = format_trading_plan_message(result_payload)
        sent = send_telegram_message(message)

        if not sent:
            raise self.retry(countdown=5)

        return {"sent": True}

    except self.MaxRetriesExceededError:
        logger.error(f"Gagal mengirim notifikasi Telegram untuk task {task_id} setelah retry.")
        return {"sent": False, "reason": "Max retries exceeded."}
    except Exception as exc:
        logger.error(f"Error tak terduga saat kirim notifikasi Telegram: {exc}")
        return {"sent": False, "reason": str(exc)}