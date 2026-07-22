<!-- ************** Added by Prateek Mittal on 20th July 2026 ****************** -->
<!-- Running change record for Agent 3: Failure Intelligence Agent. -->

# Agent 3 Changes

This is the permanent change record for Agent 3, the Failure Intelligence
Agent. Every Agent 3 implementation change, contract decision, test, and
verification result must be recorded here before Agent 3 is considered
complete.

## 2026-07-20 - Baseline audit

### Agent responsibility

Agent 3 answers:

> Given a confirmed anomaly and its trusted telemetry, what failure mode is
> most likely, how advanced is it, and what evidence supports the diagnosis?

Current contract:

```text
AnomalyEvent + TrustedBearingSignal
    -> FailureIntelligenceAgent.process()
    -> FaultDiagnosis
```

Unlike Agent 2, Agent 3 always returns an object. If no configured fault rule
matches, the output uses `fault_mode="undetermined"` and recommends manual
inspection.

### Current flow

1. Read BPFO, BPFI, BSF, FTF, kurtosis, temperature, and vibration values from
   the trusted signal.
2. Derive temperature rise, vibration-to-baseline ratio, and the broadband
   vibration flag.
3. Evaluate every active taxonomy rule in configured priority order.
4. Select the first complete match as the primary diagnosis.
5. Retain other rules with partial evidence as differential diagnoses.
6. Assign ISO stage from the matched signal's stage thresholds.
7. Assign severity from ISO stage and asset criticality/bottleneck policy.
8. Blend Agent 2 confidence with match strength.
9. Return causes, recommended checks, estimated RUL, evidence, and a
   deterministic narrative.

### Current deterministic utilities

| Component | Location | Purpose |
|---|---|---|
| Generic fault matcher | `src/tools/fault_matcher.py` | Evaluates structured taxonomy rules without per-fault branching |
| Agent-level policy | `config/fi_config.json` | Severity, confidence, broadband, checks, and logging policy |
| Fault taxonomy | `data/fault_taxonomy.json` | Fault modes, detection thresholds, priority, causes, and RUL values |
| Output schema | `src/schemas/diagnosis.py` | Agent 3 to Agent 4 `FaultDiagnosis` contract |

These are appropriate deterministic utilities under Agent 3; separate
autonomous utility agents are not required for the present scope.

### Currently active fault rules

| Priority | Code | Fault mode | Primary signal/pattern |
|---:|---|---|---|
| 1 | `FT_001` | `outer_race_fault` | BPFO energy and kurtosis gate |
| 2 | `FT_002` | `inner_race_fault` | BPFI energy and kurtosis gate |
| 3 | `FT_006` | `cage_fault` | Dominant FTF energy and kurtosis gate |
| 4 | `FT_003` | `lubrication_issue` | Broadband vibration, temperature rise, and low discrete-fault kurtosis |
| 5 | `FT_007` | `gearbox_fault` | Dominant BSF energy and kurtosis gate |

`FT_004` imbalance and `FT_005` misalignment are dormant because the canonical
telemetry schema does not currently publish the required 1X/2X spectral
features. Agent 3 must not pretend to diagnose them without those inputs.

### Current output

`FaultDiagnosis` currently contains:

- Case, asset, and bearing identity.
- Fault mode and taxonomy code.
- ISO stage and severity.
- Classification confidence.
- BPFO, BPFI, and kurtosis readings at detection.
- Structured evidence and differential diagnoses.
- Recommended checks and likely causes.
- Deterministic narrative.
- Bottleneck flag and stage-specific RUL estimate for Agent 4.
- Processing timestamp.

### Baseline tests

The authoritative current suite is `tests/test_failure_intelligence.py`.
These tests already run the real Agent 1 -> Agent 2 -> Agent 3 path rather than
testing Agent 3 only with fabricated upstream objects.

Current result:

```text
7 passed, 2 failed
```

Passing coverage includes:

- Outer-race classification and stage.
- Inner-race classification and differential evidence.
- Lubrication classification.
- Healthy records not reaching Agent 3.
- The `undetermined` fallback.
- Inclusive ISO-stage boundaries.
- Kurtosis gating.

### Confirmed taxonomy/test contract conflict

The two failures are:

1. `test_gearbox_tel_0020`
2. `test_all_fault_scenarios_classify`

