"""
app/services/crew_agents.py
==============================
5 Agen CrewAI yang menganalisa market mengikuti alur logis:
HTF Bias -> Liquidity Mapping -> Session/Timing -> Entry Precision (MSNR)
-> Trade Plan Synthesis

ATURAN NON-NEGOTIABLE (ditegaskan di tiap system prompt):
1. Setiap klaim WAJIB didukung hasil retrieval dari strategy_lookup_tool,
   bukan pengetahuan umum LLM.
2. Killzone = confidence booster, BUKAN syarat wajib entry. Sinyal valid
   di luar killzone tetap boleh dieksekusi (label: standard confidence).
3. Jika bukti di dokumen tidak cukup, agen WAJIB menyatakan
   "insufficient evidence" -- dilarang menebak/berhalusinasi.
"""

import logging
from typing import Type

from crewai import LLM
from crewai import Agent, Task, Crew, Process
from crewai.tools import BaseTool
from pydantic import BaseModel, Field

from app.core.config import get_settings
from app.services.rag_engine import query_strategy
from app.services.market_data import fetch_ohlcv

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("crew_agents")

settings = get_settings()


# ------------------------------------------------------------------
# LLM Provider: Gemini (via LiteLLM, dipakai CrewAI di balik layar)
# ------------------------------------------------------------------
gemini_llm = LLM(
  model="gemini/gemini-3.5-flash-lite",  # model hemat biaya untuk testing
  api_key=settings.GEMINI_API_KEY,
  temperature=0.2,  # rendah -- kita mau presisi merujuk dokumen, bukan kreatif
)


# ====================================================================
# TOOL 1: RAG Lookup Tool -- wrapper CrewAI di atas query_strategy()
# ====================================================================
class StrategyLookupInput(BaseModel):
    query: str = Field(..., description="Istilah/pertanyaan strategi, contoh: 'RBS entry rules' atau 'killzone times Jakarta'")
    category_filter: str = Field(
        default="",
        description="Opsional: time | structure | liquidity | entry | framework. Kosongkan untuk cari semua kategori.",
    )


class StrategyLookupTool(BaseTool):
    name: str = "strategy_lookup_tool"
    description: str = (
        "WAJIB digunakan sebelum membuat klaim apapun tentang aturan trading. "
        "Mencari definisi/aturan resmi dari 4 dokumen strategi Alchemist yang "
        "tersimpan di vector database. Jangan pernah menjawab dari ingatan "
        "umum -- selalu verifikasi lewat tool ini terlebih dahulu."
    )
    args_schema: Type[BaseModel] = StrategyLookupInput

    def _run(self, query: str, category_filter: str = "") -> str:
        category = category_filter if category_filter else None
        return query_strategy(query=query, category_filter=category)


# ====================================================================
# TOOL 2: Market Data Tool -- wrapper CrewAI di atas fetch_ohlcv()
# ====================================================================
class MarketDataInput(BaseModel):
    symbol: str = Field(..., description="Contoh: 'XAU/USD', 'EUR/USD', 'BTC/USD'")
    interval: str = Field(..., description="Contoh: '15min', '1h', '4h', '1day'")
    outputsize: int = Field(default=200, description="Jumlah candle yang diambil")


class MarketDataTool(BaseTool):
    name: str = "market_data_tool"
    description: str = (
        "Mengambil data OHLCV live/historis untuk instrumen tertentu. "
        "Gunakan ini untuk mendapatkan candle yang akan dianalisa "
        "struktur/liquidity/entry-nya."
    )
    args_schema: Type[BaseModel] = MarketDataInput

    def _run(self, symbol: str, interval: str, outputsize: int = 200) -> str:
        return fetch_ohlcv(symbol=symbol, interval=interval, outputsize=outputsize)


strategy_tool = StrategyLookupTool()
market_tool = MarketDataTool()


