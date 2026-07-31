"""Typed persona context shared by every backend agent call."""
from __future__ import annotations

from typing import List, Optional
from pydantic import BaseModel, Field


class PersonaContext(BaseModel):
    id: str
    display_name: str
    role: str
    domain: str
    response_depth: str
    preferred_format: str
    headline_focus: List[str] = Field(default_factory=list)
    details: List[str] = Field(default_factory=list)
    actions: List[str] = Field(default_factory=list)
    tags: List[str] = Field(default_factory=list)
    suppress_fields: List[str] = Field(default_factory=list)
    authority_usd: Optional[float] = None
    requested_id: str = ""
