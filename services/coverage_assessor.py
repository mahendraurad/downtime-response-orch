"""
Coverage Assessor — LLM-Based Knowledge Dimension Scoring
==========================================================

WHAT THIS FILE DOES
-------------------
Scores an incoming maintenance case against 5 knowledge dimensions to
decide whether the pipeline should retrieve an existing case (PATH_A),
generate a new case (PATH_B), or propose an enrichment (PATH_C).

The 5 dimensions scored:
  1. signal_signature         — vibration, temperature, kurtosis, BPFO/BPFI
  2. diagnosis_and_reasoning  — fault mode, confidence, reasoning chain
  3. action_and_outcome       — recommended action, SOP, parts, duration
  4. root_cause_and_factors   — primary cause, contributing factors
  5. lessons_and_future_reference — key lesson, prevention

Each dimension is scored one of:
  - COMPLETE  — matched case has this dimension fully covered
  - PARTIAL   — matched case has some but not all fields
  - MISSING   — matched case lacks this dimension entirely

The results drive routing via knowledge_state:
  - EXISTING  — all 5 COMPLETE and similarity >= threshold  -> PATH_A
  - PARTIAL   — at least 1 PARTIAL/MISSING with identity match -> PATH_C
  - NEW       — no similar candidate above threshold        -> PATH_B

WHAT IT USES
------------
Third-party libraries:
  - langchain_openai        — LLM client (Azure OpenAI)
  - langchain_core.messages — HumanMessage, SystemMessage
  - langfuse                — @observe decorator for automatic tracing

TECHNIQUES APPLIED
------------------
  - LLM prompt engineering with structured JSON output
  - Langfuse @observe decorator wraps the assessment call — creates a
    trace span automatically with prompt/response/tokens/cost
  - Concurrent execution via concurrent.futures for multi-dimension
    scoring in a single LLM call

CALLED BY
---------
  - orchestrator/langgraph_flow.py::coverage_assessment_node

CALLS INTO
----------
  - LLM (Azure OpenAI gpt-4o-mini via langchain_openai)

CONFIGURATION
-------------
Uses the same LLM client and Langfuse handler initialized in the main
Streamlit app. LLM temperature and max_tokens read from config.json.

KNOWN LIMITATIONS
-----------------
LLM-based scoring is non-deterministic — borderline cases may score
differently across runs. Documented in README.md Section 11 as
"LLM-scored coverage" with a proposal to add numerical similarity
scoring alongside for stability.
"""

import concurrent.futures as _cf
import json
import re
from typing import Optional

from langfuse import observe

KNOWLEDGE_DIMENSIONS = [
    "signal_signature",
    "diagnosis_and_reasoning",
    "action_and_outcome",
    "root_cause_and_factors",
    "lessons_and_future_reference",
]


