<!-- ************** Added by Prateek Mittal on 17th July 2026 ****************** -->
<!-- Running change record for Agent 2: Monitoring Agent. -->

# Agent 2 Changes

This is the permanent change record for Agent 2, the Monitoring Agent. Every
Agent 2 implementation change, contract decision, test, and verification result
must be recorded here before Agent 2 is considered complete.

## 2026-07-17 - Baseline audit

### Agent responsibility

Agent 2 answers:

> Is this trusted bearing signal behaving abnormally for its operating regime?

Current contract:

```text
TrustedBearingSignal -> MonitoringAgent.process() -> AnomalyEvent | None
```

`None` currently represents several different outcomes: healthy, startup
suppression, idle suppression, missing baseline, and insufficient usable
signals. These outcomes are behaviorally different but are not distinguishable
in the current contract.

### Current implementation

The existing Monitoring Agent performs:

1. Startup/shutdown suppression.
2. Operating-regime classification using state, RPM, load, and rated RPM.
3. Exclusion of missing and configured imputed fields.
4. Reduced-dimension Hotelling T-squared when some signals are unavailable.
5. Covariance reconstruction from per-bearing standard deviations and
   correlations.
6. Diagonal covariance fallback when inversion fails.
7. Chi-square CDF normalization to a 0-1 anomaly score.
8. Per-signal z-scores for evidence.
9. Confidence scoring from sensor quality, signal coverage, and Agent 1 data
   quality.
10. EWMA slow-drift detection with JSON-backed state.
11. Deterministic plain-language anomaly explanation.
12. `AnomalyEvent` emission when Hotelling T-squared or EWMA fires.

### Current utilities

| Component | Current form | Purpose |
|---|---|---|
| Baseline feature mathematics | `src/tools/baseline_features.py` | T-squared, covariance, z-score, EWMA, regime and explanation functions |
| EWMA state store | `src/tools/ewma_store.py` | JSON load/save with a process-local lock |
| Monitoring configuration | `config/monitoring_config.json` | Detector thresholds, signals, regimes and confidence weights |
| Output schema | `src/schemas/anomaly.py` | Agent 2 to Agent 3 anomaly contract |

These should remain deterministic utilities rather than autonomous LLM agents.

### Current detection design

#### Hotelling T-squared

The primary detector evaluates the correlated signal vector:

```text
[vib_rms_mm_s, kurtosis, temp_c, bpfo_energy]
```

It compares the current vector with per-bearing healthy means and covariance.
The resulting statistic is normalized with the chi-square CDF. The default
anomaly threshold is `0.85`.

#### EWMA

EWMA runs in parallel and is intended to detect gradual drift. Current state is
keyed by `bearing_id` and signal name and stored in `data/ewma_state.json`.

#### Combined verdict

Either detector may emit an anomaly:

```text
hotelling_t2_fired OR ewma_fired -> AnomalyEvent
```

The combined anomaly score is the maximum detector score.

### Existing input

Agent 2 receives `TrustedBearingSignal` from Agent 1 and uses:

- `raw` telemetry values and operating state.
- `asset_ctx.rated_rpm`.
- `bearing_ctx` means, standard deviations, and correlations.
- `data_quality_score`.
- `validation_status` in evidence only.
- `imputed_fields` for exclusion.
- `signal_quality_score` for confidence.

### Existing output

When an anomaly fires, Agent 2 returns `AnomalyEvent` containing:

- Case, asset, bearing, channel, and event identifiers.
- Anomaly and confidence scores.
- Largest absolute z-score.
- Triggered signal list.
- Plain-language reason.
- Operating regime.
- Detector and per-signal evidence.
- Baseline reference.
- Processing timestamp.

When no anomaly fires, it returns `None`.

### Existing tests

The authoritative baseline suite is `tests/test_monitoring.py`.

Current result:

```text
27 passed
```

Existing coverage includes:

- Healthy false-positive checks.
- Early and severe outer-race detection.
- Early and severe inner-race detection.
- Lubrication healthy/fault differentiation.
- Gearbox anomaly detection.
- Startup and idle suppression.
- Imputed-signal exclusion.
- Reduced-dimension T-squared.
- Confidence range and serialization.
- Regime output.
- Batch behavior.
- Chi-square CDF checks.
- Healthy/fault T-squared separation.
- Covariance symmetry and correlated-deviation behavior.

## Roadmap comparison

