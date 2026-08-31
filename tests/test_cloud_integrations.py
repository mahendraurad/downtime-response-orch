"""Contract tests for optional production infrastructure adapters."""
from __future__ import annotations

from pathlib import Path
import pytest

from src.tools.azure_search_retriever import (
    AzureSearchRetriever, _escape_filter, _normalize_search_score,
)
from src.tools.checkpointing import build_checkpoint_resources
from src.tools.retrieval_backend import build_retrieval_backend


def test_environment_template_enables_dro_cloud_resources_without_secrets():
    example=Path(".env.example").read_text(encoding="utf-8").replace("\r\n","\n")
    assert "AZURE_AI_ENDPOINT=https://proj-boxai-dro-resource.services.ai.azure.com" in example
    assert "AZURE_AI_DEPLOYMENT=gpt-5.4-mini" in example
    assert "AZURE_SEARCH_ENDPOINT=https://ai-search-dro.search.windows.net" in example
    assert "AZURE_SEARCH_INDEX_NAME=ai-search-dro" in example
    assert "RAG_BACKEND=azure" in example and "AZURE_SEARCH_AUTH=rbac" in example
    assert "AZURE_BLOB_CONTAINER=sop-dro" in example
    for secret_name in (
        "AZURE_AI_KEY","AZURE_OPENAI_KEY","AZURE_SEARCH_KEY",
        "AZURE_CLIENT_SECRET","AZURE_BLOB_CONNECTION_STRING","POSTGRES_URL",
        "LANGSMITH_API_KEY","LANGFUSE_PUBLIC_KEY","LANGFUSE_SECRET_KEY",
        "OPENAI_API_KEY","DRO_JWT_SECRET",
    ):
        assert f"{secret_name}=\n" in example


class _FakeSearchClient:
    def __init__(self, rows):
        self.rows = rows
        self.kwargs = None

    def search(self, **kwargs):
        self.kwargs = kwargs
        return self.rows


def test_azure_search_maps_results_to_agent_5_contract(monkeypatch):
    monkeypatch.delenv("AZURE_SEARCH_VECTOR_FIELD", raising=False)
    adapter = AzureSearchRetriever("https://search.example", "secret", "sops")
    client = _FakeSearchClient([{
        "source": "SOP-17", "content": "Step 1: Isolate the motor.",
        "fault_mode": "outer_race_fault", "asset_type": "motor",
        "iso_stage": 3, "@search.score": 0.91,
    }])
    adapter._client = client

    hits = adapter.retrieve(
        "outer race motor stage 3", top_k=2,
        fault_mode="outer_race_fault", asset_type="motor", iso_stage=3,
    )

    assert hits == [{
        "source": "SOP-17", "text": "Step 1: Isolate the motor.",
        "chunk_id": "", "source_uri": "",
        "score": 0.91, "raw_score": 0.91, "fault_mode": "outer_race_fault",
        "asset_type": "motor", "iso_stage": 3,
    }]
    assert client.kwargs["search_text"] == "outer race motor stage 3"
    assert client.kwargs["top"] == 2
    assert "fault_mode eq 'outer_race_fault'" in client.kwargs["filter"]


def test_azure_search_discards_results_without_citation_or_content(monkeypatch):
    monkeypatch.delenv("AZURE_SEARCH_VECTOR_FIELD", raising=False)
    adapter = AzureSearchRetriever("https://search.example", "secret", "sops")
    adapter._client = _FakeSearchClient([
        {"source": "", "content": "uncited"},
        {"source": "SOP-1", "content": ""},
    ])
    assert adapter.retrieve("query") == []


