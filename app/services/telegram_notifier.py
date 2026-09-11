"""
app/services/telegram_notifier.py
=====================================
Format & kirim trading plan ke Telegram Bot API.

Sejak update template, synthesizer_agent SUDAH menghasilkan format
lengkap (emoji, entry/SL/TP, reasoning) -- fungsi di sini hanya
escape untuk MarkdownV2 dan tambah disclaimer singkat di akhir.

Error handling: kegagalan kirim Telegram TIDAK BOLEH menggagalkan
alur analisa utama -- kegagalan notifikasi hanya di-log dan
dilaporkan terpisah ke caller.
"""

import logging
from typing import Optional

import requests

from app.core.config import get_settings

logger = logging.getLogger("telegram_notifier")
settings = get_settings()

TELEGRAM_API_URL = f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}/sendMessage"

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
    """
    Format hasil analisa untuk Telegram. synthesizer_agent sudah
    menghasilkan format lengkap -- fungsi ini escape untuk MarkdownV2
    dan tambah disclaimer singkat.
    """
    confidence = result.get("confidence_status", "UNKNOWN")

    if confidence in ("TIMEOUT", "FAILED"):
        error_text = result.get("error", "Analisa gagal diproses.")
        return escape_markdown_v2(f"⚫ Analisa gagal: {error_text}")

    plan = result.get("trading_plan", "Tidak ada detail.")
    if len(plan) > 3500:
        plan = plan[:3500] + " (dipotong)"

    escaped_plan = escape_markdown_v2(plan)
    disclaimer = "\n\n" + escape_markdown_v2("Bukan nasihat keuangan.")

    return escaped_plan + disclaimer


def send_telegram_message(message: str, chat_id: Optional[str] = None) -> bool:
    """
    Kirim pesan ke Telegram. Tidak melempar exception -- notifikasi gagal
    tidak boleh menggagalkan alur analisa utama yang sudah selesai.
    """
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