| Roadmap expectation | Current state |
|---|---|
| Consume `TrustedBearingSignal` | Implemented |
| Per-asset baseline | Implemented through bearing master |
| Per-operating-regime baseline | Partial: regime is classified, but one static baseline is used |
| Rolling vibration/temperature/kurtosis features | Partial: EWMA exists; no general rolling-window feature set |
| RPM/load regime classifier | Implemented |
| Statistical anomaly score | Implemented with Hotelling T-squared and EWMA |
| Evidence and confidence | Implemented |
| Healthy data produces low scores | Tested indirectly through `None` verdict |
| Fault data produces high scores | Implemented and tested |
| False-positive rate is measured | Partial: bundled healthy rows are checked; no metric/report contract |
| Persist monitoring decisions | Missing |
| Safe stateful streaming behavior | Partial |
| Explainable and auditable detector version | Missing provenance fields |

## Completion gaps

### 1. Agent 1 eligibility is not enforced inside Agent 2

The orchestrator now prevents ineligible Agent 1 outputs from reaching Agent 2,
but direct calls to `MonitoringAgent.process()` do not verify
`downstream_eligible`. Agent 2 should defend its own input contract.

### 2. `None` conflates healthy and not-assessed outcomes

The caller cannot distinguish:

- Healthy and assessed.
- Startup suppressed.
- Idle/stopped suppressed.
- Missing bearing context.
- Insufficient signals.
- Ineligible Agent 1 input.

A structured monitoring decision/result is needed while preserving the current
`AnomalyEvent | None` compatibility API where appropriate.

### 3. EWMA state is vulnerable to duplicates and late events

The current state update does not use Agent 1 duplicate/out-of-order metadata.
Replayed or late telemetry can contaminate the EWMA trajectory.

### 4. EWMA read-modify-write is not atomic

`load_state()` and `save_state()` each acquire a lock independently. Two
threads may both load the same state, update independently, and overwrite each
other. State mutation must be atomic at the repository operation level.

### 5. EWMA state is not regime-specific

State is keyed by bearing and signal only. Low-load, rated-load, and high-load
observations may update the same EWMA despite having different normal behavior.

### 6. EWMA state has no baseline/config version

Changing a bearing baseline, signal set, alpha, or configuration can leave old
state active under new rules. State should reset or isolate by detector and
baseline version.

### 7. Corrupt state is silently discarded

Invalid JSON currently becomes `{}` without an auditable warning or quarantine
decision. Recovery behavior should be explicit and testable.

### 8. Covariance fallback evidence is incorrect

After diagonal fallback, `cov_inv` is no longer `None`, so current evidence:

```python
"cov_inv_fallback": cov_inv is None
```

always reports `false`. A separate fallback flag is required.

### 9. Anomaly provenance is incomplete

`AnomalyEvent` lacks:

- Schema version.
- Monitoring configuration version.
- Baseline/master-data version inherited from Agent 1.
- Detector version.
- Source Agent 1 telemetry/config identifiers.

### 10. Alert lifecycle controls are absent

Repeated anomalous readings can produce repeated independent anomaly events.
The current agent has no configurable debounce, persistence count, cooldown, or
alert deduplication policy.

### 11. Monitoring decisions are not persisted

Only EWMA state is persisted. Healthy, suppressed, insufficient-data, and
anomalous decisions are not stored as an auditable monitoring stream.

### 12. Configuration validation is incomplete

Additional checks are needed for:

- Confidence weights summing to one.
- Every detector signal having baseline mappings.
- EWMA signals being a valid subset.
- Signal-order/correlation dimensional consistency.
- Positive trigger and regime thresholds.

## Proposed Agent 2 utility structure

```text
Monitoring Agent
├── Input Eligibility Guard
├── Operating Regime Classifier
├── Signal Coverage Resolver
├── Hotelling T-squared Detector
├── EWMA Drift Detector
├── Monitoring State Repository
├── Alert Policy Evaluator
├── Explanation Builder
└── Monitoring Decision Repository
```

These are deterministic utilities/services. No LLM utility agent is required.

## Planned completion sequence

1. Introduce a structured `MonitoringResult` that records assessed, healthy,
   suppressed, insufficient-data, and anomalous outcomes.
2. Preserve `process()` as the compatibility method returning
   `AnomalyEvent | None` and add a detailed decision method.
3. Enforce Agent 1 downstream eligibility inside Agent 2.
4. Replace JSON read/save calls with an atomic monitoring-state repository.
5. Protect EWMA from duplicate and out-of-order updates.
6. Isolate state by bearing, operating regime, and detector/baseline version.
7. Fix covariance fallback evidence.
8. Add anomaly and decision provenance.
9. Add configurable alert persistence/debounce behavior without overbuilding a
   full incident-management system.
