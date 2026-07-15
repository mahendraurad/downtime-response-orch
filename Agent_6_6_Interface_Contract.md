# Agent 6.6 — Integration Interface Contract

**Purpose:** This document defines exactly what Agent 6.6 (Prescriptive Optimization) consumes from the upstream agents and what it produces for the downstream agent. It is the reference for wiring Agent 6.6 into the Downtime Response Orchestrator (DRO).

**Position in the pipeline:**

```
Agent 6.3 (Failure Intelligence) ─┐
Agent 6.4 (Risk Assessment)       ├──►  Agent 6.6 (Prescriptive Optimization)  ──►  Agent 6.7 (Executor)
Agent 6.5 (Knowledge)             ─┘
```

Agent 6.6 takes **three inputs** and produces **one output**.

---

## 1. Entry Point

Agent 6.6's single entry point is:

```python
from agents.prescriptive_optimization_agent import recommend_action

recommendation = recommend_action(diagnosis, risk, guidance)
```

- **Parameters (all required, positional):** `diagnosis: FaultDiagnosis`, `risk: RiskAssessment`, `guidance: KnowledgeGuidance`
- **Returns:** exactly one `MaintenanceRecommendation`
- **Never raises for known failure cases** — invalid/unreliable/blocked inputs return a structured recommendation with a specific `recommendation_status` (see §6), not an exception.

---

## 2. Two Integration Mechanisms

The three input objects are Pydantic v2 models, so integration works either in-process or across a JSON boundary.

### Mechanism A — In-process (Python objects)

If the agents run in one process, upstream agents construct the Pydantic objects directly and pass them in:

```python
diagnosis = FaultDiagnosis(...)   # built by Agent 6.3
risk      = RiskAssessment(...)   # built by Agent 6.4
guidance  = KnowledgeGuidance(...) # built by Agent 6.5
rec = recommend_action(diagnosis, risk, guidance)
```

### Mechanism B — JSON boundary (network / queue / files)

If the agents exchange JSON, convert at the boundary using Pydantic's built-in methods:

```python
# Inbound: JSON dict -> validated object
diagnosis = FaultDiagnosis.model_validate(diagnosis_json)
risk      = RiskAssessment.model_validate(risk_json)
guidance  = KnowledgeGuidance.model_validate(guidance_json)

rec = recommend_action(diagnosis, risk, guidance)

# Outbound: object -> JSON dict (or string)
rec_json = rec.model_dump(mode="json")     # dict with JSON-safe values
rec_str  = rec.model_dump_json()           # JSON string
```

`model_validate` enforces every field type, constraint, and validator listed below, so malformed upstream JSON is rejected at the boundary with a clear Pydantic error.

---

## 3. INPUT 1 — FaultDiagnosis  (from Agent 6.3)

*What kind of fault was found.*

### Field reference

| Field | Type | Required | Constraints | Meaning |
|---|---|---|---|---|
| `case_id` | string | yes | — | Case identifier (must match across all three inputs). |
| `asset_id` | string | yes | — | Asset identifier (must match the risk input and exist in master data). |
| `bearing_id` | string | yes | — | Bearing identifier (must belong to the asset). |
| `fault_code` | string | yes | — | The fault's code. |
| `fault_mode` | string (enum) | yes | one of: `outer_race_fault`, `inner_race_fault`, `lubrication_issue`, `imbalance`, `misalignment`, `cage_fault`, `unknown` | The diagnosed fault mode. Drives catalog lookup; `unknown` routes to the novel path. |
| `affected_component` | string | yes | — | The component implicated. |
| `severity` | string (enum) | yes | one of: `monitor`, `stage_1`, `stage_2`, `stage_3` | Fault severity. Drives candidate selection and urgency. |
| `confidence` | float | **yes (no default)** | 0.0–1.0 | Diagnosis confidence. **Below 0.5 → the agent returns `unreliable_diagnosis`.** |
| `evidence` | list of strings | no | default `[]` | Supporting evidence. |
| `likely_causes` | list of strings | no | default `[]` | Likely causes. |
| `recommended_checks` | list of strings | no | default `[]` | Suggested checks. |
| `diagnosed_at_utc` | datetime (ISO 8601) | yes | — | When the diagnosis was made. |

### Sample JSON

```json
{
  "case_id": "CASE-2026-0512",
  "asset_id": "AST_PMP_001",
  "bearing_id": "BRG_005",
  "fault_code": "LUB-002",
  "fault_mode": "lubrication_issue",
  "affected_component": "pump drive-end bearing",
  "severity": "stage_2",
  "confidence": 0.86,
  "evidence": ["rising bearing temperature", "grease analysis: oxidation"],
  "likely_causes": ["degraded lubricant", "extended service interval"],
  "recommended_checks": ["inspect grease condition", "verify lubrication schedule"],
  "diagnosed_at_utc": "2026-05-12T09:30:00Z"
}
```

