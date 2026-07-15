"""app_config.py — Loads tunable parameters from config.json (with safe fallbacks).

This is the single place that reads config.json. Any module needing a tunable calls
`get_threshold(key, default)`. If config.json is missing, unreadable, or lacks the
key, the caller's `default` is returned — so the app always works, and behaviour is
unchanged unless config.json deliberately overrides a value.

Secrets are NOT stored here (they stay in .env). RAG settings stay in rag/config.py.
"""
import json
from pathlib import Path

_CONFIG_PATH = Path(__file__).parent / "config.json"

_cache = None


def _load():
    """Read and cache config.json once. Returns {} if the file is missing or invalid."""
    global _cache
    if _cache is not None:
        return _cache
    try:
        with open(_CONFIG_PATH, encoding="utf-8") as f:
            _cache = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        _cache = {}
    return _cache


def get_threshold(key, default):
    """Return thresholds[key] from config.json, or `default` if absent/invalid."""
    cfg = _load()
    try:
        value = cfg.get("thresholds", {}).get(key)
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default
