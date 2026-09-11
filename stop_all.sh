#!/bin/bash
# stop_all.sh -- Matikan semua service (kecuali Redis, biar datanya tetap ada)

pkill -f "uvicorn main:app"
pkill -f "celery -A app.workers"
pkill -f "streamlit run"
pkill -f "app.services.telegram_bot_listener"

echo "Semua service (FastAPI, Celery, Streamlit, Telegram Bot) dimatikan."
echo "Redis dibiarkan tetap jalan. Kalau mau matikan juga: docker stop redis-local"
