"""
api/persona_formatter.py  —  Persona-aware output layer

Takes the final DROGraphState and a persona string, and returns a dict
of formatted fields tailored to that persona's concerns and vocabulary.

7 personas (matching the frontend):
  supervisor  — operational decisions, run/defer risk, shift management
  engineer    — FFT analysis, RUL confidence, root cause, KB matches
  maintenance — parts, crew, job pack, schedule, cost
  manager     — production impact, cost/benefit, OEE, contingency
  executive   — portfolio risk, KPIs, ROI, board metrics
  ot          — signal health, historian, CMMS integration
  safety      — LOTO, permits, PPE, ISO compliance
"""
from __future__ import annotations

from typing import Any, Dict, Optional
from src.tools.persona_formatter import build_persona_context

# Internal asset IDs → frontend display labels
_ASSET_DISPLAY = {
    "AST_MTR_001": "M-104",
    "AST_MTR_002": "M-089",
    "AST_PMP_001": "P-207",
    "AST_PMP_002": "C-301",
    "AST_CON_001": "C-301",
    "AST_GBX_001": "G-112",
    "AST_UNKNOWN_001": "UNKNOWN",
}


def _safe_get(obj: Any, *attrs, default="—"):
    """Safely traverse attributes, return default if any is missing."""
    try:
        for attr in attrs:
            obj = getattr(obj, attr)
        return obj if obj is not None else default
    except AttributeError:
        return default


def format_for_persona(state: Dict[str, Any], persona: str) -> Dict[str, Any]:
    """
    Return a persona-tailored summary dict from the pipeline state.
    Keys: greeting, headline, details (list), actions (list), tags (list)
    """
    persona = build_persona_context(persona).id
    trusted   = state.get("trusted_signal")
    anomaly   = state.get("anomaly_event")
    diagnosis = state.get("fault_diagnosis")
    risk      = state.get("risk_assessment")
    knowledge = state.get("knowledge_guidance")
    recommendation = state.get("recommendation")
    log       = state.get("pipeline_log", [])

    # Common fields — fall back to raw signal ID for REJECTED signals (no asset_ctx)
    _internal_id = _safe_get(trusted, "asset_ctx", "asset_id")
    if _internal_id == "—":
        _internal_id = _safe_get(trusted, "raw", "asset_id")
    asset_id = _ASSET_DISPLAY.get(_internal_id, _internal_id if _internal_id != "—" else "UNKNOWN")

    # Short-circuit: REJECTED signals — asset or bearing not in master table
    _vstatus = _safe_get(trusted, "validation_status")
    _vstatus_name = _vstatus.name if hasattr(_vstatus, "name") else str(_vstatus)
    if _vstatus_name == "REJECTED":
        _reasons = getattr(getattr(trusted, "validation", None), "reasons", [])
        _reason_str = _reasons[0] if _reasons else "asset or bearing not found in master table"
        return {
            "persona":   persona,
            "headline":  f"{asset_id} — Signal REJECTED by Data Foundation Agent. Pipeline halted.",
            "details":   [
                f"Rejection reason: {_reason_str}",
                "No downstream analysis was performed.",
                "Register this asset in asset_master.json and bearing_master.json before resubmitting.",
            ],
            "actions":   [
                f"Add {_internal_id} to asset_master.json with correct asset type and criticality",
                "Verify bearing_id and channel_id in bearing_master.json",
                "Resubmit the signal once the asset is registered",
            ],
            "tags":      ["REJECTED", f"Asset:{asset_id}", "DataFoundation"],
            "pipeline_ms": sum(n.get("latency_ms", 0) for n in log),
        }
    bearing_id = _safe_get(trusted, "bearing_ctx", "bearing_id")
    fault_mode = _safe_get(diagnosis, "fault_mode") if diagnosis else "—"
    iso_stage  = _safe_get(diagnosis, "iso_stage", default=0) if diagnosis else 0
    risk_level = _safe_get(risk, "risk_level") if risk else "—"
    rul_band   = _safe_get(risk, "rul_band_label") if risk else "—"
    rul_min    = _safe_get(risk, "rul_min_days", default=0) if risk else 0
    rul_max    = _safe_get(risk, "rul_max_days", default=0) if risk else 0
    confidence = _safe_get(risk, "confidence", default=0.0) if risk else 0.0
    exposure   = _safe_get(risk, "financial_exposure", default=0.0) if risk else 0.0
    fp         = _safe_get(risk, "failure_probability", default=0.0) if risk else 0.0
    health_idx = _safe_get(risk, "health_index", default=1.0) if risk else 1.0
    advisory   = _safe_get(risk, "advisory_note") if risk else ""
    vib        = _safe_get(trusted, "raw", "vib_rms_mm_s") if trusted else "—"
    temp       = _safe_get(trusted, "raw", "temp_c") if trusted else "—"
    severity   = _safe_get(diagnosis, "severity") if diagnosis else "—"

    # SOP sources
    sop_docs   = _safe_get(knowledge, "source_documents", default=[]) if knowledge else []
    steps      = _safe_get(knowledge, "inspection_steps", default=[]) if knowledge else []
    safety_nts = _safe_get(knowledge, "safety_notes", default=[]) if knowledge else []
    loto_ref   = _safe_get(knowledge, "loto_reference") if knowledge else "—"

    # Spectral evidence from FIA (dominant band, thresholds, triggered features)
    evidence   = _safe_get(diagnosis, "evidence", default={}) if diagnosis else {}

    # Pipeline latency
    total_ms = sum(e.get("latency_ms", 0) for e in log)

    formatters = {
        "supervisor": _fmt_supervisor,
        "engineer":   _fmt_engineer,
        "maintenance":_fmt_maintenance,
        "manager":    _fmt_manager,
        "executive":  _fmt_executive,
        "ot":         _fmt_ot,
        "safety":     _fmt_safety,
    }
    fn = formatters.get(persona, _fmt_supervisor)
    return fn(
        asset_id=asset_id, bearing_id=bearing_id,
        fault_mode=fault_mode, iso_stage=iso_stage,
        risk_level=risk_level, rul_band=rul_band,
        rul_min=rul_min, rul_max=rul_max,
        confidence=confidence, exposure=exposure,
        fp=fp, health_idx=health_idx, advisory=advisory,
        vib=vib, temp=temp, severity=severity,
        sop_docs=sop_docs, steps=steps,
        safety_notes=safety_nts, loto_ref=loto_ref,
        evidence=evidence, total_ms=total_ms, state=state,
        recommendation=recommendation,
    )


