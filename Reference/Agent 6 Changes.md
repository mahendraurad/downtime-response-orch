<!-- ************** Added by Prateek Mittal on 20th July 2026 ****************** -->
# Agent 6 Changes — Prescriptive Optimization Agent

## Objective and roadmap alignment

Agent 6 converts validated Agent 4 risk and source-grounded Agent 5 guidance into a ranked, operationally feasible maintenance recommendation. It implements roadmap Phase 7: risk/RUL, criticality, bottleneck impact, inventory and planned-window constraints.

The authoritative action ranking remains deterministic. An optional LLM may rewrite only the human-readable rationale; it cannot change action, urgency, parts, window, approval or evidence.

## Implementation

- Replaced the placeholder `PrescriptiveOptimizationAgent`.
- Added validated `config/prescriptive_config.json` and `PrescriptiveConfig`.
- Added risk-specific ranked action policies, durations and urgency mapping.
- Added critical bottleneck escalation to `stop_and_replace`.
- Added part availability, lead-time and procurement detection.
- Added maintenance-window fit and unavailable-window rejection.
- Added safe fallback from an infeasible planned repair to derating/inspection.
- Added approval policy and responsible-person/approver assignment.
- Added strict Agent 4/5 identity, eligibility and grounding validation.
- Added configuration and upstream provenance.
- Added optional fail-safe LLM rationale rewriting with immutable decisions.

## Expected input

```python
agent.process(
    risk: RiskAssessment,
    diagnosis: FaultDiagnosis,
    guidance: KnowledgeGuidance,
    inventory_lookup={"SKF6310": {"qty_on_hand": 2, "lead_time_days": 5}},
    context_lookup={"planned_stop_windows": [
        {"window_id": "WIN-1", "duration_hours": 8, "available": True}
    ]},
)
```

Agent 5 guidance must be `grounded` and eligible. Case, asset and bearing identities must match upstream.

## Expected output

`MaintenanceRecommendation` contains the selected action, urgency, ranked alternatives, parts, window, procurement and approval flags, rationale, evidence, personnel, status and complete provenance.

Outcomes:

| Status | Meaning |
|---|---|
| `ok` | Valid ranked recommendation |
| `invalid_input` | Unsafe/mismatched/ungrounded handoff; not executable |

Repair/replacement/derating actions are pending approval. Monitoring is pre-approved and log-only.

## Test catalogue

`tests/test_prescriptive_optimization.py` contains 32 tests covering all risk levels, bottlenecks, parts/lead time, window fit, approval, malformed inputs, ungrounded guidance, identity tampering, unsupported risk, provenance, serialization, immutability, LLM success/malformed/timeout behavior, invalid-input no-call behavior and configuration validation.

```text
Agent 6 suite: 32 passed
```

## Completion boundary

Agent 6 is complete for deterministic pilot ranking. Production inventory and scheduling systems replace the dictionary adapters. A production LLM may improve wording but never becomes the decision authority.
<!-- *********************** -->