def test_azure_search_supports_minimal_chunk_schema(monkeypatch):
    monkeypatch.setenv("AZURE_SEARCH_CONTENT_FIELD", "chunk")
    monkeypatch.setenv("AZURE_SEARCH_SOURCE_FIELD", "title")
    monkeypatch.setenv("AZURE_SEARCH_CHUNK_ID_FIELD", "chunk_id")
    monkeypatch.setenv("AZURE_SEARCH_SOURCE_URI_FIELD", "parent_id")
    monkeypatch.setenv("AZURE_SEARCH_VECTOR_FIELD", "")
    monkeypatch.setenv("AZURE_SEARCH_FAULT_MODE_FIELD", "none")
    monkeypatch.setenv("AZURE_SEARCH_ASSET_TYPE_FIELD", "none")
    monkeypatch.setenv("AZURE_SEARCH_ISO_STAGE_FIELD", "none")
    adapter = AzureSearchRetriever("https://search.example", "secret", "sops")
    client = _FakeSearchClient([{
        "title": "Bearing SOP", "chunk": "Approved inspection procedure.",
        "chunk_id": "chunk-17", "parent_id": "blob://sop/bearing",
        "@search.score": 1.2,
    }])
    adapter._client = client

    hits = adapter.retrieve(
        "bearing inspection", fault_mode="outer_race_fault",
        asset_type="motor", iso_stage=3,
    )

    assert hits[0]["source"] == "Bearing SOP"
    assert hits[0]["chunk_id"] == "chunk-17"
    assert hits[0]["source_uri"] == "blob://sop/bearing"
    assert hits[0]["fault_mode"] == ""
    assert client.kwargs["filter"] is None
    assert client.kwargs["select"] == ["title", "chunk", "chunk_id", "parent_id"]


def test_odata_filter_value_is_escaped():
    assert _escape_filter("operator's motor") == "operator''s motor"


def test_hybrid_rrf_score_is_normalized_for_agent_contract():
    assert _normalize_search_score(2 / 60, hybrid=True) == pytest.approx(1.0)
    assert _normalize_search_score(1 / 60, hybrid=True) == pytest.approx(0.5)
    assert _normalize_search_score(0.8, hybrid=True) == pytest.approx(0.8)


def test_explicit_azure_backend_requires_configuration(monkeypatch):
    monkeypatch.setenv("RAG_BACKEND", "azure")
    monkeypatch.setenv("AZURE_SEARCH_AUTH", "key")
    for name in ("AZURE_SEARCH_ENDPOINT", "AZURE_SEARCH_KEY",
                 "AZURE_SEARCH_INDEX_NAME"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(RuntimeError, match="not configured"):
        build_retrieval_backend()


def test_rbac_backend_does_not_require_api_key(monkeypatch):
    monkeypatch.setenv("AZURE_SEARCH_AUTH", "rbac")
    monkeypatch.delenv("AZURE_SEARCH_KEY", raising=False)
    adapter = AzureSearchRetriever(
        endpoint="https://search.example", index_name="sops"
    )
    assert adapter.is_configured() is True


def test_auto_rag_backend_falls_back_to_local(monkeypatch):
    monkeypatch.setenv("RAG_BACKEND", "auto")
    for name in ("AZURE_SEARCH_ENDPOINT", "AZURE_SEARCH_KEY"):
        monkeypatch.delenv(name, raising=False)
    retriever, version = build_retrieval_backend()
    assert callable(retriever)
    assert len(version) == 16


def test_checkpoint_backend_none_has_no_external_dependency(monkeypatch):
    monkeypatch.setenv("CHECKPOINT_BACKEND", "none")
    resources = build_checkpoint_resources()
    assert resources.enabled is False
    resources.close()


def test_postgres_checkpoint_backend_requires_url(monkeypatch):
    monkeypatch.setenv("CHECKPOINT_BACKEND", "postgres")
    monkeypatch.delenv("POSTGRES_URL", raising=False)
    with pytest.raises(RuntimeError, match="POSTGRES_URL"):
        build_checkpoint_resources()


def test_invalid_checkpoint_backend_fails_fast(monkeypatch):
    monkeypatch.setenv("CHECKPOINT_BACKEND", "memory-ish")
    with pytest.raises(RuntimeError, match="none or postgres"):
        build_checkpoint_resources()
