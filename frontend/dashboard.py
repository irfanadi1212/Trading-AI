"""
frontend/dashboard.py
=========================
UI Streamlit: pilih instrumen & timeframe, submit ke FastAPI backend,
polling hasil via rerun (bukan blocking loop -- mencegah SessionInfo
error di lingkungan ter-proxy seperti Codespace).
"""

import time
import logging

import requests
import streamlit as st

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("streamlit_dashboard")

API_BASE_URL = "http://localhost:8000"
POLL_INTERVAL_SECONDS = 3
MAX_POLL_ATTEMPTS = 100

st.set_page_config(page_title="Alchemist Strategy Analyzer", layout="centered")
st.title("🔮 Alchemist AI Market Analyzer")
st.caption("Analisa berbasis strategi Alchemist (RAG-grounded, tanpa halusinasi indikator generik)")

VALID_INTERVALS = ["1min", "5min", "15min", "30min", "1h", "4h", "1day"]

with st.form("analysis_form"):
    col1, col2 = st.columns(2)
    with col1:
        symbol = st.text_input("Instrumen (format BASE/QUOTE)", value="XAU/USD")
    with col2:
        st.write("")

    col3, col4 = st.columns(2)
    with col3:
        interval_htf = st.selectbox("Timeframe HTF (bias/struktur)", VALID_INTERVALS, index=5)
    with col4:
        interval_ltf = st.selectbox("Timeframe LTF (entry presisi)", VALID_INTERVALS, index=2)

    send_telegram = st.checkbox("Kirim hasil ke Telegram juga", value=False)
    submitted = st.form_submit_button("🚀 Jalankan Analisa")


def submit_analysis(symbol: str, interval_htf: str, interval_ltf: str):
    try:
        response = requests.post(
            f"{API_BASE_URL}/analyze",
            json={"symbol": symbol, "interval_htf": interval_htf, "interval_ltf": interval_ltf},
            timeout=10,
        )
        response.raise_for_status()
        return response.json()["task_id"], None
    except requests.exceptions.RequestException as exc:
        return None, f"Gagal menghubungi backend: {exc}"


def render_trading_plan(result: dict):
    confidence = result.get("confidence_status", "UNKNOWN")
    confidence_color = {
        "HIGH_CONFIDENCE": "🟢",
        "STANDARD_CONFIDENCE": "🟡",
        "LOW_CONFIDENCE_OR_NO_SETUP": "🔴",
        "TIMEOUT": "⚫",
        "FAILED": "⚫",
    }.get(confidence, "⚪")

    st.subheader(f"{confidence_color} Confidence: {confidence}")

    if confidence in ("LOW_CONFIDENCE_OR_NO_SETUP", "TIMEOUT", "FAILED"):
        st.warning(
            "Tidak ada setup valid yang cukup kuat dari dokumen strategi untuk "
            "instrumen/timeframe ini saat ini. Ini BUKAN rekomendasi entry."
        )
        if result.get("error"):
            st.error(result["error"])
        return

    st.markdown("### 📋 Trading Plan")
    st.markdown(result.get("trading_plan", "Tidak ada detail tersedia."))

    if result.get("from_cache"):
        st.info("ℹ️ Hasil ini diambil dari cache (analisa serupa baru saja dijalankan).")


# ------------------------------------------------------------------
# State management: polling via rerun, BUKAN blocking loop.
# ------------------------------------------------------------------
if "active_task_id" not in st.session_state:
    st.session_state.active_task_id = None
if "poll_count" not in st.session_state:
    st.session_state.poll_count = 0
if "send_telegram_flag" not in st.session_state:
    st.session_state.send_telegram_flag = False

if submitted:
    if "/" not in symbol:
        st.error("Format symbol harus 'BASE/QUOTE', contoh: XAU/USD")
    else:
        task_id, error = submit_analysis(symbol.upper(), interval_htf, interval_ltf)
        if error:
            st.error(error)
        else:
            st.session_state.active_task_id = task_id
            st.session_state.poll_count = 0
            st.session_state.send_telegram_flag = send_telegram
            st.rerun()

if st.session_state.active_task_id:
    task_id = st.session_state.active_task_id
    st.info(f"Task ID: `{task_id}`")

    try:
        response = requests.get(f"{API_BASE_URL}/analyze/{task_id}", timeout=10)
        response.raise_for_status()
        data = response.json()
        status = data["status"]

        st.session_state.poll_count += 1
        st.progress(min(st.session_state.poll_count / MAX_POLL_ATTEMPTS, 1.0))
        st.text(f"Status: {status} (percobaan ke-{st.session_state.poll_count})")

        if status == "SUCCESS":
            render_trading_plan(data["result"])

            if st.session_state.send_telegram_flag:
                try:
                    tg_response = requests.post(
                        f"{API_BASE_URL}/notify/telegram",
                        json={"task_id": task_id},
                        timeout=10,
                    )
                    if tg_response.ok:
                        st.success("✅ Hasil terkirim ke Telegram.")
                    else:
                        st.warning("Gagal mengirim ke Telegram, cek konfigurasi bot.")
                except requests.exceptions.RequestException as exc:
                    st.warning(f"Gagal mengirim ke Telegram: {exc}")

            st.session_state.active_task_id = None

        elif status == "FAILURE":
            st.error("Analisa gagal diproses di backend.")
            st.session_state.active_task_id = None

        elif st.session_state.poll_count >= MAX_POLL_ATTEMPTS:
            st.error("Timeout menunggu hasil analisa (>5 menit).")
            st.session_state.active_task_id = None

        else:
            time.sleep(POLL_INTERVAL_SECONDS)
            st.rerun()

    except requests.exceptions.RequestException as exc:
        st.error(f"Gagal polling status: {exc}")
        st.session_state.active_task_id = None

st.divider()
st.caption(
    "⚠️ Disclaimer: Output ini adalah hasil analisa otomatis berbasis dokumen "
    "strategi yang diunggah, bukan nasihat keuangan. Keputusan trading "
    "sepenuhnya tanggung jawab pengguna."
)