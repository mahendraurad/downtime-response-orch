"""
LLM-based learned case generator for the Learning & Memory Agent.

Generates a complete case document matching the DRO case template:
  Section 1: Detection Summary
  Section 2: Agent Diagnosis
  Section 3: Action Taken
  Section 4: Findings at Inspection
  Section 5: Outcome
  Section 6: Root Cause Confirmed
  Section 7: Lessons Learned

The manager showed reference PDFs (CASE_001 through CASE_006) as
the exact output format this agent must produce.

LLM is used ONLY for:
  - Generating narrative text (findings, lessons learned)
  - Filling template sections from structured pipeline data

LLM is NOT used for:
  - Routing decisions (LangGraph handles that)
  - Validation (Pydantic handles that)
  - Storage (memory_storage_node handles that)
"""

import json
import logging
from datetime import datetime, timezone
from typing import Optional, List

from langfuse import observe

# LLM parameters — configurable via config.json
try:
    import json as _cg_json
    from pathlib import Path as _CgPath
    _cg_file = _CgPath(__file__).resolve().parents[1] / "config.json"
    _cg_cfg = _cg_json.loads(_cg_file.read_text(encoding="utf-8")) if _cg_file.exists() else {}
    _CG_MAX_TOKENS = _cg_cfg.get("azure_openai", {}).get("max_tokens", 1500)
except Exception:
    _CG_MAX_TOKENS = 1500

# Langfuse-patched Azure OpenAI SDK for auto-instrumented calls
try:
    from langfuse.openai import AzureOpenAI as _LangfuseAzureOpenAI
    _AZURE_OPENAI_CLIENT_CLS = _LangfuseAzureOpenAI
except Exception:
    from openai import AzureOpenAI as _RawAzureOpenAI
    _AZURE_OPENAI_CLIENT_CLS = _RawAzureOpenAI

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(module)s | %(message)s"
)
logger = logging.getLogger(__name__)


