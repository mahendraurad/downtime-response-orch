"""Select the configured RAG backend while preserving one retrieval contract."""
from __future__ import annotations

import os
from typing import Callable, Tuple


def build_retrieval_backend() -> Tuple[Callable, str]:
    backend = os.getenv("RAG_BACKEND", "local").strip().lower()
    if backend not in {"local", "azure", "auto"}:
        raise RuntimeError("RAG_BACKEND must be local, azure, or auto")

    if backend in {"azure", "auto"}:
        from src.tools.azure_search_retriever import AzureSearchRetriever
        azure = AzureSearchRetriever()
        if azure.is_configured():
            return azure.retrieve, azure.version()
        if backend == "azure":
            raise RuntimeError("RAG_BACKEND=azure but Azure Search is not configured")

    from src.tools.retriever import retrieve, knowledge_index_version
    return retrieve, knowledge_index_version()