---

## 4. INPUT 2 — RiskAssessment  (from Agent 6.4)

*How likely, how soon, how costly.*

### Field reference

| Field | Type | Required | Constraints | Meaning |
|---|---|---|---|---|
| `case_id` | string | yes | — | Must match the diagnosis. |
| `asset_id` | string | yes | — | Must match the diagnosis. |
| `bearing_id` | string | yes | — | Must match the diagnosis. |
| `failure_probability` | float | yes | 0.0–1.0 | Probability of failure. |
| `risk_level` | string (enum) | yes | one of: `low`, `medium`, `high`, `critical` | Overall risk level. |
| `rul_min_days` | integer | yes | ≥ 0 | Remaining useful life, worst-case (conservative). Drives urgency and part-lead-time checks. |
| `rul_max_days` | integer | yes | ≥ 0, **must be ≥ `rul_min_days`** | Remaining useful life, best-case. |
| `confidence` | float | yes | 0.0–1.0 | Confidence in the assessment. |
| `business_impact_flag` | boolean | yes | — | **`true` forces escalation to Plant Manager.** |
| `estimated_downtime_cost_per_hour` | float | yes | ≥ 0.0 | Cost of downtime per hour. |
| `assessed_at_utc` | datetime (ISO 8601) | yes | — | When the assessment was made. |

**Validator:** `rul_max_days` must be ≥ `rul_min_days`, or construction fails.

### Sample JSON

```json
{
  "case_id": "CASE-2026-0512",
  "asset_id": "AST_PMP_001",
  "bearing_id": "BRG_005",
  "failure_probability": 0.42,
  "risk_level": "medium",
  "rul_min_days": 14,
  "rul_max_days": 28,
  "confidence": 0.80,
  "business_impact_flag": false,
  "estimated_downtime_cost_per_hour": 3500.0,
  "assessed_at_utc": "2026-05-12T09:35:00Z"
}
```

---

## 5. INPUT 3 — KnowledgeGuidance  (from Agent 6.5)

*Relevant SOPs, sections, inspection steps, and safety notes.*

### Field reference

| Field | Type | Required | Constraints | Meaning |
|---|---|---|---|---|
| `case_id` | string | yes | — | Must match the other inputs. |
| `source_documents` | list of strings | no | default `[]` | Referenced SOP/case documents. The first is used as the rationale's source. |
| `relevant_sections` | list of strings | no | default `[]` | Relevant sections within those documents. |
| `inspection_steps` | list of strings | no | default `[]` | Actionable inspection steps. |
| `safety_notes` | list of strings | no | default `[]` | Safety notes. |

An empty guidance object (only `case_id`) is valid — it simply means no knowledge was retrieved.

### Sample JSON

```json
{
  "case_id": "CASE-2026-0512",
  "source_documents": ["SOP_003_Lubrication_Service.pdf"],
  "relevant_sections": ["3.2 Re-greasing procedure", "4.1 Lubricant specification"],
  "inspection_steps": ["Isolate pump", "Purge old grease", "Apply specified lubricant"],
  "safety_notes": ["Lock out/tag out before servicing", "Wear PPE"]
}
```

---

## 6. OUTPUT — MaintenanceRecommendation  (to Agent 6.7)

*The decision, ranked alternatives, parts, timing, approver, and rationale.*

### Field reference

| Field | Type | Meaning |
|---|---|---|
| `case_id`, `asset_id`, `bearing_id` | string | Identifiers, echoed from the inputs. |
| `recommended_action` | `Action` object | The chosen action (see sub-model below). |
| `urgency` | string (enum) | one of: `monitor`, `planned`, `urgent`, `emergency`. |
| `ranked_alternatives` | list of `Action` | Other scored candidates; may be empty. |
| `required_parts` | list of `RequiredPart` | Parts needed (see sub-model); empty for part-free actions. |
| `window_chosen` | string or null | The scheduled maintenance window id, if any. |
| `rationale` | string | Plain-language justification. |
| `evidence` | object (string→string) | Per-element reasoning; the audit record. |
| `recommendation_status` | string (enum) | Status/outcome — see the status table below. Default `ok`. |
| `is_llm_suggested` | boolean | `true` when the action was proposed by the LLM (novel path). |
| `approval_status` | string (enum) | `pending`, `escalated`, `approved`, `rejected`. Default `pending`. |
| `responsible_approver` | string | The single sign-off authority (role — name). |
| `responsible_approver_id` | string | Persona id of the approver. |
| `responsible_person` | string | *Legacy field — mirrors the approver; kept for backwards compatibility. Prefer `responsible_approver`.* |
| `responsible_person_id` | string or null | *Legacy — see above.* |
| `contributors` | list of objects | Specialists who must advise; each is `{"role", "name", "concern"}`. Empty for clean routine cases. |
| `generated_at_utc` | datetime (ISO 8601) | When the recommendation was generated. |