def build_case_generation_prompt(feedback: dict) -> str:
    """
    Builds the LLM prompt to generate a complete case document.

    The prompt references the 7-section template from the reference
    PDFs that the manager showed during the review discussion.
    Returns JSON only - no markdown, no preamble.
    """
    return f"""You are a reliability engineering knowledge system for
bearing failure predictive maintenance in the DRO pipeline.

Generate a complete learned case document from this confirmed
maintenance event. This document will be stored in the vector
database and retrieved by the Knowledge Agent for future incidents.

Return ONLY valid JSON. No markdown fences. No preamble.

Pipeline data:
- Case ID: {feedback.get('case_id', 'CASE_UNKNOWN')}
- Asset ID: {feedback.get('asset_id', 'unknown')}
- Asset type: {feedback.get('asset_type', 'unknown')}
- Bearing type: {feedback.get('bearing_type', 'unknown')}
- Fault mode confirmed: {feedback.get('fault_mode', 'unknown')}
- Fault stage: {feedback.get('fault_stage', 'unknown')}
- Anomaly score: {feedback.get('anomaly_score', 'unknown')}
- Vibration RMS: {feedback.get('vib_rms_mm_s', 'unknown')} mm/s
- Kurtosis: {feedback.get('kurtosis', 'unknown')}
- Temperature: {feedback.get('temp_c', 'unknown')} C
- BPFO energy: {feedback.get('bpfo_energy', 'unknown')}x baseline
- Signal quality: {feedback.get('signal_quality_score', 'unknown')}
- Diagnosis confidence: {feedback.get('diagnosis_confidence', 'unknown')}
- Diagnosis reasoning: {feedback.get('diagnosis_reasoning', 'unknown')}
- Risk level: {feedback.get('risk_level', 'unknown')}
- Failure probability: {feedback.get('failure_probability', 'unknown')}
- RUL estimate: {feedback.get('rul_days_min','?')}-{feedback.get('rul_days_max','?')} days
- Recommended action: {feedback.get('recommended_action', 'unknown')}
- Work order ID: {feedback.get('work_order_id', 'unknown')}
- Root cause confirmed: {feedback.get('root_cause', 'unknown')}
- Action taken: {feedback.get('action_taken', 'unknown')}
- Post repair vib: {feedback.get('post_repair_vib_rms', 'unknown')} mm/s
- Post repair temp: {feedback.get('post_repair_temp_c', 'unknown')} C
- Post repair result: {feedback.get('post_repair_result', 'unknown')}
- Technician notes: {feedback.get('technician_notes', 'none')}

Return this exact JSON structure with all 7 sections:
{{
  "case_title": "Short descriptive title like 'Stage 3 Outer Race Fault Replaced — [Asset Name]'",
  "outcome_status": "SUCCESSFUL or PENDING or PREVENTIVE",
  "detection_summary": {{
    "detection_date": "date string",
    "detected_by": "which agent detected this",
    "anomaly_score": "value with context",
    "vib_rms_mm_s": "value with baseline comparison",
    "kurtosis": "value with threshold comparison",
    "temp_c": "value with baseline and rise",
    "bpfo_energy": "value with stage multiple",
    "signal_quality_score": "value with interpretation",
    "summary_note": "1 sentence describing the overall detection picture"
  }},
  "agent_diagnosis": {{
    "primary_diagnosis": "fault mode and stage",
    "confidence": "confidence value",
    "reasoning": "2-3 sentences explaining the diagnostic reasoning",
    "differential_considered": "what other faults were ruled out and why",
    "rul_estimate": "RUL range with context"
  }},
  "action_taken": {{
    "recommended_action": "what was recommended",
    "work_order_id": "WO reference",
    "parts_required": "parts used",
    "action_performed": "what was actually done",
    "crew_notes": "any crew or LOTO notes"
  }},
  "findings_at_inspection": "2-3 sentence narrative paragraph describing what the technician physically found when they opened the bearing or performed inspection. Reference specific physical observations.",
  "outcome": {{
    "post_repair_vib_rms": "value with QA criterion",
    "post_repair_temp_c": "value with QA criterion",
    "qa_result": "PASS or FAIL with brief reason",
    "recommendation_followed": "Yes or No"
  }},
  "root_cause_confirmed": {{
    "root_cause": "confirmed root cause",
    "contributing_factors": "what contributed to this fault developing"
  }},
  "lessons_learned": [
    "Specific actionable lesson 1 with measurable detail",
    "Specific actionable lesson 2 about detection or timing",
    "Specific actionable lesson 3 about repair procedure or parts",
    "Specific actionable lesson 4 about post-repair baseline"
  ]
}}"""