# ── Per-persona formatters ────────────────────────────────────────────────────

def _fmt_supervisor(*, asset_id, fault_mode, iso_stage, risk_level, rul_band,
                    rul_min, rul_max, fp, exposure, advisory, vib, temp,
                    severity, loto_ref, total_ms, **_) -> Dict:
    urgent = risk_level in ("critical", "high")
    headline = (
        f"{asset_id} — {fault_mode.replace('_', ' ').title()} Stage {iso_stage} — "
        f"{risk_level.upper()} risk. "
        f"RUL {rul_min}–{rul_max} days. Failure probability {fp:.0%}."
    ) if fault_mode != "—" else f"{asset_id} — No fault detected. Asset healthy."
    return {
        "persona": "supervisor",
        "headline": headline,
        "details": [
            f"Risk level: {risk_level.upper()}",
            f"RUL window: {rul_band} ({rul_min}–{rul_max} days)",
            f"Failure probability: {fp:.0%}",
            f"Financial exposure: ${exposure:,.0f}",
            f"Vibration: {vib} mm/s  |  Temperature: {temp} °C",
            *(([f"Advisory: {advisory}"] if advisory else [])),
        ],
        "actions": [
            f"Approve work order for {asset_id} {'rolling-element bearing inspection/replacement' if fault_mode == 'rolling_element_fault' else 'bearing replacement'}" if urgent else
            f"Monitor {asset_id} — schedule inspection this week",
            f"Brief Line operators on {asset_id} symptoms to watch for",
            f"Generate shift handover with {asset_id} as top priority",
        ],
        "tags": [f"{asset_id} · Stage {iso_stage}", f"RUL ~{rul_min}–{rul_max}d",
                 f"Exposure ${exposure/1000:.0f}K"],
        "loto_reference": loto_ref,
        "pipeline_ms": total_ms,
    }


