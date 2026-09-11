"""
app/services/telegram_bot_listener.py
========================================
Bot listener Telegram dengan UI tombol (bukan cuma text command).
Panggil FastAPI lewat HTTP (bukan import Celery langsung) -- supaya
portable, bisa dipindah ke Termux/VPS nanti tanpa install crewai dkk.

Fitur:
- Menu persisten (reply keyboard) selalu muncul di bawah keyboard
- Tombol inline untuk pilih instrumen cepat
- Riwayat 5 analisa terakhir (disimpan di memori, reset kalau bot restart)
"""

import time
import logging
from collections import deque

import requests

from app.core.config import get_settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("telegram_bot_listener")

settings = get_settings()
TG_BASE_URL = f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}"
API_BASE_URL = "http://localhost:8000"  # FastAPI kita sendiri

POLL_TIMEOUT_SECONDS = 30
TASK_CHECK_INTERVAL_SECONDS = 3
TASK_MAX_WAIT_SECONDS = 300

PRESET_SYMBOLS = ["XAU/USD", "EUR/USD", "GBP/USD", "BTC/USD"]
riwayat_analisa = deque(maxlen=5)  # simpan 5 terakhir di memori


# ------------------------------------------------------------------
# UI Components: Menu Persisten & Tombol Inline
# ------------------------------------------------------------------
def menu_persisten() -> dict:
    """Reply keyboard -- selalu nempel di bawah keyboard HP, 4 tombol utama."""
    return {
        "keyboard": [
            ["📊 Analisa", "🕐 Riwayat"],
            ["⚡ Preset", "❓ Bantuan"],
        ],
        "resize_keyboard": True,
        "is_persistent": True,
    }


def tombol_pilih_instrumen() -> dict:
    """Inline keyboard -- tombol pilihan instrumen, tap langsung submit."""
    buttons = [[{"text": s, "callback_data": f"go:{s}"}] for s in PRESET_SYMBOLS]
    return {"inline_keyboard": buttons}


# ------------------------------------------------------------------
# HTTP Helpers ke Telegram & FastAPI kita sendiri
# ------------------------------------------------------------------
def send_message(chat_id: str, text: str, reply_markup: dict = None) -> None:
    payload = {"chat_id": chat_id, "text": text}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    try:
        requests.post(f"{TG_BASE_URL}/sendMessage", json=payload, timeout=10)
    except requests.exceptions.RequestException as exc:
        logger.error(f"Gagal kirim pesan ke {chat_id}: {exc}")


def answer_callback(callback_query_id: str) -> None:
    """Hilangkan status 'loading' di tombol setelah ditekan."""
    try:
        requests.post(
            f"{TG_BASE_URL}/answerCallbackQuery",
            json={"callback_query_id": callback_query_id},
            timeout=10,
        )
    except requests.exceptions.RequestException:
        pass


def submit_analysis(symbol: str, interval_htf: str = "4h", interval_ltf: str = "15min") -> tuple:
    """Panggil FastAPI /analyze, kembalikan (task_id, error)."""
    try:
        response = requests.post(
            f"{API_BASE_URL}/analyze",
            json={"symbol": symbol, "interval_htf": interval_htf, "interval_ltf": interval_ltf},
            timeout=10,
        )
        response.raise_for_status()
        return response.json()["task_id"], None
    except requests.exceptions.RequestException as exc:
        return None, str(exc)


def poll_and_reply(task_id: str, chat_id: str, symbol: str) -> None:
    """Polling ke FastAPI sampai selesai, kirim hasilnya (dipanggil di thread)."""
    elapsed = 0
    while elapsed < TASK_MAX_WAIT_SECONDS:
        try:
            response = requests.get(f"{API_BASE_URL}/analyze/{task_id}", timeout=10)
            response.raise_for_status()
            data = response.json()

            if data["status"] == "SUCCESS":
                result = data["result"]
                plan = result.get("trading_plan", "Tidak ada detail.")
                send_message(chat_id, plan, reply_markup=menu_persisten())
                riwayat_analisa.append({"symbol": symbol, "confidence": result.get("confidence_status")})
                return

            if data["status"] == "FAILURE":
                send_message(chat_id, f"⚫ Analisa {symbol} gagal diproses.", reply_markup=menu_persisten())
                return

        except requests.exceptions.RequestException as exc:
            logger.error(f"Gagal polling task {task_id}: {exc}")

        time.sleep(TASK_CHECK_INTERVAL_SECONDS)
        elapsed += TASK_CHECK_INTERVAL_SECONDS

    send_message(chat_id, f"⏱️ Analisa {symbol} timeout (>5 menit).", reply_markup=menu_persisten())