@observe()
def generate_case_from_pipeline(
    feedback: dict,
    azure_openai_key: str = "",
    azure_openai_endpoint: str = "",
    azure_openai_deployment: str = "gpt-4",
    azure_openai_api_version: str = "2024-08-01-preview"
) -> dict:
    """
    Generates the complete 7-section case document.

    Dev mode: builds structured content from actual pipeline data
    without calling the LLM. All values come from real feedback fields.

    Prod mode: calls Azure OpenAI with the full template prompt.
    """

    if not azure_openai_key:
        logger.info(
            "Dev mode: generating case document from pipeline data | "
            "fault=%s asset=%s",
            feedback.get('fault_mode'), feedback.get('asset_id')
        )

        fault = feedback.get('fault_mode', 'unknown')
        asset_id = feedback.get('asset_id', 'unknown')
        asset_type = feedback.get('asset_type', 'unknown')
        bearing = feedback.get('bearing_type', 'unknown')
        stage = feedback.get('fault_stage', 0)
        vib = feedback.get('vib_rms_mm_s', 0)
        kurt = feedback.get('kurtosis', 0)
        temp = feedback.get('temp_c', 0)
        bpfo = feedback.get('bpfo_energy', 0)
        sq = feedback.get('signal_quality_score', 0)
        confidence = feedback.get('diagnosis_confidence', 0)
        reasoning = feedback.get('diagnosis_reasoning', 'N/A')
        risk = feedback.get('risk_level', 'unknown')
        fp = feedback.get('failure_probability', 0)
        rul_min = feedback.get('rul_days_min', 0)
        rul_max = feedback.get('rul_days_max', 0)
        rec_action = feedback.get('recommended_action', 'Investigation required')
        wo_id = (
            f"WO_{feedback.get('run_id', 'NEW')[:8].upper()} "
            f"priority {feedback.get('work_order_priority', 'MEDIUM')}"
        )
        root_cause = feedback.get('root_cause', 'To be confirmed at inspection')
        action = feedback.get('action_taken', feedback.get('recommended_action', 'unknown'))
        post_vib = feedback.get('post_repair_vib_rms', 'not measured')
        post_temp = feedback.get('post_repair_temp_c', 'not measured')
        post_result = feedback.get('post_repair_result', 'pending')
        notes = feedback.get(
            'technician_observations',
            feedback.get('technician_notes',
                f"Stage {stage} {fault} on {asset_id}. "
                f"Vibration {vib} mm/s, kurtosis {kurt}, "
                f"temp {temp}C. Recommended action per SOP.")
        )
        parts = feedback.get('parts_required', feedback.get('parts_used', 'TBD at inspection'))
        sop = feedback.get('sop_reference', 'Custom procedure')
        duration = feedback.get('estimated_duration_hr', 4)
        lessons = feedback.get('lessons_learned', 'To be captured post-repair')

        # Build case title from available data
        fault_label = fault.replace('_', ' ').title()
        title = (
            f"Stage {stage} {fault_label} — "
            f"{asset_type.title()} {asset_id}"
        )

        # Determine outcome status
        if post_result == "pass":
            outcome_status = "SUCCESSFUL"
        elif post_result == "pending":
            outcome_status = "PENDING"
        else:
            outcome_status = "IN PROGRESS"

        return {
            "case_title": title,
            "outcome_status": outcome_status,
            "detection_summary": {
                "detection_date": feedback.get(
                    'created_at',
                    datetime.now(timezone.utc).strftime('%Y-%m-%d')
                )[:10],
                "detected_by": "monitoring_agent",
                "anomaly_score": f"{feedback.get('anomaly_score', 0):.2f}",
                "vib_rms_mm_s": f"{vib} mm/s",
                "kurtosis": (
                    f"{kurt} "
                    f"({'exceeded' if kurt > 5.0 else 'below'} "
                    f"fault threshold 5.0)"
                ),
                "temp_c": f"{temp}C",
                "bpfo_energy": f"{bpfo}x baseline",
                "signal_quality_score": f"{sq} (clean signal)",
                "summary_note": (
                    f"{fault_label} detected on {bearing} bearing "
                    f"at Stage {stage} with anomaly score "
                    f"{feedback.get('anomaly_score', 0):.2f}."
                )
            },
            "agent_diagnosis": {
                "primary_diagnosis": f"{fault} — Stage {stage}",
                "confidence": f"{confidence:.0%}",
                "reasoning": reasoning,
                "differential_considered": (
                    f"Risk level {risk}. Failure probability "
                    f"{fp:.0f}%. RUL {rul_min}-{rul_max} days."
                ),
                "rul_estimate": f"{rul_min}–{rul_max} days from detection"
            },
            "action_taken": {
                "recommended_action": rec_action,
                "work_order_id": wo_id,
                "parts_required": parts,
                "estimated_duration": f"{duration} hours",
                "sop_reference": sop,
                "action_performed": action,
                "crew_notes": notes
            },
            "findings_at_inspection": (
                f"Inspection of {bearing} bearing on {asset_type} "
                f"{asset_id} confirmed {fault}. "
                f"{notes} "
                f"Root cause preliminary assessment: {root_cause.rstrip('.')}"
            ),
            "outcome": {
                "expected_post_vib_max": feedback.get(
                    "expected_post_vib_max", 3.0),
                "expected_post_temp_max": feedback.get(
                    "expected_post_temp_max", 60),
                "expected_post_kurtosis_max": feedback.get(
                    "expected_post_kurtosis_max", 3.0),
                "qa_window": feedback.get(
                    "qa_window", "post-repair"),
                "recommendation_followed": "Pending repair completion",
            },
            "root_cause_confirmed": {
                "root_cause": root_cause,
                "contributing_factors": (
                    f"{fault} on {bearing} detected at Stage {stage}. "
                    f"Anomaly signature consistent with {root_cause}"
                )
            },
            "lessons_learned": [
                lessons,
                (
                    f"Detection signature: vib {vib} mm/s, kurtosis {kurt}, "
                    f"BPFO {bpfo}x baseline. "
                    f"RUL from detection: {rul_min}-{rul_max} days."
                ),
                (
                    f"Risk level {risk} with {fp:.0f}% failure probability "
                    f"justified {rec_action}. "
                    f"Recommended response per {sop}."
                ),
                (
                    "Post-repair baseline will be established after repair "
                    "completion. Expected post-repair: vib below baseline "
                    f"+ 0.5 mm/s, temp within ±2C of baseline. "
                    f"Reference asset: {asset_type} with {bearing} bearing."
                )
            ]
        }

    # Prod mode: real LLM call
    try:
        # Uses _AZURE_OPENAI_CLIENT_CLS resolved at module import time
        client = _AZURE_OPENAI_CLIENT_CLS(
            api_key=azure_openai_key,
            azure_endpoint=azure_openai_endpoint,
            api_version=azure_openai_api_version
        )
        prompt = build_case_generation_prompt(feedback)
        response = client.chat.completions.create(
            model=azure_openai_deployment,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=_CG_MAX_TOKENS
        )
        raw = response.choices[0].message.content
        clean = raw.replace("```json","").replace("```","").strip()
        result = json.loads(clean)
        logger.info(
            "LLM case generation successful | fault=%s",
            feedback.get('fault_mode')
        )
        return result
    except Exception as e:
        logger.error("LLM call failed: %s — using dev mode", e)
        return generate_case_from_pipeline(feedback)


