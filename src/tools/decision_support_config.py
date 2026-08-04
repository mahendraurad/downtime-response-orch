"""Validated, configurable decision-support and execution policies."""
from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path


DEFAULT_PATH = Path(__file__).resolve().parents[2] / "config" / "decision_support_config.json"


def load_decision_support_config(path: str | Path = DEFAULT_PATH) -> dict:
    source = Path(path)
    payload = json.loads(source.read_text(encoding="utf-8"))
    required = {
        "version", "currency", "cost_model", "authority_usd",
        "approval_hierarchy", "execution_steps",
    }
    missing = sorted(required - set(payload))
    if missing:
        raise ValueError(f"decision support config missing: {', '.join(missing)}")
    hierarchy = payload["approval_hierarchy"]
    if hierarchy != ["supervisor", "manager", "executive"]:
        raise ValueError("approval hierarchy must be supervisor -> manager -> executive")
    limits = payload["authority_usd"]
    if any(float(limits[name]) <= 0 for name in hierarchy):
        raise ValueError("authority limits must be positive")
    if not payload["cost_model"].get("action_costs"):
        raise ValueError("at least one action cost must be configured")
    return deepcopy(payload)


def decision_support_config_version(config: dict) -> str:
    encoded = json.dumps(config, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()[:16]
