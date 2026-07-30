"""Optional, failure-isolated Langfuse instrumentation helpers."""
from __future__ import annotations

import functools
import logging
import os
from typing import Callable

logger = logging.getLogger(__name__)


def _enabled() -> bool:
    return os.getenv("LANGFUSE_TRACING", "false").strip().lower() in {
        "1", "true", "yes", "on",
    }


def langfuse_observe(name: str, as_type: str = "span") -> Callable:
    """Return a Langfuse decorator, or a transparent decorator when disabled.

    Import and telemetry failures must never affect the operational pipeline.
    """
    def decorate(fn: Callable) -> Callable:
        if not _enabled():
            return fn
        try:
            from langfuse import observe
            return observe(name=name, as_type=as_type)(fn)
        except Exception as exc:
            logger.warning("Langfuse instrumentation unavailable: %s", exc)
            return fn
    return decorate


def flush_observability() -> None:
    """Flush configured clients during graceful process shutdown."""
    if _enabled():
        try:
            from langfuse import get_client
            get_client().flush()
        except Exception as exc:
            logger.warning("Could not flush Langfuse telemetry: %s", exc)
