<!-- ************** Added by Prateek Mittal on 20th July 2026 ****************** -->
<!-- Running change record for Agent 4: Predictive Risk Agent. -->

# Agent 4 Changes

This is the permanent implementation, rationale, contract, and test record for
Agent 4, the Predictive Risk Agent.

## 2026-07-20 - Baseline audit

### Agent responsibility

Agent 4 answers:

> Given a valid fault diagnosis and anomaly, how urgent is the risk, what is the
> explainable remaining-useful-life window, and what business impact is exposed?

Current contract:

```text
FaultDiagnosis + AnomalyEvent + TrustedBearingSignal
    -> PredictiveRiskAgent.process()
    -> RiskAssessment
```

### Guiding-document requirements

The developer roadmap requires Agent 4 to:

1. Create a health index from available degradation/severity evidence.
2. Produce explainable RUL bands and a monitor outcome.
3. Calculate confidence from available signal/evidence/history coverage.
4. Produce failure probability, RUL window, risk level, confidence, and business
   impact.
5. Ensure higher degradation produces shorter RUL.
6. Link every result back to its anomaly and diagnosis.

The current typed handoff does not contain maintenance-history coverage, trend
duration, or a generalized band-growth series. Agent 4 must expose that absence
rather than fabricate those values. ISO stage, Agent 2 anomaly evidence, Agent 3
match strength/confidence, and asset context are the deterministic MVP inputs.

### Existing implementation

The baseline Agent 4 already provides:

- Taxonomy-driven RUL bands.
- Failure probability from ISO-stage base and anomaly score.
- Health index as `1 - failure_probability`.
- Risk level inherited from Agent 3 severity.
- Bottleneck/high-criticality business-impact flag.
- Financial exposure from RUL upper bound and downtime cost.
- Monitor band for an undetermined diagnosis.
- Optional LLM advisory and optional HITL callback.
- Batch processing.

### Baseline tests

`tests/test_predictive_risk.py` initially contains 16 tests covering:

- Stage-3 rolling-element and outer-race scenarios.
- Taxonomy-driven RUL and calibration.
- Health index.
- Stage-1 risk.
- Business-impact policy.
- Monitor band and missing asset context.
- Optional LLM/HITL advisory behavior.

Baseline result:

```text
16 passed
```

### Completion gaps

1. Invalid/mismatched Agent 1-3 handoffs can raise or produce a misleading risk.
2. Invalid Agent 3 diagnoses are not distinguished from valid undetermined
   diagnoses.
3. Unknown taxonomy codes can silently become a monitor band even when a
   diagnosis claims a known stage.
4. Output lacks explicit assessment status, eligibility, explanation evidence,
   and provenance.
5. Configuration validation is incomplete.
6. Numerical NaN/infinity and range edges are not defended.
7. Batch input is not isolated against malformed items.
8. Agent 3 -> Agent 4 integration coverage is limited.
9. The LLM fallback is enabled in configuration despite not being required by
   the deterministic Phase 5 roadmap.
10. Input/output examples and a complete test catalogue are missing.

### Initial decisions

- A valid `undetermined` Agent 3 diagnosis produces an assessed `monitor`
  RiskAssessment.
- An `invalid_input` Agent 3 diagnosis produces an Agent 4 `invalid_input`
  result and is not risk-eligible.
- A diagnosis claiming stage 1-3 must have a matching active taxonomy code and
  fault mode; otherwise it is invalid, not `monitor`.
- Agent 4 will fail fast on invalid configuration/taxonomy at construction and
  return explicit invalid results for bad runtime handoffs.
- Deterministic rule outputs remain authoritative. Optional LLM output is
  advisory only and disabled by default.
- No separate Agent 4 persistence database will be added; the orchestrator owns
  the complete linked maintenance case.

## 2026-07-20 - Agent 4 completion pass

### Defensive Agent 3 handoff

Agent 4 validates the complete linked handoff before calculating risk:

- Correct `FaultDiagnosis`, `AnomalyEvent`, and `TrustedBearingSignal` types.
- Agent 3 diagnosis is not `invalid_input` and is risk eligible.
- Agent 1 trusted signal remains downstream eligible.
- Case, asset, bearing, channel, and timestamp identities agree.
- Anomaly score, anomaly confidence, and diagnosis confidence are finite and in
  the range 0-1.
- ISO stage is 0, 1, 2, or 3.
- Stage 0 means an `undetermined` diagnosis with no fault code.
- Stages 1-3 mean a `diagnosed` result with a matching active taxonomy rule.
- Agent 3 and Agent 4 taxonomy versions agree when provenance is supplied.
- Downtime cost is finite and non-negative.

Bad runtime input returns `assessment_status="invalid_input"`,
`risk_eligible=false`, and zero confidence. It is not converted into a monitor
risk card.

### Assessment outcomes

| Status | Meaning | Downstream eligible |
|---|---|---|
| `assessed` | Valid classified fault with deterministic risk and RUL | Yes |
| `monitor` | Valid anomaly but Agent 3 could not classify a supported fault | Yes, for manual guidance |
| `invalid_input` | Broken or inconsistent upstream contract | No |

The orchestrator now checks Agent 3 `diagnostic_eligible` before Agent 4 and
Agent 4 `risk_eligible` before Knowledge retrieval.

### Explainable risk calculation

```text
failure_probability =
    stage_base_weight * configured_stage_probability
  + anomaly_weight * anomaly_score

health_index = 1 - failure_probability
```

Structured evidence contains every component and weight. ISO stage is the
available severity/degradation feature. Agent 2's anomaly score captures the
stronger Hotelling T-squared/EWMA evidence, so slow drift influences risk
without inventing a separate trend duration.

### RUL policy

```text
stage 3 -> 0 to rul_days_stage_3
stage 2 -> rul_days_stage_3 to rul_days_stage_2
stage 1 -> rul_days_stage_2 to rul_days_stage_1
stage 0 -> configured monitor band
```

Taxonomy validation guarantees RUL does not increase as the fault advances.
This satisfies the roadmap criterion that higher degradation produces shorter
RUL while allowing different fault modes to have calibrated rates.

### Confidence and unavailable history

Agent 4 carries Agent 3 confidence, which already blends Agent 2 confidence
(sensor quality, usable-signal coverage, and Agent 1 data quality) with fault
match strength. Evidence explicitly states:

```text
history_coverage = not_available_in_current_typed_contract
```

Maintenance-history coverage, trend duration, and generalized band-energy
growth are not fabricated. They can be introduced later as typed inputs.

### Business impact and exposure

`business_impact_flag` is configurable for bottleneck and high-criticality
assets. Financial exposure is:

```text
rul_max_days * 24 * downtime_cost_per_hour
```

The upper bound is used because stage 3 has a zero-day lower bound; using the
lower bound would incorrectly report zero exposure. Monitor results have no
quantified failure horizon, so exposure is zero.

### Deterministic explanation

Every valid result includes `risk_explanation` and calculation `evidence`. It
names fault mode, stage, RUL band, failure probability, health index,
business-impact status, and exposure without using an LLM.

### Optional LLM/HITL boundary

The Phase 5 calculation remains deterministic. `risk_config.json` disables the
LLM fallback by default. If explicitly enabled:

- It can add only advisory text.
- It can alter risk level only for a valid `monitor` result.
- It cannot alter RUL, probability, health index, or exposure.
- A matched diagnosis retains its deterministic risk level.
- Provider exceptions, unusable output, HITL rejection, or HITL errors preserve
  the deterministic result.
- Invalid input never calls the LLM.

### Configuration validation

Agent 4 fails fast for missing/non-finite/out-of-range stage probabilities,
incorrect weight keys/sums, decreasing stage probabilities, unsupported risk
policy, invalid monitor bands, invalid LLM settings, or contradictory taxonomy.

### Provenance

