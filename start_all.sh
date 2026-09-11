#!/bin/bash
# start_all.sh -- Nyalakan semua service sekaligus (Redis, FastAPI, Celery, Streamlit, Telegram Bot)

echo "=== 1. Redis ==="
if [ "$(docker ps -q -f name=redis-local)" ]; then
    echo "Redis sudah jalan."
else
    if [ "$(docker ps -aq -f name=redis-local)" ]; then
        docker start redis-local
    else
        docker run -d --name redis-local -p 6379:6379 redis:7-alpine
    fi
    sleep 2
fi
docker exec redis-local redis-cli ping

echo "=== 2. FastAPI ==="
nohup uvicorn main:app --host 0.0.0.0 --port 8000 --reload > logs_fastapi.log 2>&1 &
sleep 3

echo "=== 3. Celery Worker ==="
nohup celery -A app.workers.celery_app worker --loglevel=info --queues=analysis_heavy,notifications_light --concurrency=2 > logs_celery.log 2>&1 &
sleep 3

echo "=== 4. Streamlit ==="
nohup streamlit run frontend/dashboard.py --server.port 8501 > logs_streamlit.log 2>&1 &
sleep 3

echo "=== 5. Telegram Bot Listener ==="
nohup python3 -m app.services.telegram_bot_listener > logs_telegrambot.log 2>&1 &
sleep 2

echo ""
echo "=== SEMUA SERVICE JALAN DI BACKGROUND ==="
echo "Cek status API     : curl http://localhost:8000/health"
echo "Cek log FastAPI    : tail -f logs_fastapi.log"
echo "Cek log Celery     : tail -f logs_celery.log"
echo "Cek log Streamlit  : tail -f logs_streamlit.log"
echo "Cek log Bot Telegram: tail -f logs_telegrambot.log"
echo "Buka dashboard     : lihat tab 'Ports' untuk port 8501"
echo "Bot Telegram       : buka chat bot Anda, ketik /start"
