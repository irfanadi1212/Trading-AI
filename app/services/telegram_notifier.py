"""
app/services/telegram_notifier.py
=====================================
Format & kirim trading plan ke Telegram Bot API.

Menggunakan MarkdownV2 dengan escaping otomatis karakter spesial,
supaya tidak rapuh terhadap isi teks apapun (termasuk output LLM
yang tidak terprediksi).
"""

import logging
import re
from typing import Optional

import requests

from app.core.config import get_settings

logger = logging.getLogger("telegram_notifier")
settings = get_settings()

TELEGRAM_API_URL = f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}/sendMessage"

# Karakter yang WAJIB di-escape untuk Telegram MarkdownV2
MARKDOWNV2_SPECIAL_CHARS = r"_*[]()~`>#+-=|{}.!"


def escape_markdown_v2(text: str) -> str:
    """Escape semua karakter spesial MarkdownV2 agar tidak bikin parsing gagal."""
    escaped = []
    for char in text:
        if char in MARKDOWNV2_SPECIAL_CHARS:
            escaped.append("\\" + char)
        else:
            escaped.append(char)
    return "".join(escaped)


def format_trading_plan_message(result: dict) -> str:
    """Format hasil analisa jadi pesan Telegram (MarkdownV2, semua teks di-escape)."""
    symbol = result.get("symbol", "N/A")
    confidence = result.get("confidence_status", "UNKNOWN")

    confidence_emoji = {
        "HIGH_CONFIDENCE": "🟢",
        "STANDARD_CONFIDENCE": "🟡",
        "LOW_CONFIDENCE_OR_NO_SETUP": "🔴",
        "TIMEOUT": "⚫",
        "FAILED": "⚫",
    }.get(confidence, "⚪")

    symbol_esc = escape_markdown_v2(symbol)
    confidence_esc = escape_markdown_v2(confidence)

    header = f"{confidence_emoji} *Alchemist Analysis: {symbol_esc}*\n"
    header += f"Confidence: `{confidence_esc}`\n\n"

    if confidence in ("LOW_CONFIDENCE_OR_NO_SETUP", "TIMEOUT", "FAILED"):
        body = escape_markdown_v2("Tidak ada setup valid ditemukan saat ini. Ini bukan rekomendasi entry.")
        if result.get("error"):
            body += "\n\n" + escape_markdown_v2(f"Error: {result['error']}")
    else:
        plan = result.get("trading_plan", "Tidak ada detail.")
        if len(plan) > 3000:
            plan = plan[:3000] + " (dipotong)"
        body = escape_markdown_v2(plan)

    disclaimer = "\n\n" + escape_markdown_v2(
        "Disclaimer: Analisa otomatis berbasis dokumen strategi, bukan nasihat keuangan."
    )

    return header + body + disclaimer


def send_telegram_message(message: str, chat_id: Optional[str] = None) -> bool:
    target_chat_id = chat_id or settings.TELEGRAM_CHAT_ID

    if not settings.TELEGRAM_BOT_TOKEN or not target_chat_id:
        logger.warning("TELEGRAM_BOT_TOKEN atau TELEGRAM_CHAT_ID belum dikonfigurasi.")
        return False

    try:
        response = requests.post(
            TELEGRAM_API_URL,
            json={"chat_id": target_chat_id, "text": message, "parse_mode": "MarkdownV2"},
            timeout=10,
        )
        response.raise_for_status()
        logger.info(f"Pesan Telegram terkirim ke chat_id={target_chat_id}")
        return True
    except requests.exceptions.Timeout:
        logger.error("Timeout mengirim pesan ke Telegram API.")
        return False
    except requests.exceptions.RequestException as exc:
        logger.error(f"Gagal mengirim pesan Telegram: {exc}. Response: {getattr(exc.response, 'text', '')}")
        return False


if __name__ == "__main__":
    test_result = {
        "symbol": "XAU/USD",
        "confidence_status": "STANDARD_CONFIDENCE",
        "trading_plan": "Ini adalah test dari telegram_notifier.py. Jika Anda menerima pesan ini, konfigurasi bot sudah benar.",
    }
    message = format_trading_plan_message(test_result)
    success = send_telegram_message(message)
    print("Berhasil terkirim:" if success else "GAGAL terkirim.", success)