def build_coverage_prompt(feedback: dict,
                          case_content: dict) -> str:
    """Build the LLM prompt for coverage assessment."""
    fault_mode = feedback.get("fault_mode", "unknown")
    asset_type = feedback.get("asset_type", "unknown")
    bearing_type = feedback.get("bearing_type", "unknown")
    fault_stage = feedback.get("fault_stage",
                               feedback.get("stage", "unknown"))
    diag_reasoning = feedback.get(
        "diagnosis_reasoning", "not provided")
    root_cause = feedback.get("root_cause",
                              "not yet confirmed")
    action_taken = feedback.get("action_taken",
                                "not yet determined")

    case_id = case_content.get("case_id", "unknown")
    c_fault = case_content.get("fault_mode", "unknown")
    c_asset = case_content.get("asset_type", "unknown")
    c_bearing = case_content.get("bearing_type", "unknown")
    c_root = case_content.get("root_cause", "unknown")
    c_action = case_content.get("action_taken", "unknown")
    c_result = case_content.get("result", "unknown")
    c_lessons = case_content.get("lessons_learned", "")
    has_detection = bool(case_content.get(
        "detection_summary"))
    has_diagnosis = bool(case_content.get(
        "agent_diagnosis"))

    system_prompt = (
        "You are a knowledge coverage assessor for "
        "industrial predictive maintenance. "
        "Your task is to evaluate whether a retrieved "
        "knowledge base case provides sufficient coverage "
        "for an incoming maintenance incident. "
        "Evaluate 5 knowledge dimensions. "
        "Return ONLY valid JSON. No preamble. No markdown.\n\n"
        "ROUTING CONSTRAINTS (apply in this strict order):\n\n"
        "1. FIRST CHECK: Does the retrieved case have the EXACT SAME "
        "fault_mode as the incoming fault?\n"
        "   - If NO (e.g. retrieved=cage_fault but incoming=misalignment, "
        "or retrieved=outer_race_fault but incoming=inner_race_fault): "
        "return NEW. Set covered_dimensions to []. Do not claim partial "
        "coverage. The retrieval is a similarity score, not a fault match.\n"
        "   - If YES: proceed to step 2.\n\n"
        "2. SECOND CHECK (only if fault_modes match): Are all 5 dimensions "
        "concretely populated in the retrieved case?\n"
        "   - Concretely populated means: not \"unknown\", not \"to be "
        "confirmed\", not empty, not a generic placeholder.\n"
        "   - If YES to all 5: return EXISTING.\n"
        "   - If NO (one or more dimensions are missing/vague/unknown): "
        "return PARTIAL.\n\n"
        "3. NO CANDIDATE RETRIEVED: return NEW.\n\n"
        "Do NOT return PARTIAL when fault_modes differ. A cage_fault case "
        "provides ZERO coverage for a misalignment fault, even if both have "
        "\"action_taken\" text — the actions are unrelated."
    )

    user_prompt = f"""INCOMING INCIDENT:
fault_mode: {fault_mode}
asset_type: {asset_type}
bearing_type: {bearing_type}
fault_stage: {fault_stage}
diagnosis_reasoning: {diag_reasoning}
root_cause: {root_cause}
action_taken: {action_taken}

RETRIEVED KNOWLEDGE BASE CASE:
case_id: {case_id}
fault_mode: {c_fault}
asset_type: {c_asset}
bearing_type: {c_bearing}
root_cause: {c_root}
action_taken: {c_action}
result: {c_result}
lessons_learned: {c_lessons}
detection_summary_present: {has_detection}
agent_diagnosis_present: {has_diagnosis}

ASSESSMENT TASK:
For each of the 5 knowledge dimensions below, evaluate \
whether the retrieved case provides COMPLETE, PARTIAL, \
or MISSING coverage for the incoming incident.

Rules:
- COMPLETE: information is present, verified, \
and applicable to this specific asset type and fault stage
- PARTIAL: information exists but is from a different \
asset type, different stage, or is incomplete
- MISSING: information is absent from the retrieved case

NOTES ON DIMENSION EVALUATION BEFORE DECIDING knowledge_state:
NOTE A — Dims 1 and 2 (signal_signature, diagnosis_and_reasoning) \
come from post-hoc telemetry. If detection_summary_present is \
False AND agent_diagnosis_present is False, mark dims 1 and 2 \
as PARTIAL (absence is expected for seed cases, not a fault). \
If either flag is True, evaluate that dimension normally.
NOTE B — Dims 4 and 5 (root_cause_and_factors, \
lessons_and_future_reference) describe post-repair knowledge. \
Evaluate them based on the RETRIEVED CASE only: if the \
retrieved case has a non-empty root_cause (not "unknown", \
"not yet confirmed", or blank), mark dim 4 COMPLETE. If the \
retrieved case has non-empty lessons_learned, mark dim 5 \
COMPLETE. Ignore what the incoming incident shows for these \
fields — an active incident will not have them yet.
NOTE C — Dim 3 (action_and_outcome): use ONLY the retrieved \
case to evaluate this dimension. IGNORE the incoming \
incident's action_taken field entirely — the reported action \
may differ from optimal KB guidance and must not influence \
this verdict (same rule as NOTE B for dims 4 and 5). \
Evaluate dim 3 by TWO criteria only: (1) does the retrieved \
case asset_type match the incoming asset_type? \
(2) does the retrieved case document a specific action_taken? \
Mark COMPLETE if both criteria are true. \
Mark PARTIAL if the retrieved case has an action but \
asset_type differs. Mark MISSING if no action is documented.
Example (PARTIAL — asset mismatch): incoming asset_type=pump, \
retrieved asset_type=motor, same fault_mode. Retrieved \
action="bearing replacement" is a motor procedure. Verdict: \
PARTIAL. Pump requires different seal and housing disassembly.
Example (COMPLETE — same asset, different incoming action): \
incoming asset_type=motor, retrieved asset_type=motor, same \
fault_mode, retrieved action="bearing replacement". Incoming \
action_taken="oil service and refill". Correct verdict: \
action_and_outcome=COMPLETE. The KB documents the right \
action for this fault on this asset. Ignore the incoming \
action_taken field per NOTE C.

Use this DECISION TREE to set knowledge_state. Execute STEP 1 \
first. If it matches, output EXISTING and stop — do not \
evaluate STEP 2 or STEP 3.

STEP 1 → EXISTING: Check whether ALL four of these are true:
  (a) dim 3 (action_and_outcome) status == COMPLETE
  (b) dim 4 (root_cause_and_factors) status == COMPLETE
  (c) dim 5 (lessons_and_future_reference) status == COMPLETE
  (d) fault_mode of the incoming incident matches fault_mode \
of the retrieved case (exact string match or clear synonym)
If (a) AND (b) AND (c) AND (d) are ALL true → \
knowledge_state = "EXISTING". STOP. Output EXISTING.
Dims 1 and 2 being PARTIAL or MISSING does NOT block EXISTING.

STEP 2 → PARTIAL (only if STEP 1 did not match): Check whether \
ALL of these are true:
  (a) fault_mode matches
  (b) at least 2 dimensions are COMPLETE
  (c) at least 1 dimension is PARTIAL or MISSING
  (d) dims 3, 4, 5 are NOT all COMPLETE (otherwise STEP 1 \
would have matched)
If true → knowledge_state = "PARTIAL". STOP.

STEP 3 → NEW (default, only if neither STEP 1 nor STEP 2 matched):
  fault_mode does not match OR fewer than 2 dimensions COMPLETE \
OR no meaningful candidate → knowledge_state = "NEW".

CRITICAL OUTPUT RULE: Do not include numeric similarity scores, percentages, \
or decimal probabilities in gap_summary or coverage_reasoning fields. \
Describe case matches qualitatively — 'strong match', 'partial match', \
'weak overlap', 'no reliable match'. This applies to your natural-language \
output only; internal dimension verdicts (COMPLETE/PARTIAL/MISSING) are unchanged.

Return ONLY this JSON structure:
{{
  "knowledge_state": "EXISTING or PARTIAL or NEW",
  "dimensions": {{
    "signal_signature": {{"status": "COMPLETE|PARTIAL|MISSING",
      "note": "brief reason"}},
    "diagnosis_and_reasoning": {{"status": "...",
      "note": "..."}},
    "action_and_outcome": {{"status": "...", "note": "..."}},
    "root_cause_and_factors": {{"status": "...",
      "note": "..."}},
    "lessons_and_future_reference": {{"status": "...",
      "note": "..."}}
  }},
  "gap_summary": "One plain English sentence: what this \
incident contributes that the knowledge base lacks.",
  "coverage_reasoning": "Two sentences explaining the \
knowledge_state decision in business language."
}}"""

    return system_prompt, user_prompt