Both tests expect the `gearbox_fault` scenario to be classified as
`outer_race_fault`. The active taxonomy now explicitly defines:

```text
FT_007 -> gearbox_fault -> dominant BSF signature -> priority 5
```

The generic matcher therefore returns `gearbox_fault`, consistently with the
active taxonomy and API fault-mode list. However, the schema comments and
pipeline documentation still describe the older outer-race interpretation.
This is a stale-contract conflict, not evidence that the matcher is randomly
misclassifying the input.

Before closing Agent 3, the repository must adopt one authoritative meaning:

- Keep `FT_007/gearbox_fault` and update stale tests, schema comments,
  documentation, recommended checks, and Agent 4 expectations; or
- Remove/deactivate `FT_007` if the scenario represents an outer-race bearing
  fault located inside a gearbox rather than a gearbox fault mode.

This initial inference was superseded after re-reading the main developer
roadmap. The authoritative decision and rationale are recorded in the
completion pass below.

## Roadmap comparison

| Expected capability | Current state |
|---|---|
| Consume Agent 2 anomaly plus trusted telemetry | Implemented |
| BPFO outer-race matching | Implemented |
| BPFI inner-race matching | Implemented |
| BSF/rolling-element or gearbox matching | Implemented as `FT_007 gearbox_fault`, but contract is inconsistent |
| FTF cage matching | Implemented |
| Broadband vibration and temperature lubrication pattern | Implemented |
| ISO-stage assignment | Implemented |
| Criticality-aware severity | Implemented |
| Confidence and supporting evidence | Implemented |
| Differential diagnoses | Implemented |
| Deterministic narrative and checks | Implemented |
| Agent 2 to Agent 3 integration | Present inside the existing test suite, but not comprehensive |
| Defensive input-contract validation | Missing |
| Configuration/taxonomy provenance | Missing |
| Taxonomy semantic validation | Partial |
| Complete fault-rule unit coverage | Missing |
| Durable diagnosis audit/idempotency | Not yet implemented; necessity must be decided against orchestrator persistence |

## Proposed completion scope

### 1. Resolve and document the gearbox meaning

Align taxonomy, schema, documentation, Agent 3 tests, Agent 4 assumptions, and
recommended checks around one meaning of `gearbox_fault`. This is the first
task because every later test depends on the authoritative contract.

### 2. Defend the Agent 2 handoff

Agent 3 should safely handle:

- Missing or invalid anomaly/trusted inputs.
- Asset or bearing identity mismatch between the two inputs.
- An Agent 1 signal that was not downstream-eligible.
- An Agent 2 decision that was suppressed, healthy, or otherwise not eligible
  for diagnosis, if the richer Agent 2 decision contract is supplied.
- Missing measurement or baseline values needed by a rule.

The output must remain explicit and auditable instead of raising an incidental
attribute/type error.

### 3. Validate configuration and taxonomy semantics

Add fail-fast validation for matters such as:

- Unique fault codes and fault modes.
- Unique or intentionally resolved priorities.
- Valid detection signal names.
- Monotonic stage thresholds (`stage_1 <= stage_2 <= stage_3`).
- Supported kurtosis modes and resolvable threshold references.
- Confidence weights and valid severity labels.
- Stage-specific RUL values required by active rules.

### 4. Add provenance to every diagnosis

Record stable schema, Agent 3 configuration, and taxonomy version identifiers
so a diagnosis can be reproduced from the exact rules that produced it.
Propagate relevant Agent 1 and Agent 2 provenance where available.

### 5. Complete fault and boundary testing

Add comprehensive tests for:

- Cage and gearbox/BSF classifications.
- All ISO stages and exact threshold boundaries.
- Dominance ties and rule priority.
- Missing signals and missing baselines.
- Lubrication positive and negative gates.
- Multiple simultaneous candidates and differential ordering.
- Severity escalation combinations.
- Confidence boundaries and weight validation.
- Malformed taxonomy rules and unresolved references.
- Serialization and provenance.
- Batch processing and input immutability.
- Agent 2 -> Agent 3 eligible, healthy, suppressed, and invalid handoffs.

### 6. Decide the persistence boundary without duplication

Agent 3 diagnoses need an audit trail, but a second SQLite repository should
only be introduced if the orchestrator/API does not already persist the full
case state. The preferred design is one case-level audit boundary rather than
independent databases for every agent.

## Initial completion boundary

