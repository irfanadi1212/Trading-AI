"""
main.py
==========
Entry point FastAPI. Menyatukan router dari app/api/routes.py,
plus rate limiting dasar dan CORS untuk akses dari Streamlit.
"""

import time
import logging
from collections import defaultdict

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.routes import router

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("main")

app = FastAPI(
    title="Alchemist Strategy Analysis API",
    description="AI Agentic market analysis berdasarkan strategi Alchemist (RAG-grounded).",
    version="1.0.0",
)

# CORS -- izinkan Streamlit (biasanya beda port) mengakses API ini
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # untuk produksi nanti, ganti ke domain spesifik
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)


# ------------------------------------------------------------------
# Rate limiting sederhana in-memory (per-IP)
# ------------------------------------------------------------------
RATE_LIMIT_WINDOW_SECONDS = 60
RATE_LIMIT_MAX_REQUESTS = 10
_request_log: dict = defaultdict(list)


def _check_rate_limit(client_ip: str) -> bool:
    now = time.time()
    window_start = now - RATE_LIMIT_WINDOW_SECONDS
    _request_log[client_ip] = [t for t in _request_log[client_ip] if t > window_start]
    if len(_request_log[client_ip]) >= RATE_LIMIT_MAX_REQUESTS:
        return False
    _request_log[client_ip].append(now)
    return True


@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    client_ip = request.client.host if request.client else "unknown"
    # Hanya rate-limit POST /analyze (submit baru = biaya LLM),
    # BUKAN GET /analyze/{task_id} (polling status = murah, wajar sering dipanggil)
    is_submit_endpoint = request.url.path == "/analyze" and request.method == "POST"
    if is_submit_endpoint and not _check_rate_limit(client_ip):
        return JSONResponse(
            status_code=429,
            content={"detail": f"Rate limit terlampaui. Maks {RATE_LIMIT_MAX_REQUESTS} request/menit."},
        )
    return await call_next(request)