**`Action` sub-model:** `name` (string), `description` (string), `estimated_duration_hours` (float ≥ 0).

**`RequiredPart` sub-model:** `part_number` (string), `quantity` (int ≥ 1), `lead_time_days` (int ≥ 0; `0` = in stock).

### `recommendation_status` values — what the downstream executor should do

| Status | Meaning | Executor action |
|---|---|---|
| `ok` | Normal recommendation. | Proceed per `approval_status`. |
| `blocked_no_part` | Correct action known, but the required part is unavailable in time. `required_parts` names it. | Initiate procurement; escalated. |
| `blocked_unknown_asset` | Asset not found in master data. | Escalate for data correction. |
| `unreliable_diagnosis` | Diagnosis confidence below 0.5. | Escalate for physical inspection. |
| `catalog_miss` | No approved procedure for this fault. | (Typically superseded by `novel_llm_suggestion`.) |
| `novel_llm_suggestion` | Novel fault; the LLM proposed a tentative action. `is_llm_suggested = true`. | **Requires human validation before execution;** escalated. |
| `blocked_invalid_input` | Upstream data failed validation. | Escalate for data correction. |

Any status other than `ok` arrives **escalated** and directs a human to a specific next step via the `rationale`.

### Sample JSON (a normal `ok` recommendation)

```json
{
  "case_id": "CASE-2026-0512",
  "asset_id": "AST_PMP_001",
  "bearing_id": "BRG_005",
  "recommended_action": {
    "name": "lubrication_service",
    "description": "Re-grease the pump drive-end bearing per SOP_003.",
    "estimated_duration_hours": 3.0
  },
  "urgency": "planned",
  "ranked_alternatives": [],
  "required_parts": [
    { "part_number": "MOBIL-DTE-25", "quantity": 1, "lead_time_days": 0 }
  ],
  "window_chosen": "WIN_005",
  "rationale": "Lubrication issue on AST_PMP_001 at stage_2 with ~14 days remaining life. A lubrication service is scheduled in the next available maintenance window; the lubricant is in stock. Routine sign-off by the Plant Supervisor.",
  "evidence": {
    "fault": "lubrication_issue (confidence 0.86)",
    "risk": "medium; RUL 14–28 days",
    "part": "MOBIL-DTE-25 in stock",
    "window": "WIN_005",
    "urgency": "planned — scheduled within an available maintenance window"
  },
  "recommendation_status": "ok",
  "is_llm_suggested": false,
  "approval_status": "pending",
  "responsible_approver": "Plant Supervisor — James Kowalski",
  "responsible_approver_id": "PERSONA_SUP",
  "responsible_person": "Maintenance Supervisor",
  "responsible_person_id": null,
  "contributors": [
    { "role": "Maintenance Planner", "name": "...", "concern": "confirm part availability and scheduling" }
  ],
  "generated_at_utc": "2026-05-12T09:40:00Z"
}
```

---

## 7. Validation and Guarantees (what the integration team can rely on)

1. **Consistency is enforced.** The three inputs must share the same `case_id`, and `asset_id`/`bearing_id` must agree across the diagnosis and risk objects; the bearing must belong to the asset. Mismatches produce `blocked_invalid_input`.
2. **Every field is type- and range-checked** on construction (Pydantic). Malformed JSON is rejected at `model_validate` with a clear error.
3. **The agent never crashes on known failure conditions** — unknown assets, unreliable diagnoses, missing parts, invalid input, and novel faults all return a structured `MaintenanceRecommendation` with the appropriate status, not an exception.
4. **The output is always a complete `MaintenanceRecommendation`**, whether it represents a normal recommendation or an escalated block.

---

## 8. Pre-processing note (safe defaults)

Before calling `recommend_action`, the current application applies `apply_safe_defaults(diagnosis, risk)` (from `agents/uncertainty_detector`), which fills missing *optional* fields with conservative defaults (e.g. missing `confidence` → 0.60, missing `rul_min_days` → 14) on copies, without mutating the originals. Integrators may reuse this step or supply complete inputs directly. Required fields (listed above) cannot be defaulted and must be provided.