Agent 3 will be considered complete for the deterministic development/pilot
scope when:

- Its fault vocabulary is consistent across taxonomy, schemas, tests,
  documentation, API, and Agent 4.
- It rejects or explicitly marks invalid upstream handoffs without crashing.
- Active taxonomy rules are validated before processing.
- Every result is traceable to schema, configuration, and taxonomy versions.
- Each active fault rule, fallback, stage, severity, confidence, differential,
  and integration path has executable pytest coverage.
- Agent 1, Agent 2, Agent 3, their integration tests, and the full repository
  suite have been rerun and documented.

## 2026-07-20 - Completion and strict edge-case pass

### Decision 1: BSF means a rolling-element fault

The original developer roadmap explicitly requires:

```text
high BPFO -> outer race fault
high BPFI -> inner race fault
high BSF  -> ball fault
```

For bearing terminology and compatibility with both balls and rollers, Agent 3
uses `rolling_element_fault`. `FT_007` was changed from `gearbox_fault` to:

```text
FT_007 -> rolling_element_fault -> dominant BSF energy
```

Rationale:

- `gearbox_fault` described the host asset/scenario, not the damaged bearing
  component.
- BSF is the rolling-element characteristic frequency requested by the main
  document.
- The diagnosis must describe what failed; asset type remains available through
  `asset_id` and asset context.

The taxonomy, schema comments, API vocabulary, persona formatting, SOP fault
mapping, Agent 3 tests, and Agent 4 RUL expectations were aligned. The gearbox
scenario name remains unchanged because it is useful asset/test context.

### Decision 2: invalid handoffs produce an audit result

Agent 3 continues to return one `FaultDiagnosis` for every direct call, but it
now distinguishes:

| `diagnosis_status` | Meaning |
|---|---|
| `diagnosed` | An active taxonomy rule matched |
| `undetermined` | Input was valid, but no active rule matched |
| `invalid_input` | The Agent 1/2 handoff was unsafe or internally inconsistent |

`diagnostic_eligible` is false only for `invalid_input`. This keeps an
`undetermined` anomaly available for manual reliability review while preventing
bad upstream data from being represented as a genuine diagnostic uncertainty.

Agent 3 now rejects safely:

- Non-`AnomalyEvent` or non-`TrustedBearingSignal` input.
- Agent 1 records that are not downstream eligible.
- Missing case, asset, or bearing identity.
- Asset, bearing, channel, or timestamp mismatch across Agent 2 and Agent 1.
- Anomaly scores or confidence outside 0-1, including NaN and infinity.
- Non-finite diagnostic telemetry, even if a caller incorrectly marks it
  downstream eligible.

### Decision 3: taxonomy errors fail during construction

`src/tools/failure_intelligence_utilities.py` now validates the taxonomy before
Agent 3 accepts work. This is intentionally stricter than the runtime fallback:
an unknown telemetry pattern is a valid `undetermined` result, but contradictory
configuration is a deployment error and must be corrected.

Validation includes:

- Non-empty taxonomy.
- Unique fault codes and fault modes.
- Structured detection objects.
- Positive, unique integer priorities.
- Supported canonical signal names.
- Finite, positive, monotonic stage thresholds.
- Finite, positive RUL values that do not increase as the fault advances.
- Supported kurtosis modes and resolvable references.
- Valid, non-duplicated dominance lists.

Dormant imbalance and misalignment rules remain permitted without detection
blocks. This is deliberate because the canonical schema does not yet publish
their required 1X/2X features.

### Decision 4: deterministic boundary semantics

- Stage thresholds use `>=`; an exact stage boundary belongs to that stage.
- An `above` kurtosis gate uses strict `>`; equality is not evidence above the
  gate.
- If two dominant bands have exactly equal values and both rules match, the
  configured unique priority decides the primary result. Other evidence remains
  available as a differential.
- Missing unrelated optional bands do not block a valid diagnosis.
- Missing vibration baseline or missing temperature rise prevents a false
  lubrication diagnosis.
- A malformed batch item produces its own `invalid_input` result and does not
  abort valid items in the same batch.

### Decision 5: diagnosis provenance is additive

Every result now contains:

```text
schema_version = 1.1
fi_config_version
taxonomy_version
source_schema_version
source_config_version
source_master_data_version
source_monitoring_config_version
source_detector_version
```

