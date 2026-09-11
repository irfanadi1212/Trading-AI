"""
app/core/config.py
=====================
Sumber tunggal konfigurasi aplikasi, dibaca dari environment variable (.env).
Menggunakan pydantic-settings agar validasi tipe & required field otomatis --
jika ada kredensial penting yang belum diisi, aplikasi akan gagal start
dengan pesan jelas alih-alih error samar di tengah eksekusi.
"""

import os
from functools import lru_cache

from dotenv import load_dotenv

load_dotenv()  # Muat isi .env ke environment variable proses ini


class Settings:
    # --- Market Data Provider ---
    TWELVEDATA_API_KEY: str = os.getenv("TWELVEDATA_API_KEY", "")

    # --- LLM Provider (CrewAI) ---
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
    EXPLABS_API_KEY: str = os.getenv("EXPLABS_API_KEY", "")
    GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")
    
    # --- Redis (Celery broker & backend) ---
    REDIS_URL: str = os.getenv("REDIS_URL", "redis://localhost:6379/0")

    # --- ChromaDB (Vector Store) ---
    CHROMA_PERSIST_DIR: str = os.getenv("CHROMA_PERSIST_DIR", "./chroma_strategy_db")
    CHROMA_COLLECTION_NAME: str = os.getenv("CHROMA_COLLECTION_NAME", "alchemist_strategy")

    # --- Telegram Bot ---
    TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    TELEGRAM_CHAT_ID: str = os.getenv("TELEGRAM_CHAT_ID", "")

    # --- Frontend ---
    API_BASE_URL: str = os.getenv("API_BASE_URL", "http://localhost:8000")

    def validate_required_for_phase(self, phase: str) -> list[str]:
        """
        Cek kredensial yang wajib ada untuk fase eksekusi tertentu.
        Mengembalikan list nama variable yang MASIH KOSONG (belum diisi).
        Dipakai tiap script sebelum mulai kerja, supaya error kredensial
        kosong terdeteksi di awal -- bukan gagal di tengah proses berat.
        """
        required_map = {
            "ingestion": [],  # Fase ingestion PDF tidak butuh API key eksternal
            "market_data": ["TWELVEDATA_API_KEY"],
            "crew_agents": ["GEMINI_API_KEY", "TWELVEDATA_API_KEY"],
            "telegram": ["TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"],
        }

        required_vars = required_map.get(phase, [])
        missing = [var for var in required_vars if not getattr(self, var, "")]
        return missing


@lru_cache()
def get_settings() -> Settings:
    """
    Cached singleton -- environment variable hanya dibaca sekali per proses,
    bukan berulang kali setiap file lain mengimpor config ini.
    """
    return Settings()