_FAULT_BAND_LABEL = {
    "rolling_element_fault": "BSF",
    "outer_race_spall": "BPFO",
    "inner_race_spall": "BPFI",
    "cage_fault":       "FTF",
}

_FAULT_FFT_ACTION = {
    "rolling_element_fault": "Run BSF harmonic and sideband analysis against bearing geometry",
    "outer_race_spall": "Run full FFT harmonic analysis (BPFO family + sidebands)",
    "inner_race_spall": "Run full FFT harmonic analysis (BPFI family + AM sidebands)",
    "cage_fault":       "Run FTF sub-harmonic analysis (0.4–0.5× shaft harmonics)",
}


def _fmt_engineer(*, asset_id, bearing_id, fault_mode, iso_stage, risk_level,
                  rul_min, rul_max, confidence, fp, health_idx, vib, temp,
                  advisory, sop_docs, steps, evidence, total_ms, **_) -> Dict:
    headline = (
        f"{asset_id}/{bearing_id} — {fault_mode.replace('_',' ')} Stage {iso_stage}. "
        f"Confidence {confidence:.0%}. RUL {rul_min}–{rul_max} d (P10/P90)."
    ) if fault_mode != "—" else f"{asset_id} — No anomalous fault detected."

    # Build spectral evidence lines from FIA output
    ev_lines = []
    if isinstance(evidence, dict):
        dom_band  = evidence.get("dominant_band", "")
        dom_val   = evidence.get("dominant_value", "")
        rule_id   = evidence.get("matched_rule", "")
        kurt_ev   = evidence.get("kurtosis", {})
        triggered = evidence.get("anomaly_triggered_features", [])
        tc        = evidence.get("thresholds_crossed", {})
        if rule_id:
            ev_lines.append(f"Matched rule: {rule_id} · Dominant band: {dom_band} ({dom_val}×)")
        if tc:
            ev_lines.append(
                f"Threshold crossed: Stage {tc.get('iso_stage','')} "
                f"(1×={tc.get('stage_1','?')} 2×={tc.get('stage_2','?')} 3×={tc.get('stage_3','?')})"
            )
        if kurt_ev:
            ev_lines.append(
                f"Kurtosis: {kurt_ev.get('value','?')} "
                f"(threshold {kurt_ev.get('threshold','?')} — "
                f"{'exceeded' if kurt_ev.get('exceeded') else 'below threshold'})"
            )
        if triggered:
            ev_lines.append(f"Anomaly triggered by: {', '.join(triggered)}")

    band_label = _FAULT_BAND_LABEL.get(fault_mode, fault_mode.upper()[:4])
    fft_action = _FAULT_FFT_ACTION.get(fault_mode, "Run full FFT harmonic analysis for dominant fault band")

    return {
        "persona": "engineer",
        "headline": headline,
        "details": [
            f"Fault mode: {fault_mode.replace('_', ' ')} · ISO Stage {iso_stage}",
            f"RUL estimate: {rul_min}–{rul_max} days · Confidence {confidence:.0%}",
            f"Health index: {health_idx:.2f} · Failure probability {fp:.0%}",
            f"Vibration RMS: {vib} mm/s · Temperature: {temp} °C",
            *ev_lines,
            f"Source SOPs: {', '.join(sop_docs) if sop_docs else 'None retrieved'}",
            *(([f"LLM advisory: {advisory}"] if advisory else [])),
        ],
        "inspection_steps": steps,
        "sop_documents": sop_docs,
        "actions": [
            fft_action,
            f"Compute RUL P10/P90 confidence intervals for {asset_id}",
            "Match to KB cases; compare degradation rate vs historical",
            "Define post-repair QA vibration baseline target",
        ],
        "tags": [f"{band_label}·Stage{iso_stage}", f"Conf {confidence:.0%}",
                 f"Health {health_idx:.2f}"],
        "pipeline_ms": total_ms,
    }


