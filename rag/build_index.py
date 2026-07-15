"""
rag/build_index.py
Run once (or whenever PDFs change) to build the FAISS index from scratch.

Usage (from project root, venv active):
    python rag/build_index.py
"""
import sys
from pathlib import Path

# Allow running from project root without installing the package
sys.path.insert(0, str(Path(__file__).parent.parent))

from rag.document_store import build_index

if __name__ == "__main__":
    build_index()