10. Add durable monitoring-decision persistence for development/pilot use.
11. Strengthen configuration validation.
12. Expand pytest coverage and run full regression tests.
13. Add expected input/output examples and a complete test catalogue.

## Attribution convention

Every new Agent 2 code section will use:

```python
# ************** Added by Prateek Mittal on 17th July 2026 ******************
# Explanation of the added Agent 2 behavior.
...
# ***********************
```

JSON configuration additions will use equivalent `_comment` fields so the file
remains valid JSON.

## 2026-07-17 - Completion implementation

### Structured monitoring result

Added `MonitoringResult` in `src/schemas/anomaly.py`. The new `assess()` API
distinguishes:

- `healthy`
- `anomaly`
- `suppressed`
- `ineligible`
- `duplicate`
- `out_of_order`
- `insufficient_data`
- `pending_alert`
- `cooldown`

The existing `process()` API remains compatible and returns only
`AnomalyEvent | None` by delegating to `assess()`.

### Defensive Agent 1 handoff

Agent 2 now independently checks `downstream_eligible`, `duplicate_detected`,
and `out_of_order` before touching detector state. Ineligible, duplicate, and
late inputs cannot contaminate EWMA.

### Atomic and versioned monitoring state

Added `mutate_state()` to `src/tools/ewma_store.py`. It holds the lock across
the complete read-modify-write cycle, uses replace semantics where available,
and reports corrupt-state recovery.

EWMA state is isolated by:

```text
state namespace/config version/master-data version
  -> bearing_id
    -> operating regime
      -> signal EWMA
```

This prevents state reuse across changed baselines/configuration and avoids
mixing different operating regimes.

### Alert policy

Agent 2 now supports configurable:

- `min_consecutive_anomalies`
- `cooldown_seconds`

Defaults preserve existing immediate alert behavior (`1` and `0`). Detector
evidence is unchanged; the policy only controls anomaly-event emission.

### Detector evidence correction

The covariance fallback flag is now captured before diagonal replacement, so
`evidence.cov_inv_fallback` correctly reports singular-covariance fallback.

### Provenance

`AnomalyEvent` and `MonitoringResult` now include:

- Agent 2 schema version.
- Monitoring configuration version.
- Detector version.
- Source Agent 1 schema version.
- Source Agent 1 configuration version.
- Source Agent 1 master-data version.

### Durable monitoring decisions

Added `SQLiteMonitoringDecisionRepository` in
`src/tools/monitoring_decision_repository.py`. It persists all detailed
decisions, including healthy and suppressed outcomes.

Use:

```python
agent = MonitoringAgent.with_sqlite_repository("data/monitoring_decisions.db")
result = agent.assess_and_store(trusted_signal)
```

Repository failures are surfaced through `persistence_status="failed"` and a
visible reason.

### Configuration hardening

Monitoring configuration validation now checks:

- Confidence weights sum to one.
- Positive z-score threshold.
- Every detector signal has a baseline mapping.
- Every EWMA signal has a baseline mapping.
- Minimum consecutive anomaly count is at least one.
- Cooldown is non-negative.

## Expected Input

Agent 2 accepts the `TrustedBearingSignal` produced by Agent 1. A minimal
eligible example is conceptually:

```json
{
  "raw": {
    "telemetry_id": "TEL-1001",
    "timestamp_utc": "2026-07-17T10:00:00Z",
    "asset_id": "AST_MTR_002",
    "bearing_id": "BRG_003",
    "channel_id": "CH_003",
    "rpm": 1768.0,
    "load_pct": 70.0,
    "machine_state": "running",
    "startup_shutdown_flag": false,
    "vib_rms_mm_s": 1.9,
    "kurtosis": 2.1,
    "temp_c": 53.0,
    "bpfo_energy": 0.6,
    "signal_quality_score": 1.0
  },
  "asset_ctx": {"rated_rpm": 1765},
  "bearing_ctx": {
    "baseline_vib_rms_mean": 1.8,
    "baseline_vib_rms_std": 0.2,
    "baseline_kurtosis_mean": 2.2,
    "baseline_kurtosis_std": 0.3,
    "baseline_temp_mean": 52.0,
    "baseline_temp_std": 1.5,
    "baseline_bpfo_mean": 0.6,
    "baseline_bpfo_std": 0.1
  },
  "data_quality_score": 1.0,
  "downstream_eligible": true,
  "duplicate_detected": false,
  "out_of_order": false,
  "schema_version": "1.2",
  "config_version": "agent-1-config-version",
  "master_data_version": "master-version"
}
```

## Expected Output

### Detailed healthy outcome