def _fmt_maintenance(*, asset_id, fault_mode, iso_stage, risk_level, rul_min,
                     rul_max, exposure, steps, sop_docs, loto_ref, total_ms,
                     **_) -> Dict:
    urgent = risk_level in ("critical", "high")
    headline = (
        f"{asset_id} — {fault_mode.replace('_',' ').title()} Stage {iso_stage}. "
        f"{'Replacement required.' if iso_stage >= 3 else 'Inspection adequate.'} "
        f"RUL {rul_min}–{rul_max} days."
    ) if fault_mode != "—" else f"{asset_id} — Healthy. No immediate maintenance needed."
    return {
        "persona": "maintenance",
        "headline": headline,
        "details": [
            f"Action type: {'Replace bearing' if iso_stage >= 3 else 'Inspect + lubricate'}",
            f"Estimated duration: {'4h' if iso_stage >= 3 else '1.5–2h'}",
            f"LOTO reference: {loto_ref}",
            f"SOP documents: {', '.join(sop_docs) if sop_docs else 'See SOP library'}",
            f"Financial exposure if deferred: ${exposure:,.0f}",
        ],
        "job_steps": steps,
        "sop_documents": sop_docs,
        "actions": [
            f"Confirm parts in stock for {asset_id}",
            "Assign crew and confirm LOTO certification",
            "Print job pack from SOP library",
            f"Book maintenance window (RUL window: {rul_min}–{rul_max} days)",
        ],
        "tags": [f"{asset_id}·{'Replace' if iso_stage>=3 else 'Inspect'}",
                 f"LOTO:{loto_ref}", f"RUL {rul_min}–{rul_max}d"],
        "pipeline_ms": total_ms,
    }


def _fmt_manager(*, asset_id, fault_mode, iso_stage, risk_level, rul_min,
                 rul_max, fp, exposure, total_ms, recommendation=None, **_) -> Dict:
    support = getattr(recommendation, "decision_support", None)
    has_costs = bool(support and support.cost_if_approved is not None
                     and support.cost_if_deferred is not None)
    currency = getattr(support, "currency", "USD") if support else "USD"
    headline = (
        f"Line asset {asset_id} — {risk_level.upper()} risk. "
        + (f"Approve {currency} {support.cost_if_approved:,.0f} planned work "
           f"to avoid {currency} {support.cost_if_deferred:,.0f} deferred exposure."
           if has_costs else "Cost-benefit evaluation is pending approved cost inputs.")
    ) if fault_mode != "—" else f"{asset_id} — No production risk. Asset operating normally."
    return {
        "persona": "manager",
        "headline": headline,
        "details": [
            f"Risk level: {risk_level.upper()} · Failure probability: {fp:.0%}",
            f"RUL window: {rul_min}–{rul_max} days",
            (f"Cost if deferred: {currency} {support.cost_if_deferred:,.0f}"
             if has_costs else "Cost if deferred: unavailable — authoritative cost input pending"),
            (f"Planned intervention cost: {currency} {support.cost_if_approved:,.0f}"
             if has_costs else "Planned intervention cost: unavailable — authoritative cost input pending"),
            (f"Authority: {support.authority_reason}"
             if has_costs else "ROI and authority check: not evaluated"),
        ],
        "actions": [
            f"Approve WO for {asset_id} today (before RUL window expires)",
            "Confirm production plan adjustment for maintenance window",
            "Review contingency if repair overruns",
        ],
        "tags": ["Configured demo cost" if has_costs else "Cost data pending",
                 f"RUL {rul_min}–{rul_max}d"],
        "pipeline_ms": total_ms,
    }