def parse_coverage_response(raw: str) -> dict:
    """Parse LLM JSON response for coverage assessment."""
    try:
        cleaned = raw.strip()
        cleaned = re.sub(
            r'^```(?:json)?', '', cleaned).strip()
        cleaned = re.sub(r'```$', '', cleaned).strip()
        return json.loads(cleaned)
    except Exception:
        return {}


def _heuristic_fallback(feedback: dict,
                        best_candidate: dict) -> dict:
    """
    Structural fallback when LLM is unavailable.
    Never uses similarity thresholds for routing.
    Uses field-level structural matching only.
    """
    fault_match = (
        feedback.get("fault_mode", "").lower() ==
        best_candidate.get("fault_mode", "").lower()
    )
    asset_match = (
        feedback.get("asset_type", "").lower() ==
        best_candidate.get("asset_type", "").lower()
    )
    has_root = bool(best_candidate.get("root_cause") and
                    best_candidate["root_cause"] not in
                    ["unknown", "", None])
    has_lessons = bool(
        best_candidate.get("lessons_learned") and
        best_candidate["lessons_learned"] not in
        ["", None])

    missing_dims = []
    covered_dims = []

    if not best_candidate.get("detection_summary"):
        missing_dims.append("signal_signature")
    else:
        covered_dims.append("signal_signature")

    if not best_candidate.get("agent_diagnosis"):
        missing_dims.append("diagnosis_and_reasoning")
    else:
        covered_dims.append("diagnosis_and_reasoning")

    if best_candidate.get("action_taken") and asset_match:
        covered_dims.append("action_and_outcome")
    else:
        missing_dims.append("action_and_outcome")

    if has_root:
        covered_dims.append("root_cause_and_factors")
    else:
        missing_dims.append("root_cause_and_factors")

    if has_lessons:
        covered_dims.append("lessons_and_future_reference")
    else:
        missing_dims.append("lessons_and_future_reference")

    # Heuristic mirrors the relaxed LLM rule: dims 1 and 2
    # (signal_signature, diagnosis_and_reasoning) are post-hoc
    # telemetry that seed cases don't carry, so they're not gated
    # on for EXISTING. fault_match + asset_match + has_root +
    # has_lessons is sufficient evidence that this is a known case.
    if (fault_match and asset_match and has_root
            and has_lessons):
        state = "EXISTING"
        gap = ("Structural match confirmed. "
               "Signal and diagnosis data may be absent "
               "from seed cases.")
        reasoning = ("Fault type, asset type, root cause, "
                     "and lessons are all present. "
                     "Coverage is sufficient for action.")
    elif fault_match and len(covered_dims) >= 2:
        state = "PARTIAL"
        gap = (f"Fault type matches but "
               f"{len(missing_dims)} dimensions are "
               f"absent or asset-mismatched.")
        reasoning = ("Partial structural match found. "
                     "Some knowledge dimensions are "
                     "confirmed but gaps exist.")
    else:
        state = "NEW"
        gap = ("No structurally applicable case found. "
               "This appears to be a new fault or "
               "asset combination.")
        reasoning = ("Fault type or asset type does not "
                     "match any retrieved case. "
                     "New case generation is appropriate.")

    dim_statuses = {}
    for d in KNOWLEDGE_DIMENSIONS:
        if d in covered_dims:
            dim_statuses[d] = {
                "status": "COMPLETE",
                "note": "Structural match confirmed."
            }
        else:
            dim_statuses[d] = {
                "status": "MISSING",
                "note": "Not present in retrieved case."
            }

    return {
        "knowledge_state": state,
        "dimensions": dim_statuses,
        "gap_summary": gap,
        "coverage_reasoning": reasoning,
        "coverage_method": "heuristic"
    }


