"""
app/services/market_data.py
==============================
Integrasi ke Twelve Data API untuk menarik data OHLCV (Open, High,
Low, Close, Volume). Dipakai oleh crew_agents.py sebagai tool yang
bisa dipanggil agen untuk mengambil data candle live/historis.

Error handling: API market rentan gagal (rate limit, network, symbol
tidak valid) -- semua kegagalan dikembalikan sebagai string pesan
error yang jelas, BUKAN exception yang bisa menghentikan agen di
tengah proses analisa.
"""

import logging
from typing import Optional

import requests

from app.core.config import get_settings

logger = logging.getLogger("market_data")
settings = get_settings()

BASE_URL = "https://api.twelvedata.com/time_series"
REQUEST_TIMEOUT_SECONDS = 15
MAX_CANDLES_IN_SUMMARY = 20  # batasi output agar tidak boros context window LLM


def fetch_ohlcv(symbol: str, interval: str, outputsize: int = 200) -> str:
    """
    Ambil data candle dari Twelve Data.
    Mengembalikan ringkasan teks (bukan raw JSON) supaya langsung bisa
    dibaca agen tanpa parsing tambahan.
    """
    if not settings.TWELVEDATA_API_KEY:
        return "ERROR: TWELVEDATA_API_KEY belum diisi di .env. Tidak bisa mengambil data market."

    params = {
        "symbol": symbol,
        "interval": interval,
        "outputsize": outputsize,
        "apikey": settings.TWELVEDATA_API_KEY,
    }

    try:
        response = requests.get(BASE_URL, params=params, timeout=REQUEST_TIMEOUT_SECONDS)
        response.raise_for_status()
        data = response.json()

        if data.get("status") == "error":
            error_msg = data.get("message", "Unknown error dari Twelve Data")
            logger.error(f"Twelve Data API error untuk {symbol}: {error_msg}")
            return f"ERROR dari market data provider: {error_msg}"

        values = data.get("values", [])
        if not values:
            return f"Tidak ada data candle ditemukan untuk {symbol} @ {interval}."

        latest = values[:MAX_CANDLES_IN_SUMMARY]
        summary = (
            f"Data {symbol} interval {interval} ({len(values)} candle tersedia, "
            f"menampilkan {len(latest)} candle terbaru):\n"
        )
        for v in latest:
            summary += (
                f"  {v['datetime']} | O:{v['open']} H:{v['high']} "
                f"L:{v['low']} C:{v['close']}\n"
            )
        return summary

    except requests.exceptions.Timeout:
        logger.error(f"Timeout saat mengambil data {symbol} dari Twelve Data.")
        return f"ERROR: Request ke market data API timeout (>{REQUEST_TIMEOUT_SECONDS} detik). Coba ulangi."

    except requests.exceptions.RequestException as exc:
        logger.error(f"Gagal menghubungi Twelve Data API untuk {symbol}: {exc}")
        return f"ERROR: Gagal menghubungi market data API ({exc})."

    except (KeyError, ValueError) as exc:
        # Response format tak terduga (API berubah struktur, dsb)
        logger.error(f"Format respons tak terduga dari Twelve Data untuk {symbol}: {exc}")
        return f"ERROR: Format data dari provider tidak sesuai ekspektasi ({exc})."


if __name__ == "__main__":
    # Quick manual test -- jalankan file ini langsung untuk cek koneksi API
    result = fetch_ohlcv(symbol="XAU/USD", interval="1h", outputsize=10)
    print(result)