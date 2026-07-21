"""
tools/curated_store.py

Persists remediated, routable signals to a CURATED table
(data/curated_signals.json), leaving the raw telemetry source immutable.

Why a separate table?
  * The raw historian feed (telemetry_scenarios.json) is the audit/lineage
    source of truth and must never be mutated — we need to know what actually
    arrived vs. what we imputed.
  * This curated table is the trusted signals layer the Monitoring Agent
    and all downstream agents read. It contains every routable record after
    remediation, with full provenance of what happened to it:
      - imputed records carry the filled-in values + which fields + method
      - dropped / rejected records are excluded entirely
      - kept records flow through with their quality flag

Column schema (same names in JSON, Postgres, or any other store):
  # ── Identity ─────────────────────────────────────────
  telemetry_id          TEXT  PRIMARY KEY   -- unique per record
  timestamp_utc         TEXT
  asset_id              TEXT
  bearing_id            TEXT
  channel_id            TEXT

  # ── Signal values (post-imputation) ──────────────────
  rpm                   REAL
  load_pct              REAL
  machine_state         TEXT
  startup_shutdown_flag BOOLEAN
  vib_rms_mm_s          REAL
  vib_peak_g            REAL
  kurtosis              REAL
  temp_c                REAL
  bpfo_energy           REAL
  bpfi_energy           REAL
  bsf_energy            REAL
  ftf_energy            REAL
  envelope_peak_hz      REAL
  motor_current_a       REAL
  voltage_v             REAL
  current_deviation     REAL
  signal_quality_score  REAL
  data_source           TEXT

  # ── Quality & remediation provenance ─────────────────
  data_quality_score    REAL    -- DFA composite score after remediation
  validation_status     TEXT    -- VALID | FLAGGED
  remediation_action    TEXT    -- KEEP | IMPUTE | DROP (dropped rows absent)
  imputed_fields        TEXT[]  -- fields filled from baseline ([] if none)
  imputation_method     TEXT    -- e.g. "baseline_mean" ("" if not imputed)
  curated_at            TEXT    -- UTC ISO-8601 timestamp of this write

Swap the JSON read/write for Postgres — nothing else changes:
  write_curated()  ->  INSERT ... ON CONFLICT (telemetry_id) DO UPDATE ...
  load_curated()   ->  SELECT * FROM curated_signals WHERE ...
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Dict, List, Optional

from src.schemas.bearing_signal import TrustedBearingSignal, ValidationStatus

_HERE    = os.path.dirname(os.path.abspath(__file__))
_CURATED = os.path.normpath(os.path.join(_HERE, "..", "..", "data", "curated_signals.json"))


# ---------------------------------------------------------------------------
# Row builder
# ---------------------------------------------------------------------------

def curated_row(trusted: TrustedBearingSignal) -> dict:
    """
    Flatten one trusted signal into a curated table row.
    All signal columns carry post-imputation values so downstream agents
    read the best available value. Provenance columns record exactly what
    happened so agents can decide how much to trust each field.
    """
    row = dict(trusted.raw.to_dict())
    row["data_quality_score"] = round(trusted.data_quality_score, 4)
    row["validation_status"]  = trusted.validation_status.value
    row["remediation_action"] = trusted.remediation_action or "KEEP"
    row["imputed_fields"]     = list(trusted.imputed_fields)
    row["imputation_method"]  = trusted.imputation_method or ""
    row["curated_at"]         = datetime.now(tz=timezone.utc).isoformat()
    return row


# ---------------------------------------------------------------------------
# Build the routable set from a batch of DFA results + remediation decisions
# ---------------------------------------------------------------------------

def build_curated_rows(
    results:   List[TrustedBearingSignal],
    decisions: Dict[str, Optional[TrustedBearingSignal]],
) -> List[dict]:
    """
    Build the curated (routable) record set from one processing batch.

    `decisions` maps telemetry_id -> the final signal for records that went
    through the remediation step, or None if the operator dropped it.

    Rules applied in order:
      decided + dropped (None)           -> excluded
      decided + kept / imputed           -> use the remediated signal
      not decided + REJECTED             -> excluded (not routable)
      not decided + VALID / FLAGGED      -> included as-is (KEEP)
    """
    rows: List[dict] = []
    for r in results:
        tid = r.raw.telemetry_id
        if tid in decisions:
            final = decisions[tid]
            if final is None:
                continue
            rows.append(curated_row(final))
        else:
            if r.validation_status == ValidationStatus.REJECTED:
                continue
            rows.append(curated_row(r))
    return rows


# ---------------------------------------------------------------------------
# Persistence  —  upsert (not overwrite) so history accumulates
# ---------------------------------------------------------------------------

def upsert_curated(rows: List[dict], path: str = _CURATED) -> str:
    """
    Upsert rows into the curated table keyed on telemetry_id.

    Existing rows for the same telemetry_id are updated (so a re-run after
    a remediation decision change reflects the new decision). New rows are
    appended. Records not present in `rows` are left untouched — nothing is
    deleted from historical runs.

    Returns the file path.

    Postgres equivalent:
        INSERT INTO curated_signals (...) VALUES (...)
        ON CONFLICT (telemetry_id) DO UPDATE SET ...;
    """
    os.makedirs(os.path.dirname(path), exist_ok=True)

    # Load existing records (keyed by telemetry_id for O(1) lookup).
    existing: Dict[str, dict] = {}
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as fh:
            try:
                for rec in json.load(fh):
                    existing[rec["telemetry_id"]] = rec
            except (json.JSONDecodeError, KeyError):
                pass   # treat a corrupt / empty file as empty

    # Apply upsert.
    for row in rows:
        existing[row["telemetry_id"]] = row

    # Write back as an ordered list (insertion / update order).
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(list(existing.values()), fh, indent=2)

    return path


# ---------------------------------------------------------------------------
# Back-compat alias  (old callers used write_curated; keep working)
# ---------------------------------------------------------------------------

def write_curated(rows: List[dict], path: str = _CURATED) -> str:
    """Alias for upsert_curated — preserved so existing call-sites don't break."""
    return upsert_curated(rows, path)
