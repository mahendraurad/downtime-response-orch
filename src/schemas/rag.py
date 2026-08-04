"""Contracts for governed, source-backed open-ended knowledge answers."""
from __future__ import annotations

from typing import Any, Dict, List

from pydantic import BaseModel, Field


class RAGCitation(BaseModel):
    """A citation returned to chat consumers for one retrieved passage."""

    citation_id: str
    title: str
    chunk_id: str = ""
    source_uri: str = ""
    retrieval_score: float
    source_type: str = "approved_document"


class GovernedRAGResult(BaseModel):
    """Safe outcome of an open-ended retrieval request."""

    status: str  # grounded | no_match | retrieval_failed | blocked
    answer: str
    citations: List[RAGCitation] = Field(default_factory=list)
    retrieval_query: str = ""
    retrieval_hit_count: int = 0
    index_version: str = ""
    llm_used: bool = False
    status_reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return self.model_dump()
