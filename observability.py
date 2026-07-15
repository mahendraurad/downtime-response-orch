# observability.py — Langfuse tracing helper (feature-flagged, fails safe)
import os
from dotenv import load_dotenv

load_dotenv()

_ENABLED = os.getenv("LANGFUSE_ENABLED", "false").strip().lower() == "true"
_client = None
_init_error = None


def langfuse_enabled() -> bool:
    """True only if the flag is on AND the client initialized cleanly."""
    return _ENABLED and _client is not None


def _init_client():
    global _client, _init_error
    if not _ENABLED:
        return None
    if _client is not None:
        return _client
    try:
        from langfuse import get_client
        client = get_client()
        # verify credentials/connection; auth_check returns True/False
        if client.auth_check():
            _client = client
        else:
            _init_error = "auth_check failed — check keys/host"
    except Exception as e:
        _init_error = f"{type(e).__name__}: {e}"
    return _client


def get_callback_handler():
    """Return a Langfuse LangChain CallbackHandler, or None if disabled/unavailable.
    Pass into chain/graph .invoke(config={'callbacks': [handler]}) only if not None.
    """
    if not _ENABLED:
        return None
    if _init_client() is None:
        return None
    try:
        from langfuse.langchain import CallbackHandler
        return CallbackHandler()
    except Exception as e:
        global _init_error
        _init_error = f"handler: {type(e).__name__}: {e}"
        return None


def flush():
    """Flush buffered traces (call before process exit / after a request)."""
    if _client is not None:
        try:
            _client.flush()
        except Exception:
            pass


def status() -> str:
    """Human-readable status for a quick diagnostic."""
    if not _ENABLED:
        return "Langfuse DISABLED (LANGFUSE_ENABLED != true)"
    if _init_client() is not None:
        return "Langfuse ENABLED and authenticated"
    return f"Langfuse ENABLED but NOT working: {_init_error}"


def warm_up():
    """Eagerly initialize the Langfuse client once, at app startup.
    Safe to call repeatedly; no-op if disabled. Returns status string.
    """
    if not _ENABLED:
        return "disabled"
    _init_client()
    return "ok" if _client is not None else f"failed: {_init_error}"
