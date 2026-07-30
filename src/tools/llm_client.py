"""
tools/llm_client.py

Thin, defensive wrapper around Azure AI services for the rare cases where the
deterministic rule engine cannot produce a confident answer and we want an LLM
to fill the gap (e.g. Predictive Risk Agent fallback).

Supports two Azure inference configurations (auto-detected from the endpoint URL):

  Azure AI Foundry  — endpoint contains .services.ai.azure.com
                      Uses openai SDK with the resource base URL and API version
                      2025-04-01-preview. The /api/projects/... suffix, if present
                      in the env var, is stripped automatically so the standard
                      Azure OpenAI deployment path is used.
                      Env vars: AZURE_AI_ENDPOINT, AZURE_AI_KEY, AZURE_AI_DEPLOYMENT

  Azure OpenAI      — all other endpoints (e.g. .openai.azure.com)
                      Standard AzureOpenAI client with API version 2024-06-01.
                      Env vars: AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_KEY,
                                AZURE_OPENAI_DEPLOYMENT

Azure AI Foundry vars take priority when both are present. Constructor arguments
override env vars.

Design principles:
  * Optional dependency — the `openai` package is imported lazily. If it is not
    installed, or credentials are missing, is_configured() returns False and
    every call returns None. Callers MUST treat None as "LLM unavailable."
  * Stateless and cheap to construct.
  * JSON-first: complete_json() asks the model for a JSON object, parses it
    defensively, and returns a dict or None.
  * Nothing here ever raises into the agent pipeline.
"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Dict, List, Optional
from langsmith import traceable
from src.tools.observability import langfuse_observe

logger = logging.getLogger(__name__)

# API versions — pinned for reproducibility.
_FOUNDRY_API_VERSION = "2025-04-01-preview"   # works with gpt-5.4 and later
_OPENAI_API_VERSION  = "2024-06-01"            # legacy Azure OpenAI

_FOUNDRY_ENDPOINT_MARKER = ".services.ai.azure.com"


def _extract_foundry_base(endpoint: str) -> str:
    """
    Strip the /api/projects/... project suffix from an Azure AI Foundry URL so
    the openai SDK can append the standard /openai/deployments/... path.

    Example:
      "https://foo.services.ai.azure.com/api/projects/my-proj"
      → "https://foo.services.ai.azure.com"
    """
    # Accept project URLs and copy-pasted OpenAI-compatible endpoint URLs.
    return re.sub(r"/(?:api|openai)/.*$", "", endpoint.rstrip("/"))


class LLMClient:
    """Defensive Azure LLM client. Never raises; returns None on any failure."""

    def __init__(
        self,
        endpoint: Optional[str] = None,
        api_key: Optional[str] = None,
        deployment: Optional[str] = None,
        api_version: Optional[str] = None,
        timeout_seconds: float = 20.0,
    ):
        # Azure AI Foundry vars take priority; fall back to legacy Azure OpenAI vars.
        raw_endpoint    = (endpoint
                           or os.getenv("AZURE_AI_ENDPOINT")
                           or os.getenv("AZURE_OPENAI_ENDPOINT"))
        self.api_key    = (api_key
                           or os.getenv("AZURE_AI_KEY")
                           or os.getenv("AZURE_OPENAI_KEY"))
        self.deployment = (deployment
                           or os.getenv("AZURE_AI_DEPLOYMENT")
                           or os.getenv("AZURE_OPENAI_DEPLOYMENT"))
        self.timeout    = timeout_seconds
        self._client    = None  # lazy

        # For Foundry endpoints, normalise to the resource base URL so the
        # openai SDK can append its standard path correctly.
        if raw_endpoint and _FOUNDRY_ENDPOINT_MARKER in raw_endpoint:
            self.endpoint    = _extract_foundry_base(raw_endpoint)
            self.api_version = api_version or _FOUNDRY_API_VERSION
            self._use_foundry = True
        else:
            self.endpoint    = raw_endpoint
            self.api_version = api_version or _OPENAI_API_VERSION
            self._use_foundry = False

    # ------------------------------------------------------------------

    def is_configured(self) -> bool:
        """True only if all three connection values are present."""
        return bool(self.endpoint and self.api_key and self.deployment)

    def _get_client(self):
        """Lazily build the AzureOpenAI client. Returns None if unavailable."""
        if self._client is not None:
            return self._client
        if not self.is_configured():
            return None
        try:
            from openai import AzureOpenAI  # lazy: optional dependency
        except ImportError:
            logger.warning(
                "LLM fallback requested but the 'openai' package is not "
                "installed. Run: pip install openai>=1.0.0. "
                "Falling back to the deterministic result."
            )
            return None
        try:
            self._client = AzureOpenAI(
                azure_endpoint=self.endpoint,
                api_key=self.api_key,
                api_version=self.api_version,
                timeout=self.timeout,
            )
        except Exception as exc:
            logger.warning("Could not construct AzureOpenAI client: %s", exc)
            return None
        return self._client

    # ------------------------------------------------------------------

    @traceable(name="Azure LLM Completion", run_type="llm",
               tags=["dro", "llm", "azure"])
    @langfuse_observe("Azure LLM Completion", as_type="generation")
    def complete_json(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.2,
        max_tokens: int = 500,
    ) -> Optional[Dict]:
        """
        Ask the model for a single JSON object and return it as a dict.
        Returns None if the LLM is unavailable, errors, or returns non-JSON.
        """
        client = self._get_client()
        if client is None:
            return None

        messages: List[Dict[str, str]] = [
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": user_prompt},
        ]

        # Newer models (gpt-5.x, o-series) use max_completion_tokens; older
        # models use max_tokens. Pass both so the API picks the right one —
        # Azure silently ignores the inapplicable key.
        kwargs: Dict = dict(
            model=self.deployment,
            messages=messages,
            temperature=temperature,
        )
        if self._use_foundry:
            # Foundry / gpt-5.x: use max_completion_tokens
            kwargs["max_completion_tokens"] = max_tokens
        else:
            kwargs["max_tokens"] = max_tokens
            kwargs["response_format"] = {"type": "json_object"}

        try:
            resp    = client.chat.completions.create(**kwargs)
            content = resp.choices[0].message.content
        except Exception as exc:
            logger.warning("LLM call failed (%s) — using deterministic result.", exc)
            return None

        return self._parse_json(content)

    @staticmethod
    def _parse_json(content: Optional[str]) -> Optional[Dict]:
        """Parse JSON from model output, stripping markdown code fences."""
        if not content:
            return None
        stripped = content.strip()
        if stripped.startswith("```"):
            lines = stripped.splitlines()
            stripped = "\n".join(
                lines[1:-1] if lines[-1].strip() == "```" else lines[1:]
            )
        try:
            return json.loads(stripped)
        except (json.JSONDecodeError, TypeError) as exc:
            logger.warning("LLM returned non-JSON content (%s).", exc)
            return None