Configuration and taxonomy versions are stable SHA-256-derived identifiers.
The upstream values link the diagnosis back through Agents 2 and 1.

### Decision 6: persistence remains case-level

No Agent 3-specific SQLite database was added. The orchestrator/API owns the
full maintenance case and is the correct place to persist the linked anomaly,
diagnosis, risk, and later decisions. This avoids several partially synchronized
databases while keeping Agent 3 deterministic and independently testable.

## Expected Input

Agent 3 receives the Agent 2 anomaly and the exact Agent 1 trusted signal from
which that anomaly was created:

```python
diagnosis = failure_agent.process(anomaly_event, trusted_signal)
```

Required relationship:

```text
anomaly.asset_id    == trusted.raw.asset_id
anomaly.bearing_id  == trusted.raw.bearing_id
anomaly.channel_id  == trusted.raw.channel_id   (when supplied)
anomaly.timestamp   == trusted.raw.timestamp    (when supplied)
trusted.downstream_eligible == true
```

Agent 3 uses BPFO, BPFI, BSF, FTF, kurtosis, temperature, vibration, bearing
baselines, anomaly evidence/confidence, and asset criticality.

## Expected Output

Example diagnosed result:

```json
{
  "case_id": "ANOM-TEL_0020",
  "asset_id": "AST_GBX_001",
  "bearing_id": "BRG_011",
  "fault_mode": "rolling_element_fault",
  "fault_code": "FT_007",
  "iso_stage": 3,
  "severity": "critical",
  "confidence": 0.93,
  "diagnosis_status": "diagnosed",
  "diagnostic_eligible": true,
  "rul_days_estimate": 10,
  "evidence": {
    "dominant_band": "BSF"
  },
  "recommended_checks": [],
  "fi_config_version": "16-character-version",
  "taxonomy_version": "16-character-version"
}
```

Example valid but unmatched result:

```json
{
  "fault_mode": "undetermined",
  "fault_code": "",
  "iso_stage": 0,
  "diagnosis_status": "undetermined",
  "diagnostic_eligible": true,
  "status_reason": "no active taxonomy rule matched the anomaly evidence"
}
```

Example invalid handoff:

```json
{
  "fault_mode": "undetermined",
  "diagnosis_status": "invalid_input",
  "diagnostic_eligible": false,
  "confidence": 0.0,
  "status_reason": "anomaly and trusted signal identities do not match"
}
```

## Complete test catalogue summary

Agent 3 now has 70 focused and boundary tests:

| Suite | Cases | Coverage |
|---|---:|---|
| `test_failure_intelligence.py` | 9 | Real Agent 1 -> 2 -> 3 fault scenarios and core matcher boundaries |
| `test_failure_intelligence_hardening.py` | 19 | Handoff safety, roadmap vocabulary, provenance, taxonomy validation, serialization, batch behavior |
| `test_failure_intelligence_edge_cases.py` | 36 | NaN/infinity, score ranges, identity, exact gates, ties, missing inputs, malformed batches/config/taxonomy |
| `test_agent2_agent3_integration.py` | 6 | Anomaly, healthy, suppressed, ineligible, provenance, and BSF handoffs |
| **Total** | **70** | |

Current focused result:

```text
70 passed
```

Full repository regression after the strict edge-case pass:

```text
244 passed, 2 skipped, 0 failed
```

The two skips are existing optional tests; Agent 3 introduced no skipped cases
and no repository regressions.

Run with:

```powershell
python -m pytest tests/test_failure_intelligence.py tests/test_failure_intelligence_hardening.py tests/test_failure_intelligence_edge_cases.py tests/test_agent2_agent3_integration.py -q
```

## Completion status

Agent 3 is complete for the deterministic development/pilot scope. Future
history-aware enhancements from the roadmap—trend duration, band-energy growth,
and maintenance-history-assisted diagnosis—should be added only when those
inputs become part of the typed upstream contract. They must not be fabricated
from a single telemetry record.

## 2026-07-20 - Three-agent integration certification

The combined Agent 1 -> Agent 2 -> Agent 3 boundary was subsequently expanded
to 46 integration tests, including 32 complete-chain cases. The authoritative
matrix, input/output formats, persistence decisions, and case catalogue are in
`Agent 1-2-3 Integration.md`.

Post-integration result:

```text
Integration suites: 46 passed
Full repository: 276 passed, 2 skipped, 0 failed
```

<!-- *********************** -->