```json
{
  "telemetry_id": "TEL-1001",
  "status": "healthy",
  "assessed": true,
  "suppression_reason": "",
  "anomaly_event": null,
  "regime": "running_at_rated_speed",
  "usable_signals": ["vib_rms_mm_s", "kurtosis", "temp_c", "bpfo_energy"],
  "state_recovered": false,
  "persistence_status": "not_requested"
}
```

### Detailed anomaly outcome

```json
{
  "status": "anomaly",
  "assessed": true,
  "anomaly_event": {
    "case_id": "ANOM-BRG_001-2026-07-17T10:00:00Z",
    "anomaly_score": 0.99,
    "confidence_score": 0.96,
    "triggered_features": ["vib_rms_mm_s", "bpfo_energy"],
    "reason": "Deterministic explanation",
    "evidence": {
      "triggered_methods": ["hotelling_t2", "ewma"],
      "cov_inv_fallback": false,
      "state_recovered": false
    },
    "schema_version": "2.0",
    "monitoring_config_version": "16-character-version"
  }
}
```

### Suppressed outcome

```json
{
  "status": "suppressed",
  "assessed": false,
  "suppression_reason": "startup_shutdown",
  "anomaly_event": null
}
```

### Ineligible Agent 1 outcome

```json
{
  "status": "ineligible",
  "assessed": false,
  "suppression_reason": "record requires review or remediation before Monitoring",
  "anomaly_event": null
}
```

### Alert-policy outcomes

```json
{"status": "pending_alert", "anomaly_event": null}
```

```json
{"status": "cooldown", "anomaly_event": null}
```

## Complete Test Case Catalogue

The authoritative executable suite is `tests/test_monitoring.py` and contains
44 tests.

### Original baseline tests (1-27)

| # | Test case | Expected behavior |
|---:|---|---|
| 1 | `test_healthy_returns_none` | Compatibility API emits no anomaly for healthy input. |
| 2 | `test_no_false_positives_on_any_healthy_row` | All bundled healthy rows remain anomaly-free. |
| 3 | `test_outer_race_worst_is_anomaly` | Severe outer-race degradation is detected. |
| 4 | `test_outer_race_first_is_anomaly_with_t2` | T-squared detects early outer-race degradation. |
| 5 | `test_outer_race_evidence` | Outer-race event contains detector and signal evidence. |
| 6 | `test_inner_race_early_detection` | Early inner-race degradation is detected. |
| 7 | `test_inner_race_worst_is_anomaly` | Severe inner-race degradation is detected. |
| 8 | `test_lubrication_earliest_healthy` | Initial healthy lubrication record stays healthy. |
| 9 | `test_lubrication_fault_detected` | Developed lubrication degradation is detected. |
| 10 | `test_gearbox_all_anomalous` | All bundled gearbox-fault records are detected. |
| 11 | `test_startup_is_suppressed` | Startup telemetry does not emit an anomaly. |
| 12 | `test_idle_machine_is_suppressed` | Idle telemetry does not emit an anomaly. |
| 13 | `test_imputed_field_excluded_from_scoring` | Imputed fields do not influence T-squared. |
| 14 | `test_fully_imputed_record_raises_no_anomaly` | A record without real usable signals emits nothing. |
| 15 | `test_reduced_t2_when_signal_missing` | Reduced-dimensional T-squared works with one missing signal. |
| 16 | `test_event_confidence_in_range` | Confidence remains within 0-1. |
| 17 | `test_event_is_serialisable` | Anomaly output is JSON serializable. |
| 18 | `test_regime_label_populated` | Anomaly includes its operating regime. |
| 19 | `test_batch_returns_only_anomaly_events` | Compatibility batch returns anomaly events only. |
| 20 | `test_batch_healthy_produces_no_events` | Healthy batch produces an empty anomaly list. |
| 21 | `test_chi2_cdf_known_percentiles` | Chi-square CDF matches known values. |
| 22 | `test_chi2_cdf_zero_returns_zero` | Zero statistic returns zero probability. |
| 23 | `test_chi2_cdf_all_p_values` | CDF is monotonic across supported dimensions. |
| 24 | `test_t2_healthy_below_threshold` | Healthy vector scores below threshold. |
| 25 | `test_t2_fault_above_threshold` | Fault vector scores above threshold. |
| 26 | `test_covariance_matrix_is_symmetric` | Constructed covariance is symmetric. |
| 27 | `test_t2_greater_than_diagonal_for_correlated_deviation` | Correlated T-squared highlights unusual joint patterns. |

### Completion tests (28-44)

