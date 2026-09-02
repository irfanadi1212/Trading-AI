"""
app/services/rag_engine.py
=============================
Engine RAG: ingestion 4 dokumen PDF strategi ke ChromaDB (semantic
chunking per-konsep, dengan OCR fallback untuk halaman berisi diagram),
dan fungsi query yang dipakai oleh crew_agents.py sebagai RAG tool.

PRINSIP: file ini SATU-SATUNYA titik akses ke ChromaDB di seluruh
aplikasi. Modul lain (crew_agents.py) tidak boleh membuka koneksi
Chroma sendiri -- selalu lewat fungsi di sini.
"""

import os
import re
import uuid
import logging
from dataclasses import dataclass, field
from typing import List, Dict, Optional

import chromadb
from chromadb.utils import embedding_functions
import pdfplumber
import pytesseract
from pdf2image import convert_from_path

from app.core.config import get_settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("rag_engine")

settings = get_settings()

# Ambang batas: jika teks hasil pdfplumber di suatu halaman lebih pendek
# dari ini, halaman dianggap "gambar/diagram" dan di-OCR.
MIN_TEXT_LENGTH_BEFORE_OCR = 30


# ------------------------------------------------------------------
# Struktur data satu chunk konsep + metadata untuk retrieval terarah.
# ------------------------------------------------------------------
@dataclass
class ConceptChunk:
    chunk_id: str
    text: str
    source_doc: str
    category: str  # "time" | "structure" | "liquidity" | "entry" | "framework"
    related_terms: List[str] = field(default_factory=list)


# ------------------------------------------------------------------
# Kamus istilah -> kategori (4 dokumen: Mechanical Trader,
# Alchemist Killzone, E-Book Alchemist, SMC Musashi).
# ------------------------------------------------------------------
TERM_CATEGORY_MAP: Dict[str, str] = {
    "killzone": "time", "ldnkz": "time", "nykz": "time",
    "asia session": "time", "time colombo": "time", "time jakarta": "time",
    "bos": "structure", "choch": "structure", "quasimodo": "structure",
    "qm": "structure", "rbs": "structure", "sbr": "structure",
    "classic v": "structure", "classic a": "structure", "trendline": "structure",
    "zigzag": "structure", "candle kejepit": "structure", "order block": "structure",
    "breaker block": "structure", "mitigation block": "structure",
    "bsl": "liquidity", "ssl": "liquidity", "erl": "liquidity", "irl": "liquidity",
    "eqh": "liquidity", "eql": "liquidity", "pwh": "liquidity", "pwl": "liquidity",
    "pdh": "liquidity", "pdl": "liquidity", "dealing range": "liquidity",
    "premium": "liquidity", "discount": "liquidity",
    "msnr": "entry", "ocl": "entry", "fvg": "entry", "ce": "entry",
    "0.5 wick": "entry", "confluence": "entry", "qmx": "entry",
    "alchemist": "framework", "ict": "framework", "smc": "framework", "lit": "framework",
}


def detect_category(text: str) -> str:
    text_lower = text.lower()
    scores: Dict[str, int] = {}
    for term, category in TERM_CATEGORY_MAP.items():
        if term in text_lower:
            scores[category] = scores.get(category, 0) + 1
    return max(scores, key=scores.get) if scores else "structure"


def detect_related_terms(text: str) -> List[str]:
    text_lower = text.lower()
    return sorted({term for term in TERM_CATEGORY_MAP if term in text_lower})


# ------------------------------------------------------------------
# Ekstraksi teks per halaman, dengan OCR fallback untuk halaman yang
# isinya dominan gambar/diagram.
# ------------------------------------------------------------------
def _ocr_single_page(pdf_path: str, page_num: int) -> str:
    """
    Render satu halaman PDF jadi gambar, lalu jalankan Tesseract OCR.
    Error di sini tidak boleh menghentikan seluruh pipeline ingestion --
    cukup log dan kembalikan string kosong untuk halaman tersebut.
    """
    try:
        images = convert_from_path(
            pdf_path, first_page=page_num, last_page=page_num, dpi=200
        )
        if not images:
            return ""
        text = pytesseract.image_to_string(images[0], lang="ind+eng")
        return text.strip()
    except Exception as exc:
        logger.warning(f"OCR gagal untuk halaman {page_num} dari '{pdf_path}': {exc}")
        return ""


def extract_pages(pdf_path: str) -> List[str]:
    pages_text: List[str] = []
    try:
        with pdfplumber.open(pdf_path) as pdf:
            for page_num, page in enumerate(pdf.pages, start=1):
                raw = (page.extract_text() or "").strip()

                if len(raw) >= MIN_TEXT_LENGTH_BEFORE_OCR:
                    pages_text.append(raw)
                else:
                    ocr_text = _ocr_single_page(pdf_path, page_num)
                    if ocr_text:
                        logger.info(
                            f"  Halaman {page_num} di-OCR (teks asli {len(raw)} char) "
                            f"-> {len(ocr_text)} char hasil OCR"
                        )
                    pages_text.append(ocr_text)
    except Exception as exc:
        logger.error(f"Gagal membaca PDF '{pdf_path}': {exc}")
    return pages_text


HEADING_PATTERN = re.compile(r"^([A-Z][A-Z\s&/\-()]{3,40}|[0-9]+\.\s?[A-Za-z].{2,40})$")


