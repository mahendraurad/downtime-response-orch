"""Data-driven persona registry and prescriptive response formatter."""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from src.schemas.persona import PersonaContext

_CONFIG = Path(__file__).resolve().parents[2] / "config" / "personas.json"


@lru_cache(maxsize=1)
def _registry() -> dict:
    raw = json.loads(_CONFIG.read_text(encoding="utf-8"))
    if set(raw["personas"]) != {
        "supervisor", "engineer", "maintenance", "manager",
        "executive", "ot", "safety",
    }:
        raise ValueError("persona registry must define exactly the seven approved personas")
    return raw


def normalize_persona_id(persona_id: str) -> str:
    requested = str(persona_id or "supervisor").strip().lower()
    raw = _registry()
    canonical = raw.get("aliases", {}).get(requested, requested)
    if canonical not in raw["personas"]:
        raise ValueError(f"unsupported persona: {requested}")
    return canonical


def build_persona_context(persona: str | PersonaContext = "supervisor") -> PersonaContext:
    if isinstance(persona, PersonaContext):
        return persona
    requested = str(persona or "supervisor").strip().lower()
    canonical = normalize_persona_id(requested)
    return PersonaContext(
        id=canonical, requested_id=requested,
        **_registry()["personas"][canonical],
    )


def persona_prompt(context: PersonaContext) -> str:
    return (
        f"Persona: {context.id}\n"
        f"Name: {context.display_name}\n"
        f"Role: {context.role}\n"
        f"Response depth: {context.response_depth}\n"
        f"Preferred format: {context.preferred_format}\n"
        f"Headline focus: {', '.join(context.headline_focus)}\n"
        f"Suppress fields: {', '.join(context.suppress_fields) or 'none'}"
    )


def apply_persona_contract(payload: dict[str, Any],
                           context: PersonaContext) -> dict[str, Any]:
    """Attach rendering policy without changing evidence or decisions."""
    result = dict(payload)
    result["persona"] = context.model_dump()
    result["persona_depth"] = context.response_depth
    result["preferred_format"] = context.preferred_format
    return result