@observe()
def assess_coverage(feedback: dict,
                    best_match_content: Optional[dict],
                    llm_client) -> dict:
    """
    Assess knowledge coverage for an incoming incident.

    Returns a dict with:
        knowledge_state: EXISTING / PARTIAL / NEW
        dimensions: dict of 5 dimension assessments
        gap_summary: plain English gap description
        coverage_reasoning: business language explanation
        coverage_method: 'llm' or 'heuristic'

    Never uses similarity thresholds for routing.
    Falls back to structural heuristic if LLM unavailable.
    """
    print(
        f"[coverage_assessor] called | "
        f"candidate_present={bool(best_match_content)} | "
        f"candidate_id={best_match_content.get('case_id') if best_match_content else None} | "
        f"fault_mode={feedback.get('fault_mode')!r}",
        flush=True,
    )
    if not best_match_content:
        return {
            "knowledge_state": "NEW",
            "dimensions": {
                d: {"status": "MISSING",
                    "note": "No candidate retrieved."}
                for d in KNOWLEDGE_DIMENSIONS
            },
            "gap_summary": (
                "No relevant case found in the knowledge "
                "base. This fault requires new case "
                "generation."),
            "coverage_reasoning": (
                "FAISS retrieval returned no candidates "
                "or returned only noise-level results. "
                "Generating a new case is the correct "
                "action."),
            "coverage_method": "no_candidate"
        }

    if llm_client is None:
        result = _heuristic_fallback(
            feedback, best_match_content)
        result["coverage_method"] = "heuristic"
        return result

    try:
        from langchain_core.messages import (
            HumanMessage, SystemMessage)
        system_prompt, user_prompt = build_coverage_prompt(
            feedback, best_match_content)
        with _cf.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(
                llm_client.invoke,
                [SystemMessage(content=system_prompt),
                 HumanMessage(content=user_prompt)]
            )
            try:
                response = future.result(timeout=30)
            except _cf.TimeoutError:
                raise TimeoutError(
                    "LLM coverage assessment timed out after 30s")
        raw = response.content if hasattr(
            response, "content") else str(response)
        parsed = parse_coverage_response(raw)

        if not parsed or "knowledge_state" not in parsed:
            raise ValueError(
                "LLM returned invalid coverage response")

        parsed["coverage_method"] = "llm"

        if "dimensions" not in parsed:
            parsed["dimensions"] = {
                d: {"status": "MISSING", "note": ""}
                for d in KNOWLEDGE_DIMENSIONS
            }

        covered = [
            d for d, v in parsed["dimensions"].items()
            if v.get("status") == "COMPLETE"
        ]
        missing = [
            d for d, v in parsed["dimensions"].items()
            if v.get("status") in ("MISSING", "PARTIAL")
        ]
        parsed["covered_dimensions"] = covered
        parsed["missing_dimensions"] = missing

        # Python enforces the EXISTING rule deterministically.
        # The LLM reliably scores individual dimensions but
        # inconsistently applies the routing priority. If dims
        # 3, 4, 5 are all COMPLETE and fault_mode matches, this
        # is EXISTING regardless of what the LLM output.
        _dims = parsed.get("dimensions", {})
        _d3 = _dims.get("action_and_outcome", {}).get("status") == "COMPLETE"
        _d4 = _dims.get("root_cause_and_factors", {}).get("status") == "COMPLETE"
        _d5 = _dims.get("lessons_and_future_reference", {}).get("status") == "COMPLETE"
        _fm_match = (
            feedback.get("fault_mode", "").lower().strip() ==
            best_match_content.get("fault_mode", "").lower().strip()
        )
        if _d3 and _d4 and _d5 and _fm_match:
            if parsed.get("knowledge_state") != "EXISTING":
                parsed["knowledge_state"] = "EXISTING"
                parsed["coverage_method"] = "llm+rule"

        candidate_present = bool(best_match_content)
        candidate_fault = (best_match_content or {}).get("fault_mode", "")
        incoming_fault = feedback.get("fault_mode", "")
        fault_modes_match = (
            candidate_present
            and candidate_fault
            and incoming_fault
            and candidate_fault.strip().lower() == incoming_fault.strip().lower()
        )
        print(
            f"[coverage_assessor] enforcer state | "
            f"candidate_fault={candidate_fault!r} | "
            f"incoming_fault={incoming_fault!r} | "
            f"fault_modes_match={fault_modes_match} | "
            f"llm_verdict={parsed.get('knowledge_state')!r}",
            flush=True,
        )

        # Rule 1: Fault modes DIFFER → verdict must be NEW regardless of LLM output.
        # A different-fault candidate is a similarity artifact, not real coverage.
        if candidate_present and not fault_modes_match:
            if parsed.get("knowledge_state") != "NEW":
                _orig_state = parsed.get("knowledge_state", "?")
                _orig_reasoning = parsed.get("coverage_reasoning", "(none)")
                parsed["knowledge_state"] = "NEW"
                parsed["coverage_reasoning"] = (
                    f"Retrieved candidate has fault_mode='{candidate_fault}' but incoming is "
                    f"'{incoming_fault}'. LLM returned {_orig_state}, forced to NEW per "
                    f"architectural rule (different fault = no real coverage). "
                    f"Original LLM reasoning: " + _orig_reasoning
                )
                parsed["coverage_method"] = "llm+rule"
                parsed["covered_dimensions"] = []
                parsed["missing_dimensions"] = [
                    "signal_signature",
                    "diagnosis_and_reasoning",
                    "action_and_outcome",
                    "root_cause_and_factors",
                    "lessons_and_future_reference",
                ]

        # Rule 2: Fault modes MATCH but LLM returned NEW → force PARTIAL.
        # A same-fault candidate cannot be "no knowledge" — at minimum signal/diagnosis is covered.
        elif candidate_present and fault_modes_match and parsed.get("knowledge_state") == "NEW":
            _orig_reasoning = parsed.get("coverage_reasoning", "(none)")
            parsed["knowledge_state"] = "PARTIAL"
            parsed["coverage_reasoning"] = (
                "LLM returned NEW but a retrieved case with matching fault_mode is present. "
                "Routing forced to PARTIAL per architectural rule. "
                "Original LLM reasoning: " + _orig_reasoning
            )
            parsed["coverage_method"] = "llm+rule"

        return parsed

    except Exception as e:
        print(f"[COVERAGE] LLM assessment failed: {e}")
        result = _heuristic_fallback(
            feedback, best_match_content)
        result["coverage_method"] = "heuristic"
        return result
