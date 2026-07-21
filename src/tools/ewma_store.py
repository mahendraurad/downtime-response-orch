"""
tools/ewma_store.py

JSON-sidecar persistence for the Monitoring Agent's EWMA state.

State shape:
    {
      "BRG_001": {
        "vib_rms_mm_s": 2.13,
        "kurtosis":     2.52,
        "temp_c":       58.4,
        "bpfo_energy":  0.81
      },
      "BRG_002": { ... }
    }

One entry per (bearing_id, signal_field). Missing entries are initialised
to the bearing's baseline mean on first observation so the EWMA does not
spike when the agent starts up cold.
"""
from __future__ import annotations

import json
import os
import threading
import tempfile
from typing import Callable, Dict, Tuple, Any

# Prevents concurrent FastAPI request threads from corrupting the EWMA file
_LOCK = threading.Lock()

_HERE         = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.normpath(os.path.join(_HERE, "..", ".."))


def _resolve(path: str) -> str:
    """Resolve relative paths against project root, leave absolute paths alone."""
    return path if os.path.isabs(path) else os.path.join(_PROJECT_ROOT, path)


def load_state(path: str) -> Dict[str, Dict[str, float]]:
    """Load EWMA state from disk. Returns {} if the file does not exist yet."""
    resolved = _resolve(path)
    with _LOCK:
        if not os.path.exists(resolved):
            return {}
        try:
            with open(resolved, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except (json.JSONDecodeError, OSError):
            return {}


def save_state(state: Dict[str, Dict[str, float]], path: str) -> None:
    """Persist EWMA state to disk (overwrites)."""
    resolved = _resolve(path)
    with _LOCK:
        os.makedirs(os.path.dirname(resolved) or ".", exist_ok=True)
        with open(resolved, "w", encoding="utf-8") as fh:
            json.dump(state, fh, indent=2, sort_keys=True)


# ************** Added by Prateek Mittal on 17th July 2026 ******************
# Atomic read-modify-write operation for Agent 2 state. The lock spans the
# entire mutation, writes use replace semantics, and corrupt state recovery is
# explicitly returned to the caller for audit.
def mutate_state(path: str, mutator: Callable[[Dict], Any]) -> Tuple[Any, bool]:
    resolved = _resolve(path)
    recovered = False
    with _LOCK:
        state: Dict = {}
        if os.path.exists(resolved):
            try:
                with open(resolved, "r", encoding="utf-8") as fh:
                    state = json.load(fh)
                if not isinstance(state, dict):
                    state = {}
                    recovered = True
            except (json.JSONDecodeError, OSError):
                state = {}
                recovered = True

        result = mutator(state)
        directory = os.path.dirname(resolved) or "."
        os.makedirs(directory, exist_ok=True)
        fd, temp_path = tempfile.mkstemp(prefix=".ewma-", suffix=".json", dir=directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(state, fh, indent=2, sort_keys=True)
                fh.flush()
                os.fsync(fh.fileno())
            try:
                os.replace(temp_path, resolved)
            except PermissionError:
                # Windows security/indexing software can briefly deny replace.
                # The process-wide lock still protects this fallback write.
                with open(resolved, "w", encoding="utf-8") as target:
                    json.dump(state, target, indent=2, sort_keys=True)
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)
        return result, recovered
# ***********************
