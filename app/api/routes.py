"""
app/api/routes.py
=====================
Endpoint API: submit analisa, polling hasil, & kirim notifikasi Telegram.
"""

import logging
from typing import Optional, Literal

from fastapi import APIRouter, HTTPException
from celery.result import AsyncResult
from pydantic import BaseModel, Field, field_validator

from app.workers.celery_app import celery_app
from app.workers.tasks import run_market_analysis, send_telegram_notification

logger = logging.getLogger("api_routes")
router = APIRouter()

VALID_INTERVALS = {"1min", "5min", "15min", "30min", "1h", "4h", "1day"}


# ------------------------------------------------------------------
# Schemas
# ------------------------------------------------------------------
class AnalysisRequest(BaseModel):
    symbol: str = Field(..., examples=["XAU/USD", "EUR/USD"])
    interval_htf: str = Field(default="4h")
    interval_ltf: str = Field(default="15min")

    @field_validator("interval_htf", "interval_ltf")
    @classmethod
    def validate_interval(cls, v: str) -> str:
        if v not in VALID_INTERVALS:
            raise ValueError(f"Interval '{v}' tidak valid. Pilihan: {sorted(VALID_INTERVALS)}")
        return v

    @field_validator("symbol")
    @classmethod
    def validate_symbol(cls, v: str) -> str:
        if "/" not in v:
            raise ValueError("Format symbol harus 'BASE/QUOTE', contoh: 'XAU/USD'")
        return v.upper()


class TaskSubmitResponse(BaseModel):
    task_id: str
    status: str = "PENDING"
    message: str = "Analisa sedang diproses di background."


class TaskStatusResponse(BaseModel):
    task_id: str
    status: Literal["PENDING", "STARTED", "SUCCESS", "FAILURE", "RETRY"]
    result: Optional[dict] = None


class TelegramNotifyRequest(BaseModel):
    task_id: str = Field(..., description="task_id dari hasil analisa yang sudah SUCCESS")


class TelegramNotifyResponse(BaseModel):
    submitted: bool
    message: str = "Notifikasi Telegram sedang diproses di background."


# ------------------------------------------------------------------
# Endpoints
# ------------------------------------------------------------------
@router.get("/health")
async def health_check():
    try:
        celery_app.control.inspect(timeout=2).ping()
        broker_status = "connected"
    except Exception as exc:
        logger.error(f"Health check gagal terhubung ke broker: {exc}")
        broker_status = "disconnected"
    return {"status": "ok", "broker": broker_status}


@router.post("/analyze", response_model=TaskSubmitResponse, status_code=202)
async def submit_analysis(payload: AnalysisRequest):
    try:
        task = run_market_analysis.delay(
            symbol=payload.symbol,
            interval_htf=payload.interval_htf,
            interval_ltf=payload.interval_ltf,
        )
        logger.info(f"Task disubmit: {task.id} untuk {payload.symbol}")
        return TaskSubmitResponse(task_id=task.id)
    except Exception as exc:
        logger.error(f"Gagal submit task ke Celery: {exc}")
        raise HTTPException(status_code=503, detail="Sistem antrian analisa sedang tidak tersedia.")


@router.get("/analyze/{task_id}", response_model=TaskStatusResponse)
async def get_analysis_result(task_id: str):
    try:
        result = AsyncResult(task_id, app=celery_app)
        response = TaskStatusResponse(task_id=task_id, status=result.status)

        if result.status == "SUCCESS":
            response.result = result.result
        elif result.status == "FAILURE":
            response.result = {"error": "Analisa gagal diproses. Silakan submit ulang."}
            logger.error(f"Task {task_id} FAILURE: {result.traceback}")

        return response
    except Exception as exc:
        logger.error(f"Gagal mengambil status task {task_id}: {exc}")
        raise HTTPException(status_code=500, detail="Gagal mengambil status task.")


@router.post("/notify/telegram", response_model=TelegramNotifyResponse, status_code=202)
async def notify_telegram(payload: TelegramNotifyRequest):
    try:
        existing = AsyncResult(payload.task_id, app=celery_app)
        if existing.status != "SUCCESS":
            raise HTTPException(
                status_code=409,
                detail=f"Task belum selesai (status: {existing.status}). Tunggu sampai SUCCESS.",
            )
        send_telegram_notification.delay(task_id=payload.task_id)
        logger.info(f"Notifikasi Telegram disubmit untuk task {payload.task_id}")
        return TelegramNotifyResponse(submitted=True)
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"Gagal submit notifikasi Telegram: {exc}")
        raise HTTPException(status_code=503, detail="Gagal mengirim notifikasi Telegram saat ini.")