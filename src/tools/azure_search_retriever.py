"""Azure AI Search adapter implementing Agent 5's canonical retrieval contract."""
from __future__ import annotations

import hashlib
import json
import os
from typing import Dict, List

from langsmith import traceable

from src.tools.observability import langfuse_observe


def _escape_filter(value: str) -> str:
    return str(value).replace("'", "''")


def _optional_field(environment_name: str, default: str) -> str:
    value = os.getenv(environment_name)
    if value is None:
        return default
    value = value.strip()
    return "" if value.lower() in {"", "none", "null", "-"} else value


def _normalize_search_score(raw_score: float, hybrid: bool) -> float:
    """Map Azure hybrid RRF scores to Agent 5's documented 0..1 contract.

    RRF uses a rank constant of 60. A hybrid query has two ranked lists, so its
    maximum first-position score is approximately 2/60. Non-RRF service scores
    are already suitable and are merely clamped.
    """
    if hybrid and 0.0 <= raw_score <= 0.1:
        return max(0.0, min(1.0, raw_score / (2.0 / 60.0)))
    return max(0.0, min(1.0, raw_score))


class AzureSearchRetriever:
    """Lazy Azure Search client with optional integrated vectorization."""

    def __init__(self, endpoint: str = "", key: str = "", index_name: str = ""):
        self.endpoint = endpoint or os.getenv("AZURE_SEARCH_ENDPOINT", "")
        self.key = key or os.getenv("AZURE_SEARCH_KEY", "")
        self.index_name = index_name or os.getenv("AZURE_SEARCH_INDEX_NAME", "")
        self.auth_mode = os.getenv("AZURE_SEARCH_AUTH", "auto").strip().lower()
        if self.auth_mode not in {"auto", "key", "rbac"}:
            raise RuntimeError("AZURE_SEARCH_AUTH must be auto, key, or rbac")
        self.content_field = os.getenv("AZURE_SEARCH_CONTENT_FIELD", "content")
        self.source_field = os.getenv("AZURE_SEARCH_SOURCE_FIELD", "source")
        # Metadata fields are optional because a minimal Azure chunk index may
        # contain only chunk, title, parent/key, and vector fields.
        self.fault_field = _optional_field(
            "AZURE_SEARCH_FAULT_MODE_FIELD", "fault_mode"
        )
        self.asset_field = _optional_field(
            "AZURE_SEARCH_ASSET_TYPE_FIELD", "asset_type"
        )
        self.stage_field = _optional_field(
            "AZURE_SEARCH_ISO_STAGE_FIELD", "iso_stage"
        )
        self.vector_field = _optional_field("AZURE_SEARCH_VECTOR_FIELD", "")
        self._client = None

    def is_configured(self) -> bool:
        has_credential = bool(self.key) or self.auth_mode in {"auto", "rbac"}
        return bool(self.endpoint and self.index_name and has_credential)

    def _get_client(self):
        if self._client is not None:
            return self._client
        if not self.is_configured():
            raise RuntimeError(
                "Azure Search requires AZURE_SEARCH_ENDPOINT and "
                "AZURE_SEARCH_INDEX_NAME plus key or RBAC authentication"
            )
        try:
            from azure.search.documents import SearchClient
        except ImportError as exc:
            raise RuntimeError(
                "Install azure-search-documents to use RAG_BACKEND=azure"
            ) from exc
        if self.auth_mode == "rbac" or (
            self.auth_mode == "auto" and not self.key
        ):
            try:
                from azure.identity import DefaultAzureCredential
            except ImportError as exc:
                raise RuntimeError(
                    "Install azure-identity to use AZURE_SEARCH_AUTH=rbac"
                ) from exc
            credential = DefaultAzureCredential()
        else:
            from azure.core.credentials import AzureKeyCredential
            credential = AzureKeyCredential(self.key)
        self._client = SearchClient(
            endpoint=self.endpoint,
            index_name=self.index_name,
            credential=credential,
        )
        return self._client

    @traceable(name="Azure AI Search RAG", run_type="retriever",
               tags=["dro", "rag", "azure-search"])
    @langfuse_observe("Azure AI Search RAG", as_type="retriever")
    def retrieve(self, query: str, top_k: int = 3, fault_mode: str = "",
                 asset_type: str = "", iso_stage: int = 0, **_: object) -> List[Dict]:
        filters = []
        if fault_mode and self.fault_field:
            filters.append(f"{self.fault_field} eq '{_escape_filter(fault_mode)}'")
        if asset_type and self.asset_field:
            filters.append(f"{self.asset_field} eq '{_escape_filter(asset_type)}'")

        select_fields = [
            field for field in (
                self.source_field, self.content_field, self.fault_field,
                self.asset_field, self.stage_field,
            ) if field
        ]
        # Preserve order while avoiding duplicate fields.
        select_fields = list(dict.fromkeys(select_fields))
        kwargs: Dict = {
            "search_text": query,
            "top": top_k,
            "filter": " and ".join(filters) or None,
            "select": select_fields,
        }
        if self.vector_field:
            try:
                from azure.search.documents.models import VectorizableTextQuery
                kwargs["vector_queries"] = [
                    VectorizableTextQuery(
                        text=query, k_nearest_neighbors=top_k,
                        fields=self.vector_field,
                    )
                ]
            except ImportError as exc:
                raise RuntimeError(
                    "Configured vector search requires a recent "
                    "azure-search-documents package"
                ) from exc

        results = []
        for item in self._get_client().search(**kwargs):
            source = str(item.get(self.source_field, "") or "")
            text = str(item.get(self.content_field, "") or "")
            if not source or not text:
                continue
            raw_score = float(item.get("@search.score", 0.0) or 0.0)
            results.append({
                "source": source,
                "text": text,
                "score": _normalize_search_score(
                    raw_score, hybrid=bool(self.vector_field)
                ),
                "raw_score": raw_score,
                "fault_mode": (
                    str(item.get(self.fault_field, "") or "")
                    if self.fault_field else ""
                ),
                "asset_type": (
                    str(item.get(self.asset_field, "") or "")
                    if self.asset_field else ""
                ),
                "iso_stage": (
                    int(item.get(self.stage_field, 0) or 0)
                    if self.stage_field else 0
                ),
            })
        return results

    def version(self) -> str:
        payload = {
            "backend": "azure-search", "endpoint": self.endpoint,
            "index": self.index_name, "content_field": self.content_field,
            "source_field": self.source_field, "vector_field": self.vector_field,
            "auth_mode": self.auth_mode,
        }
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True).encode("utf-8")
        ).hexdigest()[:16]