def split_into_concept_blocks(pages: List[str]) -> List[str]:
    blocks: List[str] = []
    current_block_lines: List[str] = []
    for page_text in pages:
        if not page_text:
            continue
        for line in page_text.split("\n"):
            stripped = line.strip()
            if HEADING_PATTERN.match(stripped) and current_block_lines:
                blocks.append("\n".join(current_block_lines).strip())
                current_block_lines = [stripped]
            else:
                current_block_lines.append(line)
        if current_block_lines:
            blocks.append("\n".join(current_block_lines).strip())
            current_block_lines = []
    return [b for b in blocks if len(b) > 20]


def add_overlap(blocks: List[str], overlap_sentences: int = 2) -> List[str]:
    overlapped: List[str] = []
    for i, block in enumerate(blocks):
        prefix = ""
        if i > 0:
            prev_sentences = re.split(r"(?<=[.!?])\s+", blocks[i - 1])
            prefix = " ".join(prev_sentences[-overlap_sentences:]) + "\n---\n"
        overlapped.append(prefix + block)
    return overlapped


def process_pdf_to_chunks(pdf_path: str) -> List[ConceptChunk]:
    doc_name = os.path.basename(pdf_path)
    logger.info(f"Memproses dokumen: {doc_name}")
    pages = extract_pages(pdf_path)
    if not pages:
        logger.warning(f"Tidak ada teks yang berhasil diekstrak dari {doc_name}")
        return []
    blocks = add_overlap(split_into_concept_blocks(pages))
    chunks = [
        ConceptChunk(
            chunk_id=str(uuid.uuid4()),
            text=block,
            source_doc=doc_name,
            category=detect_category(block),
            related_terms=detect_related_terms(block),
        )
        for block in blocks
    ]
    logger.info(f"  -> {len(chunks)} concept chunk dihasilkan dari {doc_name}")
    return chunks


# ------------------------------------------------------------------
# Koneksi ChromaDB terpusat -- semua fungsi lain di aplikasi lewat sini.
# ------------------------------------------------------------------
def get_collection():
    client = chromadb.PersistentClient(path=settings.CHROMA_PERSIST_DIR)
    embed_fn = embedding_functions.SentenceTransformerEmbeddingFunction(
        model_name="all-MiniLM-L6-v2"
    )
    return client.get_or_create_collection(
        name=settings.CHROMA_COLLECTION_NAME,
        embedding_function=embed_fn,
    )


def reset_collection() -> None:
    """Hapus koleksi lama sebelum re-ingest, mencegah duplikasi chunk."""
    try:
        client = chromadb.PersistentClient(path=settings.CHROMA_PERSIST_DIR)
        client.delete_collection(name=settings.CHROMA_COLLECTION_NAME)
        logger.info(f"Koleksi lama '{settings.CHROMA_COLLECTION_NAME}' dihapus.")
    except Exception:
        pass  # Aman diabaikan jika koleksi belum pernah ada


def ingest_documents(pdf_paths: List[str]) -> None:
    all_chunks: List[ConceptChunk] = []
    for path in pdf_paths:
        if not os.path.exists(path):
            logger.error(f"File tidak ditemukan, dilewati: {path}")
            continue
        all_chunks.extend(process_pdf_to_chunks(path))

    if not all_chunks:
        logger.warning("Tidak ada chunk untuk disimpan. Ingestion dibatalkan.")
        return

    category_counts: Dict[str, int] = {}
    for c in all_chunks:
        category_counts[c.category] = category_counts.get(c.category, 0) + 1
    logger.info(f"Distribusi kategori chunk: {category_counts}")

    try:
        collection = get_collection()
        collection.add(
            ids=[c.chunk_id for c in all_chunks],
            documents=[c.text for c in all_chunks],
            metadatas=[
                {
                    "source_doc": c.source_doc,
                    "category": c.category,
                    "related_terms": ", ".join(c.related_terms),
                }
                for c in all_chunks
            ],
        )
        logger.info(
            f"Berhasil menyimpan {len(all_chunks)} chunk ke koleksi "
            f"'{settings.CHROMA_COLLECTION_NAME}'."
        )
    except Exception as exc:
        logger.error(f"Gagal menyimpan ke vector store: {exc}")
        raise


def query_strategy(query: str, category_filter: Optional[str] = None, top_k: int = 4) -> str:
    """
    Dipanggil oleh crew_agents.py sebagai RAG tool. Mengembalikan teks
    hasil retrieval, atau pesan eksplisit jika tidak ditemukan.
    """
    try:
        collection = get_collection()
        where_clause = {"category": category_filter} if category_filter else None
        results = collection.query(query_texts=[query], n_results=top_k, where=where_clause)

        if not results["documents"] or not results["documents"][0]:
            return "TIDAK DITEMUKAN referensi untuk query ini di dokumen strategi."

        formatted = []
        for doc, meta in zip(results["documents"][0], results["metadatas"][0]):
            formatted.append(f"[Sumber: {meta.get('source_doc')} | Kategori: {meta.get('category')}]\n{doc}")
        return "\n\n---\n\n".join(formatted)

    except Exception as exc:
        logger.error(f"query_strategy error: {exc}")
        return f"ERROR: Gagal mengakses strategy database ({exc})."


if __name__ == "__main__":
    PDF_DIR = "data/pdfs"
    pdf_files = [
        os.path.join(PDF_DIR, "ALCHEMIST_2_00_AbayFX.pdf"),
        os.path.join(PDF_DIR, "ALCHEMIST_KILZONE_BY_MECHANICAL_TRADER.pdf"),
        os.path.join(PDF_DIR, "E-Book_Alchemist.pdf"),
        os.path.join(PDF_DIR, "SMC_Smart_Money_Concept.pdf"),
    ]
    reset_collection()
    ingest_documents(pdf_files)