# ====================================================================
# AGENT DEFINITIONS
# ====================================================================
htf_bias_agent = Agent(
    role="HTF Structure & Bias Analyst",
    goal=(
        "Menentukan Dealing Range (DRH/DRL), zona premium/discount, dan arah "
        "bias market (bullish/bearish/sideways) berdasarkan struktur BOS/CHoCH "
        "SEMATA-MATA merujuk pada aturan di dokumen strategi."
    ),
    backstory=(
        "Anda adalah analis struktur harga timeframe tinggi. Anda HANYA boleh "
        "menggunakan definisi BOS (break of structure) dan CHoCH (change of "
        "character) persis seperti dijelaskan di dokumen SMC Musashi dan "
        "E-Book Alchemist. Anda WAJIB memanggil strategy_lookup_tool dengan "
        "category_filter='structure' sebelum menyimpulkan apapun. Jika data "
        "candle tidak cukup jelas menunjukkan BOS/CHoCH, nyatakan bias "
        "'undetermined' -- jangan memaksakan kesimpulan."
    ),
    tools=[strategy_tool, market_tool],
    llm=gemini_llm,
    verbose=True,
    allow_delegation=False,
)

liquidity_agent = Agent(
    role="Liquidity Mapping Specialist",
    goal=(
        "Memetakan pool likuiditas relevan (ERL vs IRL, BSL/SSL, EQH/EQL, "
        "PDH/PDL/PWH/PWL) sebagai target draw pergerakan harga, sesuai "
        "definisi persis dari dokumen."
    ),
    backstory=(
        "Anda adalah spesialis likuiditas. Definisi ERL vs IRL HARUS diambil "
        "dari strategy_lookup_tool dengan category_filter='liquidity'. Anda "
        "tidak pernah menyebut istilah likuiditas generik dari luar dokumen "
        "-- gunakan istilah persis: BSL, SSL, ERL, IRL, EQH, EQL, PWH, PWL, "
        "PDH, PDL."
    ),
    tools=[strategy_tool, market_tool],
    llm=gemini_llm,
    verbose=True,
    allow_delegation=False,
)

timing_agent = Agent(
    role="Session & Timing Analyst",
    goal=(
        "Mengevaluasi sesi Asia (strong high/low atau liquidity pool) dan "
        "memberi skor confidence berdasarkan posisi waktu relatif terhadap "
        "jendela killzone (LNDKZ 13:00-14:00 / NYKZ 18:00-19:00 Jakarta)."
    ),
    backstory=(
        "PENTING: killzone BUKAN syarat wajib entry, melainkan confidence "
        "booster. Jika sinyal valid ditemukan di luar jam killzone, tetap "
        "tandai sebagai 'signal strength: standard' -- BUKAN ditolak. Jika "
        "di dalam killzone, tandai 'signal strength: high confluence'. "
        "Gunakan strategy_lookup_tool dengan category_filter='time' untuk "
        "verifikasi jam killzone dan aturan sesi Asia sesuai dokumen "
        "Mechanical Trader."
    ),
    tools=[strategy_tool, market_tool],
    llm=gemini_llm,
    verbose=True,
    allow_delegation=False,
)

entry_precision_agent = Agent(
    role="Entry Precision Agent (MSNR)",
    goal=(
        "Mencari titik entry presisi di LTF menggunakan CE (50% FVG), 0.5 "
        "wick, Order Block, FVG, atau candle kejepit, di titik confluence "
        "(SNR + Trendline atau QMX), lalu menunggu konfirmasi struktur LTF "
        "(BMS/CHoCH) sebelum entry."
    ),
    backstory=(
        "Setiap level entry yang Anda ajukan HARUS bisa ditelusuri balik ke "
        "definisi persis di strategy_lookup_tool (category_filter='entry' "
        "atau 'structure'). Jangan mengarang level entry dari intuisi "
        "chart pattern umum -- hanya berdasarkan MSNR (CE/0.5 wick/OB/FVG) "
        "dan candle kejepit seperti didefinisikan di dokumen."
    ),
    tools=[strategy_tool, market_tool],
    llm=gemini_llm,
    verbose=True,
    allow_delegation=False,
)

