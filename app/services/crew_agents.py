"""
app/services/crew_agents.py
==============================
5 Agen CrewAI: HTF Bias -> Liquidity -> Session/Timing -> Entry Precision (MSNR)
-> Trade Plan Synthesis. LLM: OpenAI GPT-5.6 Terra.
"""

import logging
from typing import Type

from crewai import Agent, Task, Crew, Process, LLM
from crewai.tools import BaseTool
from pydantic import BaseModel, Field

from app.core.config import get_settings
from app.services.rag_engine import query_strategy
from app.services.market_data import fetch_ohlcv

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("crew_agents")

settings = get_settings()

gemini_llm = LLM(
    model="openai/gpt-5.6-luna",
    api_key=settings.EXPLABS_API_KEY,
    base_url="https://api.experientiallabs.ai/v1",
)


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
    max_iter=3,
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
    max_iter=3,
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
    max_iter=3,
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
    max_iter=3,
    verbose=True,
    allow_delegation=False,
)

synthesizer_agent = Agent(
    role="Trade Plan Synthesizer",
    goal=(
        "Menggabungkan output 4 agen sebelumnya menjadi SATU trading plan "
        "final, dengan penilaian probabilitas berjenjang (High/Medium/Low) "
        "sebelum menyatakan tidak ada setup sama sekali."
    ),
    backstory=(
        "Anda adalah quality gate terakhir. Evaluasi setup dengan LOGIKA "
        "BERJENJANG berikut, cek dari tingkat tertinggi ke terendah:\n\n"
        "1. HIGH PROBABILITY: bias 4H jelas (BOS/CHoCH terkonfirmasi) DAN "
        "entry POI presisi (CE/OB/FVG) terkonfirmasi struktur LTF DAN "
        "liquidity target jelas.\n"
        "2. MEDIUM PROBABILITY: bias 4H jelas DAN liquidity target jelas, "
        "TAPI entry POI/struktur LTF belum sepenuhnya terkonfirmasi (masih "
        "ada indikasi arah, cukup kuat untuk entry dengan risiko lebih "
        "besar).\n"
        "3. LOW PROBABILITY: bias 4H ada indikasi arah (meski belum BOS "
        "bersih) DAN liquidity target teridentifikasi, meski entry presisi "
        "tidak tersedia -- entry pakai level observasi kasar.\n\n"
        "Jika HIGH tidak terpenuhi, cek MEDIUM. Jika MEDIUM tidak "
        "terpenuhi, cek LOW. HANYA jika ketiganya tidak terpenuhi (bias "
        "4H benar-benar 'undetermined' tanpa indikasi arah apapun), "
        "keluarkan pesan NO SETUP.\n\n"
        "FORMAT OUTPUT UNTUK SETUP VALID (High/Medium/Low) -- ikuti persis:\n\n"
        "ALCHEMIST SIGNAL\n\n"
        "{SYMBOL} · {arah: buy/sell}\n"
        "{harga entry, angka saja}\n\n"
        "stop {harga SL}\n"
        "target {harga TP}\n\n"
        "risk : reward {rasio}\n"
        "{gauge visual pakai karakter blok: total 15 karakter, isi bagian "
        "risk pakai '█' sisanya '░' sesuai proporsi rasio}\n\n"
        "probability: {HIGH/MEDIUM/LOW}\n\n"
        "reasoning\n"
        "(MAKSIMAL 3 poin, masing-masing 3-5 kata saja, tanpa emoji, "
        "bahasa teknis padat)\n"
        "(baris terakhir: nama file dokumen sumber, dipisah titik tengah "
        "'·', dicetak miring)\n\n"
        "FORMAT OUTPUT UNTUK NO SETUP (hanya jika ketiga tingkat gagal):\n"
        "Keluarkan HANYA teks ini, tanpa section lain apapun:\n"
        "'⚠️ NO SETUP NO ENTRY'\n\n"
        "ATURAN KETAT:\n"
        "- Setiap poin reasoning maksimal 5 kata\n"
        "- WAJIB sebutkan nama file dokumen sumber di baris terakhir "
        "(kecuali kondisi NO SETUP NO ENTRY)\n"
        "- Jangan menjelaskan definisi istilah, cukup sebut istilahnya\n"
        "- Total keseluruhan pesan maksimal 15 baris\n"
        "- JANGAN mengulang placeholder yang sama berkali-kali -- setiap "
        "kondisi output HANYA muncul satu kali sesuai format di atas"
    ),
    tools=[],
    llm=gemini_llm,
    max_iter=3,
    verbose=True,
    allow_delegation=False,
)


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
    crew = build_crew(symbol="XAU/USD", interval_htf="4h", interval_ltf="15min")
    result = crew.kickoff()
    print("\n\n=== HASIL TRADING PLAN ===\n")
    print(result)