def build_learned_case_object(feedback: dict, generated: dict) -> dict:
    """
    Combines FeedbackEvent and generated case content into a complete
    LearnedCase-compatible dict ready for Pydantic validation and storage.
    """
    lessons = generated.get("lessons_learned", [])
    if isinstance(lessons, list):
        lessons_text = " | ".join(str(l) for l in lessons)
    else:
        lessons_text = str(lessons)

    return {
        "case_id": feedback.get("case_id", "CASE_UNKNOWN"),
        "asset_type": feedback.get("asset_type", "unknown"),
        "bearing_type": feedback.get("bearing_type", "unknown"),
        "fault_mode": feedback.get("fault_mode", "unknown"),
        "root_cause": (
            generated.get("root_cause_confirmed", {})
                     .get("root_cause",
                          feedback.get("root_cause", "unknown"))
        ),
        "action_taken": (
            generated.get("action_taken", {})
                     .get("action_performed",
                          feedback.get("action_taken", "unknown"))
        ),
        "result": feedback.get("result", "unknown"),
        "lessons_learned": lessons_text,
        "valid_until": feedback.get("valid_until", "2028-12-31"),
        "created_by": "pipeline",
        "case_title": generated.get("case_title", ""),
        "outcome_status": generated.get("outcome_status", ""),
        "detection_summary": generated.get("detection_summary", {}),
        "agent_diagnosis": generated.get("agent_diagnosis", {}),
        "action_taken_detail": generated.get("action_taken", {}),
        "findings_at_inspection": generated.get(
            "findings_at_inspection", ""
        ),
        "outcome": generated.get("outcome", {}),
        "root_cause_confirmed": generated.get(
            "root_cause_confirmed", {}
        )
    }