# ------------------------------------------------------------------
# Handlers
# ------------------------------------------------------------------
def handle_text(chat_id: str, text: str) -> None:
    text = text.strip()

    if text in ("/start", "❓ Bantuan"):
        send_message(
            chat_id,
            "👋 Selamat datang di Alchemist Signal Bot\n\n"
            "📊 Analisa — pilih instrumen & jalankan analisa\n"
            "🕐 Riwayat — lihat 5 analisa terakhir\n"
            "⚡ Preset — instrumen favorit cepat",
            reply_markup=menu_persisten(),
        )
        return

    if text == "📊 Analisa" or text == "⚡ Preset":
        send_message(chat_id, "Pilih instrumen:", reply_markup=tombol_pilih_instrumen())
        return

    if text == "🕐 Riwayat":
        if not riwayat_analisa:
            send_message(chat_id, "Belum ada riwayat analisa.", reply_markup=menu_persisten())
            return
        lines = [f"• {r['symbol']} — {r['confidence']}" for r in reversed(riwayat_analisa)]
        send_message(chat_id, "🕐 5 analisa terakhir:\n\n" + "\n".join(lines), reply_markup=menu_persisten())
        return

    send_message(chat_id, "Command tidak dikenali. Gunakan menu di bawah.", reply_markup=menu_persisten())


PRESET_HTF = ["1h", "4h", "1day"]
PRESET_LTF = ["5min", "15min", "30min"]

# Simpan pilihan sementara per chat (symbol yang sudah dipilih, menunggu timeframe)
pending_selection = {}


def tombol_pilih_htf() -> dict:
    buttons = [[{"text": tf, "callback_data": f"htf:{tf}"}] for tf in PRESET_HTF]
    return {"inline_keyboard": buttons}


def tombol_pilih_ltf() -> dict:
    buttons = [[{"text": tf, "callback_data": f"ltf:{tf}"}] for tf in PRESET_LTF]
    return {"inline_keyboard": buttons}


def handle_callback(chat_id: str, callback_data: str, callback_query_id: str) -> None:
    answer_callback(callback_query_id)

    if callback_data.startswith("go:"):
        symbol = callback_data.replace("go:", "")
        pending_selection[chat_id] = {"symbol": symbol}
        send_message(chat_id, f"{symbol} dipilih. Pilih timeframe HTF (bias):", reply_markup=tombol_pilih_htf())
        return

    if callback_data.startswith("htf:"):
        htf = callback_data.replace("htf:", "")
        if chat_id not in pending_selection:
            send_message(chat_id, "Sesi kadaluarsa, mulai lagi dari /start.", reply_markup=menu_persisten())
            return
        pending_selection[chat_id]["htf"] = htf
        send_message(chat_id, f"HTF {htf} dipilih. Pilih timeframe LTF (entry):", reply_markup=tombol_pilih_ltf())
        return

    if callback_data.startswith("ltf:"):
        ltf = callback_data.replace("ltf:", "")
        if chat_id not in pending_selection or "symbol" not in pending_selection[chat_id]:
            send_message(chat_id, "Sesi kadaluarsa, mulai lagi dari /start.", reply_markup=menu_persisten())
            return

        selection = pending_selection.pop(chat_id)
        symbol = selection["symbol"]
        htf = selection["htf"]

        task_id, error = submit_analysis(symbol, interval_htf=htf, interval_ltf=ltf)
        if error:
            send_message(chat_id, f"Gagal submit analisa: {error}", reply_markup=menu_persisten())
            return

        send_message(chat_id, f"🔄 Analisa {symbol} ({htf}/{ltf}) sedang diproses...")

        import threading
        threading.Thread(target=poll_and_reply, args=(task_id, chat_id, symbol), daemon=True).start()


def process_update(update: dict) -> None:
    if "message" in update:
        chat_id = str(update["message"]["chat"]["id"])
        text = update["message"].get("text", "")
        if text:
            handle_text(chat_id, text)

    elif "callback_query" in update:
        cq = update["callback_query"]
        chat_id = str(cq["message"]["chat"]["id"])
        handle_callback(chat_id, cq["data"], cq["id"])


def run_listener() -> None:
    if not settings.TELEGRAM_BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN belum diisi.")
        return

    logger.info("Bot listener dimulai (mode tombol). Menunggu interaksi...")
    offset = 0

    while True:
        try:
            response = requests.get(
                f"{TG_BASE_URL}/getUpdates",
                params={"offset": offset, "timeout": POLL_TIMEOUT_SECONDS},
                timeout=POLL_TIMEOUT_SECONDS + 10,
            )
            response.raise_for_status()
            for update in response.json().get("result", []):
                offset = update["update_id"] + 1
                process_update(update)

        except requests.exceptions.Timeout:
            continue
        except requests.exceptions.RequestException as exc:
            logger.error(f"Error polling: {exc}. Retry 5 detik.")
            time.sleep(5)
        except KeyboardInterrupt:
            logger.info("Bot dihentikan.")
            break


if __name__ == "__main__":
    run_listener()