| # | Test case | Expected behavior |
|---:|---|---|
| 28 | `test_assess_healthy_is_distinct_from_suppressed` | Detailed API identifies assessed healthy input. |
| 29 | `test_assess_startup_reports_suppression_reason` | Startup suppression is explicit. |
| 30 | `test_ineligible_agent1_input_is_defensively_rejected` | Ineligible input is rejected without state mutation. |
| 31 | `test_duplicate_does_not_update_state` | Duplicate input cannot contaminate state. |
| 32 | `test_out_of_order_does_not_update_state` | Late input cannot contaminate state. |
| 33 | `test_insufficient_signals_has_structured_result` | Insufficient coverage is explicit. |
| 34 | `test_anomaly_and_result_include_provenance` | Decision and anomaly carry traceable versions. |
| 35 | `test_config_version_is_stable` | Equivalent configuration produces a stable identifier. |
| 36 | `test_corrupt_state_recovery_is_audited` | Corrupt state is recovered and reported. |
| 37 | `test_state_is_namespaced_by_version_bearing_and_regime` | State isolation hierarchy is correct. |
| 38 | `test_singular_covariance_reports_fallback` | Diagonal fallback evidence is accurate. |
| 39 | `test_minimum_consecutive_alert_policy` | Persistence policy delays emission until configured count. |
| 40 | `test_alert_cooldown_suppresses_repeated_emission` | Cooldown prevents repeated immediate alerts. |
| 41 | `test_assess_and_store_persists_decision` | Detailed decision is stored and retrievable. |
| 42 | `test_assess_and_store_requires_repository` | Durable API requires an explicit repository. |
| 43 | `test_decision_persistence_failure_is_visible` | Persistence failure remains visible to callers. |
| 44 | `test_monitoring_config_rejects_invalid_confidence_weights` | Invalid confidence policy fails configuration validation. |

### Verification results

```text
Agent 2: 44 passed
Full repository: 172 passed, 3 failed, 2 skipped
```

The three failures are the existing Agent 3/4 gearbox taxonomy and RUL
expectation mismatches. Agent 2 introduced no new regression.

## Agent 2 completion boundary

Agent 2 is complete for the current deterministic development/pilot scope:

- Defensive Agent 1 contract enforcement.
- Structured healthy/suppressed/insufficient/anomalous outcomes.
- Hotelling T-squared and EWMA detection.
- Atomic, regime-specific, versioned state.
- Duplicate and late-event state protection.
- Configurable alert persistence and cooldown.
- Explainable evidence and corrected fallback reporting.
- Detector and source provenance.
- Durable monitoring-decision audit.
- Backward-compatible anomaly-only API.
- Comprehensive pytest coverage.

## Agent 1 to Agent 2 integration closure

Added the dedicated suite `tests/test_agent1_agent2_integration.py`. It tests
the real repository-backed handoff rather than only isolated agent calls.

| # | Integration test | Certified behavior |
|---:|---|---|
| 1 | `test_healthy_durable_flow` | Agent 1 stores a valid signal and Agent 2 stores an assessed healthy decision. |
| 2 | `test_fault_durable_flow_emits_anomaly` | A valid fault passes both durable layers and emits a linked anomaly. |
| 3 | `test_flagged_agent1_record_is_not_assessed` | Agent 1 `FLAGGED` output cannot update Agent 2 detector state. |
| 4 | `test_rejected_agent1_record_stops_before_detection` | Agent 1 rejection remains auditable and never reaches detection. |
| 5 | `test_startup_is_explicitly_suppressed` | Valid startup input reaches Agent 2 and receives an explicit suppression decision. |
| 6 | `test_duplicate_durable_event_does_not_change_monitoring_state` | Agent 1 duplicate detection prevents any Agent 2 state mutation. |
| 7 | `test_out_of_order_durable_event_does_not_change_monitoring_state` | Agent 1 late-event detection prevents any Agent 2 state mutation. |
| 8 | `test_provenance_and_repository_records_are_linked` | Telemetry ID and Agent 1 provenance link both repository records and the anomaly. |

The integration suite also established that the specific `duplicate` and
`out_of_order` outcomes take precedence over the general `ineligible` outcome
inside Agent 2.

### Final closure verification

```text
Agent 1 + Agent 2 + integration suites: 112 passed
Dedicated Agent 1 -> Agent 2 integration: 8 passed
Full repository: 180 passed, 3 failed, 2 skipped
```

The three failures remain the pre-existing downstream gearbox taxonomy/RUL
expectation mismatches. Agent 1, Agent 2, and their integration boundary are
fully green.

<!-- *********************** -->
