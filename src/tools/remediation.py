"""
tools/remediation.py

The decision point that sits AFTER the Data Foundation Agent and BEFORE the
Monitoring Agent. The DFA only diagnoses quality; this module acts on a
FLAGGED record by either imputing missing fields, dropping the record, or
keeping it as-is.

Design notes
------------
* The DFA stays pure — it never mutates data. All filling-in happens here.
* The *action* (IMPUTE / DROP / KEEP) is an argument. Today it is supplied by
  the config policy or a CLI prompt; later a frontend button supplies the same
  value. The decision point does not change — only who answers it.
* After imputing, the record is re-run through the DFA so the quality score
  reflects the fix and there is a clean audit trail. Imputed fields are
  recorded on the result so downstream agents can lower their confidence.
* A field is imputable ONLY if it has a baseline in config.imputable_fields.
  Identity fields (config.never_impute_fields) can never be imputed.

Usage
-----
    from src.tools.remediation import decide_action, remediate, IMPUTE, DROP

    action = decide_action(trusted, cfg)        # policy default, or...
    action = IMPUTE                              # ...a human / frontend choice
    fixed  = remediate(trusted, action, agent, cfg)   # None if dropped
"""

from __future__ import annotations

from typing import List, Optional

from src.schemas.bearing_signal import TrustedBearingSignal, ValidationStatus
from src.tools.config_loader import DFAConfig

# ── Action constants ────────────────────────────────────────────────────────
IMPUTE          = "IMPUTE"
DROP            = "DROP"
KEEP            = "KEEP"
FLAG_FOR_REVIEW = "FLAG_FOR_REVIEW"   # "a human must decide" — not a final action


# ---------------------------------------------------------------------------
# Inspection — what is missing, and what can we actually fix?
# ---------------------------------------------------------------------------

def missing_critical_fields(trusted: TrustedBearingSignal, cfg: DFAConfig) -> List[str]:
    """Critical signal fields that arrived empty (null) on this record."""
    return [
        f for f in cfg.critical_signal_fields
        if getattr(trusted.raw, f, None) is None
    ]


def imputable_fields(trusted: TrustedBearingSignal, cfg: DFAConfig) -> List[str]:
    """
    The subset of missing fields we can actually impute — i.e. fields that
    have a baseline in config AND a baseline value available on this bearing.
    A field with no baseline (e.g. bpfi_energy) is reported as missing but is
    NOT imputable, so the UI must offer only DROP / KEEP for it.
    """
    bctx = trusted.bearing_ctx
    if bctx is None:
        return []
    out = []
    for f in missing_critical_fields(trusted, cfg):
        if f in cfg.never_impute_fields:
            continue
        baseline_attr = cfg.imputable_fields.get(f)
        if baseline_attr and getattr(bctx, baseline_attr, None) is not None:
            out.append(f)
    return out


# ---------------------------------------------------------------------------
# Policy — what action would we take with no human present?
# ---------------------------------------------------------------------------

def decide_action(trusted: TrustedBearingSignal, cfg: DFAConfig) -> str:
    """
    The unattended/default decision. A human (CLI or frontend) may override it.

      VALID    -> KEEP   (nothing to fix)
      REJECTED -> DROP   (identity missing — cannot be fixed)
      FLAGGED  -> config default, but only meaningful if something is imputable;
                  otherwise KEEP (let it flow with its quality flag).
    """
    if trusted.validation_status == ValidationStatus.VALID:
        return KEEP
    if trusted.validation_status == ValidationStatus.REJECTED:
        return DROP
    # FLAGGED
    if imputable_fields(trusted, cfg):
        return cfg.remediation_default_action
    return KEEP


# ---------------------------------------------------------------------------
# Imputation — fill missing fields from the per-bearing baseline mean
# ---------------------------------------------------------------------------

def _impute_patched_dict(trusted, cfg, fields):
    """
    Build a raw-record dict with the requested fields filled from baseline
    means. Returns (patched_dict, [fields_actually_filled]).
    """
    patched = trusted.raw.to_dict()
    filled = []
    bctx = trusted.bearing_ctx
    if bctx is None:
        return patched, filled

    targets = fields if fields is not None else imputable_fields(trusted, cfg)
    for f in targets:
        if f in cfg.never_impute_fields:
            continue
        baseline_attr = cfg.imputable_fields.get(f)
        if not baseline_attr:
            continue
        value = getattr(bctx, baseline_attr, None)
        if value is not None and patched.get(f) is None:
            patched[f] = value
            filled.append(f)
    return patched, filled


# ---------------------------------------------------------------------------
# The entry point
# ---------------------------------------------------------------------------

def remediate(
    trusted: TrustedBearingSignal,
    action:  str,
    agent,                       # DataFoundationAgent — used to re-validate
    cfg:     DFAConfig,
    fields:  Optional[List[str]] = None,
) -> Optional[TrustedBearingSignal]:
    """
    Apply a remediation decision to one record.

      DROP   -> returns None (caller removes the record; log it, never silent).
      KEEP   -> returns the record unchanged, tagged with the KEEP action.
      IMPUTE -> fills `fields` (or all imputable fields) from baseline means,
                RE-RUNS the DFA on the patched record, and tags the new result
                with the imputed fields + method.

    Returns None only for DROP. For IMPUTE with nothing actually fillable,
    falls back to KEEP so the record is never silently lost.
    """
    action = (action or "").upper()

    if action == DROP:
        return None

    if action == KEEP or action == FLAG_FOR_REVIEW:
        # Don't clobber a prior action — a record that was IMPUTED and then
        # kept should still read as IMPUTE in its provenance.
        if not trusted.remediation_action:
            trusted.remediation_action = KEEP
        return trusted

    if action == IMPUTE:
        patched, filled = _impute_patched_dict(trusted, cfg, fields)
        if not filled:
            # Nothing was imputable — don't lose the record, just keep it.
            trusted.remediation_action = KEEP
            return trusted
        result = agent.process(patched)          # re-validate the patched record
        result.remediation_action = IMPUTE
        result.imputed_fields     = filled
        result.imputation_method  = cfg.auto_impute_method
        return result

    raise ValueError(
        f"Unknown remediation action '{action}'. "
        f"Expected one of: {IMPUTE}, {DROP}, {KEEP}, {FLAG_FOR_REVIEW}"
    )