def render_case_as_markdown(
    case_id: str,
    feedback: dict,
    generated: dict
) -> str:
    """
    Renders the complete case document as a markdown string.
    This is the document file your manager showed in the PDFs —
    saved to data/learned_cases/ so the Knowledge Agent can read it.

    Format matches the reference PDFs exactly:
    Header, then 7 numbered sections.
    """
    ds = generated.get("detection_summary", {})
    ad = generated.get("agent_diagnosis", {})
    at = generated.get("action_taken", {})
    findings = generated.get("findings_at_inspection", "")
    outcome = generated.get("outcome", {})
    rc = generated.get("root_cause_confirmed", {})
    lessons = generated.get("lessons_learned", [])
    title = generated.get(
        "case_title",
        f"{feedback.get('fault_mode','').replace('_',' ').title()} — "
        f"{feedback.get('asset_id','')}"
    )
    outcome_status = generated.get("outcome_status", "UNKNOWN")
    today = datetime.now(timezone.utc).strftime('%Y-%m-%d')

    expected_vib_max = feedback.get(
        "expected_post_vib_max",
        outcome.get("expected_post_vib_max", 3.0))
    expected_temp_max = feedback.get(
        "expected_post_temp_max",
        outcome.get("expected_post_temp_max", 60))
    expected_kurt_max = feedback.get(
        "expected_post_kurtosis_max",
        outcome.get("expected_post_kurtosis_max", 3.0))
    qa_window = feedback.get(
        "qa_window",
        outcome.get("qa_window", "post-repair"))

    if isinstance(lessons, list):
        lessons_md = "\n".join(f"- {l}" for l in lessons)
    else:
        lessons_md = f"- {lessons}"

    return f"""# {title}

**Asset:** {feedback.get('asset_id','')} ({feedback.get('asset_type','')}) | **Bearing:** {feedback.get('bearing_type','')}
**Fault Mode:** {feedback.get('fault_mode','')} | **Stage:** {feedback.get('fault_stage','')}
**Learned Case — Knowledge Agent Retrieval Document | DRO Pipeline**

**{case_id}** | Outcome: {outcome_status} | Generated: {today}

---

## 1. Detection Summary

| Field | Value |
|---|---|
| Detection date | {ds.get('detection_date', today)} |
| Detected by | {ds.get('detected_by', 'monitoring_agent')} |
| Anomaly score | {ds.get('anomaly_score', '')} |
| Vibration RMS | {ds.get('vib_rms_mm_s', '')} |
| Kurtosis | {ds.get('kurtosis', '')} |
| Temperature | {ds.get('temp_c', '')} |
| BPFO energy | {ds.get('bpfo_energy', '')} |
| Signal quality | {ds.get('signal_quality_score', '')} |

{ds.get('summary_note', '')}

---

## 2. Agent Diagnosis

| Field | Value |
|---|---|
| Primary diagnosis | {ad.get('primary_diagnosis', '')} |
| Confidence | {ad.get('confidence', '')} |
| RUL estimate | {ad.get('rul_estimate', '')} |

**Reasoning:** {ad.get('reasoning', '')}

**Differential considered:** {ad.get('differential_considered', '')}

---

## 3. Action Taken

| Field | Value |
|---|---|
| Recommended action | {at.get('recommended_action', '')} |
| Work order | {at.get('work_order_id', '')} |
| Parts required | {at.get('parts_required', '')} |
| Estimated duration | {at.get('estimated_duration', '')} |
| SOP reference | {at.get('sop_reference', '')} |
| Action performed | {at.get('action_performed', '')} |

{at.get('crew_notes', '')}

---

## 4. Findings at Inspection

{findings}

---

## 5. Expected Outcome

*Repair has not yet been performed. These are the acceptance criteria from SOP_006 for marking this repair successful when complete.*

| Field | Acceptance Criterion |
|---|---|
| Post-repair vibration target | ≤ {expected_vib_max} mm/s |
| Post-repair temperature target | ≤ {expected_temp_max}°C |
| Post-repair kurtosis target | ≤ {expected_kurt_max} |
| QA check window | {qa_window} |
| Status | NEW — awaiting repair |

---

## 6. Root Cause Confirmed

**Root cause:** {rc.get('root_cause', '')}

**Contributing factors:** {rc.get('contributing_factors', '')}

---

## 7. Lessons Learned

{lessons_md}
"""