synthesizer_agent = Agent(
    role="Trade Plan Synthesizer",
    goal=(
        "Menggabungkan output 4 agen sebelumnya menjadi satu trading plan "
        "final: bias, target likuiditas, level entry, stop loss, take "
        "profit, dan skor confidence keseluruhan."
    ),
    backstory=(
        "Anda adalah quality gate terakhir. Jika salah satu agen sebelumnya "
        "melaporkan 'undetermined' atau 'insufficient evidence' pada "
        "komponen krusial, Anda WAJIB menyimpulkan 'NO VALID SETUP' -- "
        "jangan memaksakan rencana trading dari data tidak lengkap. Setiap "
        "trading plan HARUS mencantumkan sumber referensi dokumen untuk "
        "tiap komponen keputusan."
    ),
    tools=[],
    llm=gemini_llm,
    verbose=True,
    allow_delegation=False,
)


# ====================================================================
# CREW BUILDER
# ====================================================================
def build_crew(symbol: str, interval_htf: str, interval_ltf: str) -> Crew:
    task_bias = Task(
        description=(
            f"Analisa data {symbol} pada timeframe {interval_htf} (HTF). "
            "Tentukan Dealing Range, zona premium/discount, dan bias arah "
            "berdasarkan BOS/CHoCH."
        ),
        expected_output="Bias arah + level DRH/DRL + zona premium/discount, dengan referensi dokumen.",
        agent=htf_bias_agent,
    )

    task_liquidity = Task(
        description=(
            f"Berdasarkan bias dari task sebelumnya, petakan pool likuiditas "
            f"relevan untuk {symbol} (ERL/IRL, BSL/SSL, EQH/EQL, PDH/PDL, PWH/PWL)."
        ),
        expected_output="Daftar level likuiditas dengan kategori (internal/external) dan target draw.",
        agent=liquidity_agent,
        context=[task_bias],
    )

    task_timing = Task(
        description=(
            f"Evaluasi sesi Asia terbaru untuk {symbol} dan tentukan skor "
            "confidence berdasarkan posisi waktu saat ini relatif killzone."
        ),
        expected_output="Status sesi Asia + skor confidence timing.",
        agent=timing_agent,
        context=[task_bias],
    )

    task_entry = Task(
        description=(
            f"Turun ke LTF ({interval_ltf}) untuk {symbol}. Cari POI presisi "
            "(CE/0.5 wick/OB/FVG/candle kejepit) di titik confluence, tunggu "
            "konfirmasi struktur LTF sebelum mengajukan level entry."
        ),
        expected_output="Level entry presisi + jenis POI + status konfirmasi struktur LTF.",
        agent=entry_precision_agent,
        context=[task_bias, task_liquidity],
    )

    task_synthesis = Task(
        description=(
            "Gabungkan seluruh output menjadi satu trading plan final: "
            "arah, entry, SL, TP (berdasarkan target likuiditas), dan skor "
            "confidence gabungan. Jika data tidak cukup, nyatakan NO VALID SETUP."
        ),
        expected_output=(
            "Trading plan terstruktur (arah/entry/SL/TP/confidence score/"
            "sumber referensi) ATAU pernyataan 'NO VALID SETUP' dengan alasan."
        ),
        agent=synthesizer_agent,
        context=[task_bias, task_liquidity, task_timing, task_entry],
    )

    return Crew(
        agents=[htf_bias_agent, liquidity_agent, timing_agent, entry_precision_agent, synthesizer_agent],
        tasks=[task_bias, task_liquidity, task_timing, task_entry, task_synthesis],
        process=Process.sequential,
        verbose=True,
    )


if __name__ == "__main__":
    # Test manual: jalankan satu analisa lengkap
    crew = build_crew(symbol="XAU/USD", interval_htf="4h", interval_ltf="15min")
    result = crew.kickoff()
    print("\n\n=== HASIL TRADING PLAN ===\n")
    print(result)