def _fmt_executive(*, asset_id, fault_mode, risk_level, fp, exposure,
                   health_idx, total_ms, recommendation=None, **_) -> Dict:
    support = getattr(recommendation, "decision_support", None)
    has_costs = bool(support and support.cost_if_approved is not None
                     and support.cost_if_deferred is not None)
    currency = getattr(support, "currency", "USD") if support else "USD"
    headline = (
        f"Portfolio alert: {asset_id} requires review. "
        + (f"Configured exposure is {currency} {support.cost_if_deferred:,.0f} "
           f"versus {currency} {support.cost_if_approved:,.0f} planned action."
           if has_costs else "Financial return is pending approved cost inputs.")
    ) if fault_mode != "—" else f"{asset_id} — Healthy. No leadership action required."
    return {
        "persona": "executive",
        "headline": headline,
        "details": [
            f"Risk level: {risk_level.upper()} · Failure probability: {fp:.0%}",
            (f"Financial exposure: {currency} {support.cost_if_deferred:,.0f}"
             if has_costs else "Financial exposure: unavailable — authoritative cost input pending"),
            (f"Planned action cost: {currency} {support.cost_if_approved:,.0f}"
             if has_costs else "Planned action cost: unavailable — authoritative cost input pending"),
            (f"Authority: {support.authority_reason}"
             if has_costs else "ROI and authority check: not evaluated"),
            f"Asset health index: {health_idx:.2f}",
        ],
        "actions": [
            f"Review operational recommendation for {asset_id}",
            "Review DRO YTD avoidance dashboard",
            "Add to board briefing if exposure > $500K",
        ],
        "tags": ["Configured demo cost" if has_costs else "Cost data pending",
                 f"Health {health_idx:.2f}"],
        "pipeline_ms": total_ms,
    }


def _fmt_ot(*, asset_id, bearing_id, vib, temp, fault_mode, confidence,
            sop_docs, total_ms, state, **_) -> Dict:
    trusted = state.get("trusted_signal")
    quality = _safe_get(trusted, "quality_report", "overall_score", default=None)
    status  = _safe_get(trusted, "validation_status", default="UNKNOWN")
    status_name = status.name if hasattr(status, "name") else str(status)
    headline = (
        f"{asset_id} signal status: {status_name}. "
        f"Quality score: {quality:.2f}" if quality else
        f"{asset_id} signal status: {status_name}."
    )
    return {
        "persona": "ot",
        "headline": headline,
        "details": [
            f"Validation status: {status_name}",
            f"Data quality score: {quality:.2f}" if quality else "Data quality: N/A",
            f"Vibration RMS: {vib} mm/s · Temperature: {temp} °C",
            f"Fault detected: {fault_mode.replace('_',' ')} · Conf {confidence:.0%}" if fault_mode != "—" else "No fault detected",
            f"Referenced SOPs: {', '.join(sop_docs) if sop_docs else 'None'}",
        ],
        "actions": [
            f"Validate all 4 channels on {asset_id} are nominal",
            "Check historian coverage for last 48h",
            "Confirm CMMS record is current",
            "Set post-repair historian baseline after maintenance",
        ],
        "tags": [f"Status:{status_name}", f"Quality:{quality:.2f}" if quality else "Quality:N/A",
                 f"Vib:{vib}mm/s"],
        "pipeline_ms": total_ms,
    }


def _fmt_safety(*, asset_id, fault_mode, iso_stage, risk_level, loto_ref,
                safety_notes, total_ms, **_) -> Dict:
    zone = "D" if risk_level in ("critical", "high") else "C" if risk_level == "medium" else "A/B"
    headline = (
        f"{asset_id} — ISO 10816-3 Zone {zone}. "
        f"LOTO required: {loto_ref or 'verify'}. "
        f"{'Mandatory isolation — no approach without LOTO.' if zone=='D' else 'Standard PPE applies.'}"
    ) if fault_mode != "—" else f"{asset_id} — Zone A/B. No mandatory isolation required."
    return {
        "persona": "safety",
        "headline": headline,
        "details": [
            f"ISO 10816-3 Zone: {zone}",
            f"LOTO reference: {loto_ref or 'Verify with maintenance lead'}",
            f"Risk level: {risk_level.upper()} · Stage {iso_stage}",
            *safety_notes[:4],
        ],
        "actions": [
            f"Validate LOTO {loto_ref} is current and in scope",
            "Verify crew LOTO certification before dispatch",
            "Issue pre-task safety brief",
            "Log safety compliance record after job closure",
        ],
        "tags": [f"Zone {zone}", f"LOTO:{loto_ref or '?'}", f"Stage {iso_stage}"],
        "pipeline_ms": total_ms,
    }
