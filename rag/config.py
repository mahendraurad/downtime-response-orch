# rag/config.py — single source of truth for all RAG settings.
#
# To switch embedding models (e.g. to Azure), change EMBEDDING_MODEL_NAME and
# the embed function in document_store.py, then DELETE the index files and
# re-run build_index — the index MUST be rebuilt with the same model used for
# queries.

EMBEDDING_MODEL_NAME = "all-MiniLM-L6-v2"

CHUNK_SIZE    = 800   # target characters per chunk
CHUNK_OVERLAP = 150   # overlap between consecutive chunks

TOP_K = 6             # default number of results returned by search_documents

PDF_DIR     = "data/sops_and_cases"
INDEX_PATH  = "rag/faiss_index.bin"
CHUNKS_PATH = "rag/chunks.json"
