"""
rag/document_store.py
Ingestion (build_index) and retrieval (search_documents) for Agent 6.6 RAG.

Embedding swap point: _embed() is the single function that knows HOW vectors
are produced.  To switch to Azure OpenAI embeddings (or any other provider),
change EMBEDDING_MODEL_NAME in config.py and replace the body of _embed() —
nothing else needs to change.
"""
import json
import os
from pathlib import Path

import faiss
import numpy as np
from pypdf import PdfReader
from sentence_transformers import SentenceTransformer

from rag.config import (
    CHUNKS_PATH,
    CHUNK_OVERLAP,
    CHUNK_SIZE,
    EMBEDDING_MODEL_NAME,
    INDEX_PATH,
    PDF_DIR,
    TOP_K,
)

# ---------------------------------------------------------------------------
# Embedding model — loaded ONCE at module level so every call reuses it.
# This is the swap point: replace SentenceTransformer with any other encoder
# (e.g. AzureOpenAIEmbeddings) by editing here and in _embed() below.
# ---------------------------------------------------------------------------
_model = SentenceTransformer(EMBEDDING_MODEL_NAME)


def _embed(texts: list[str]) -> np.ndarray:
    """Embed a list of strings -> float32 matrix (n_texts, dim).

    SWAP POINT: this is the one place that knows HOW embeddings are produced.
    To switch providers, replace this function body only.
    """
    return _model.encode(texts, show_progress_bar=False, convert_to_numpy=True).astype("float32")


# ---------------------------------------------------------------------------
# Chunker
# ---------------------------------------------------------------------------

def _chunk_text(text: str, size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """Split text into overlapping chunks, breaking at word boundaries."""
    text = text.strip()
    if not text:
        return []

    chunks = []
    start = 0
    while start < len(text):
        end = start + size
        if end >= len(text):
            chunks.append(text[start:].strip())
            break
        # Walk back to the nearest space so we don't cut mid-word.
        boundary = text.rfind(" ", start, end)
        if boundary == -1 or boundary <= start:
            boundary = end  # no space found; hard cut
        chunks.append(text[start:boundary].strip())
        start = boundary - overlap  # step forward with overlap
        if start < 0:
            start = 0
    return [c for c in chunks if c]


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------

def build_index() -> None:
    """Read every PDF in PDF_DIR, chunk, embed, and write FAISS index to disk."""
    pdf_dir = Path(PDF_DIR)
    pdfs = sorted(pdf_dir.glob("*.pdf"))
    if not pdfs:
        raise FileNotFoundError(f"No PDFs found in {PDF_DIR!r}")

    all_chunks: list[dict] = []

    for pdf_path in pdfs:
        fname = pdf_path.name
        # Infer doc_type from filename prefix
        doc_type = "SOP" if fname.upper().startswith("SOP") else "CASE"

        reader = PdfReader(str(pdf_path))
        for page_num, page in enumerate(reader.pages, start=1):
            raw = page.extract_text() or ""
            raw = raw.strip()
            if not raw:
                continue
            for chunk_text in _chunk_text(raw):
                all_chunks.append({
                    "source_file": fname,
                    "doc_type":    doc_type,
                    "page_number": page_num,
                    "text":        chunk_text,
                })

    if not all_chunks:
        raise ValueError("No text extracted from any PDF — check the files.")

    # Embed all chunk texts
    texts = [c["text"] for c in all_chunks]
    print(f"Embedding {len(texts)} chunks from {len(pdfs)} PDFs…")
    vectors = _embed(texts)  # shape (n_chunks, dim)

    # Build FAISS flat-L2 index
    dim = vectors.shape[1]
    index = faiss.IndexFlatL2(dim)
    faiss.normalize_L2(vectors)       # cosine similarity via normalised L2
    index = faiss.IndexFlatIP(dim)    # inner-product on normalised vecs = cosine
    index.add(vectors)

    # Persist
    os.makedirs(Path(INDEX_PATH).parent, exist_ok=True)
    faiss.write_index(index, INDEX_PATH)
    with open(CHUNKS_PATH, "w", encoding="utf-8") as f:
        json.dump(all_chunks, f, ensure_ascii=False, indent=2)

    print(
        f"Index built: {len(pdfs)} PDFs | {len(all_chunks)} chunks | "
        f"dim={dim} | saved to {INDEX_PATH!r} + {CHUNKS_PATH!r}"
    )


# ---------------------------------------------------------------------------
# Retrieval — lazy-loaded module-level cache
# ---------------------------------------------------------------------------

_index_cache: faiss.Index | None = None
_chunks_cache: list[dict] | None = None


def _load_index_once() -> tuple[faiss.Index, list[dict]]:
    """Load index + chunks from disk, caching in module-level vars."""
    global _index_cache, _chunks_cache
    if _index_cache is not None:
        return _index_cache, _chunks_cache

    if not Path(INDEX_PATH).exists() or not Path(CHUNKS_PATH).exists():
        raise FileNotFoundError(
            "Index not built — run  python rag/build_index.py  first."
        )

    _index_cache = faiss.read_index(INDEX_PATH)
    with open(CHUNKS_PATH, encoding="utf-8") as f:
        _chunks_cache = json.load(f)
    return _index_cache, _chunks_cache


def search_documents(query: str, k: int = TOP_K) -> list[dict]:
    """Embed query and return top-k matching chunks.

    Returns list of dicts: {source_file, doc_type, page_number, text, score}.
    score is cosine similarity (0–1, higher = more similar).
    """
    index, chunks = _load_index_once()

    q_vec = _embed([query])            # (1, dim)
    faiss.normalize_L2(q_vec)
    scores, indices = index.search(q_vec, k)

    results = []
    for score, idx in zip(scores[0], indices[0]):
        if idx == -1:
            continue
        chunk = chunks[idx]
        results.append({
            "source_file": chunk["source_file"],
            "doc_type":    chunk["doc_type"],
            "page_number": chunk["page_number"],
            "text":        chunk["text"],
            "score":       float(score),
        })
    return results