Every result includes Agent 4 schema/config/taxonomy versions, Agent 3
schema/config/taxonomy versions, Agent 2 config/detector versions, Agent 1
schema/config/master versions, and linked anomaly/diagnosis case IDs.

### Batch and persistence decisions

Malformed batch triples receive individual `invalid_input` results without
aborting valid items. Agent 4 does not create another SQLite repository: risk
belongs to the orchestrator/API case record alongside anomaly and diagnosis.

## Expected Input

```python
risk = predictive_risk_agent.process(
    fault_diagnosis,
    anomaly_event,
    trusted_bearing_signal,
)
```

All three objects must describe the same case, asset, bearing, channel, and
event. A stage 1-3 diagnosis must match the active taxonomy; stage 0 must be a
valid `undetermined` diagnosis.

## Expected Output

### Classified fault

```json
{
  "case_id": "ANOM-BRG_011-2026-05-20T10:00:00Z",
  "asset_id": "AST_GBX_001",
  "bearing_id": "BRG_011",
  "failure_probability": 0.94,
  "health_index": 0.06,
  "risk_level": "critical",
  "rul_min_days": 0,
  "rul_max_days": 10,
  "rul_band_label": "0–10 days",
  "confidence": 0.93,
  "business_impact_flag": true,
  "financial_exposure": 4320000.0,
  "assessment_status": "assessed",
  "risk_eligible": true,
  "assessment_source": "rules",
  "risk_explanation": "Rolling Element Fault at ISO stage 3...",
  "risk_config_version": "16-character-version",
  "taxonomy_version": "16-character-version"
}
```

### Valid undetermined anomaly

```json
{
  "assessment_status": "monitor",
  "risk_eligible": true,
  "rul_min_days": 45,
  "rul_max_days": 999,
  "rul_band_label": "monitor",
  "financial_exposure": 0.0
}
```

### Invalid handoff

```json
{
  "assessment_status": "invalid_input",
  "risk_eligible": false,
  "confidence": 0.0,
  "rul_band_label": "",
  "status_reason": "diagnosis and anomaly case identities do not match"
}
```

## Complete test catalogue summary

| Suite | Cases | Coverage |
|---|---:|---|
| `test_predictive_risk.py` | 16 | Original RUL, probability, exposure, impact, monitor, LLM behavior |
| `test_predictive_risk_hardening.py` | 56 | Types, identity, numbers, diagnosis/taxonomy/config, provenance, batch, RUL curves |
| `test_predictive_risk_advisory_edges.py` | 9 | Default-off policy, provider/HITL failures and advisory invariants |
| `test_agent3_agent4_integration.py` | 10 | Four faults, monitor, invalid diagnosis, provenance, impact, immutability |
| `test_agent1_agent2_agent3_agent4_integration.py` | 33 | Complete fault, stop, source-profile, quality, ordering, routing, JSON, and batch paths |
| **Agent 4 total** | **124** | |

```text
Agent 4 certification: 124 passed
All Agent 1-4 integration suites: 89 passed
Full repository: 384 passed, 2 skipped, 0 failed
```

Run Agent 4 certification:

```powershell
python -m pytest tests/test_predictive_risk.py tests/test_predictive_risk_hardening.py tests/test_predictive_risk_advisory_edges.py tests/test_agent3_agent4_integration.py tests/test_agent1_agent2_agent3_agent4_integration.py -q
```

## Completion boundary

Agent 4 is complete for the deterministic development/pilot scope:

- Explainable probability and health index.
- Taxonomy-driven monotonic RUL and monitor outcome.
- Confidence linked to upstream data/evidence strength.
- Risk level, business-impact flag, and exposure.
- Deterministic explanation and evidence.
- Strict Agent 1-3 validation and Agent 5 routing protection.
- Configuration/taxonomy validation and provenance.
- Optional advisory/HITL isolation.
- Single/batch operation and comprehensive tests.

History-aware calibration remains a future typed-input enhancement, not an
unfinished hidden dependency of the MVP.

<!-- *********************** -->
