# DRO — Data Foundation, Monitoring & Failure Intelligence Agents

End-to-end developer reference for the first three agents in the Digital Reliability Operations (DRO) pipeline.

**Phase 2** — Data Foundation Agent: validates and enriches every inbound bearing telemetry record.
**Phase 3** — Monitoring Agent: detects anomalies on the validated stream using Hotelling T² (point-in-time) and EWMA (drift).
**Phase 4** — Failure Intelligence Agent: classifies each anomaly into a named fault, ISO stage, and severity using the fault taxonomy.

If you're new to the codebase, read §1 first, then §2 (DFA), §3 (Monitoring), or §3A (Failure Intelligence) depending on what you're touching.

---

## 1. End-to-End Pipeline Summary

```
                ┌────────────────────────────┐
                │ Raw telemetry record (dict) │   from historian / file / Kafka / SQL
                └─────────────┬───────────────┘
                              │
                              ▼
   ┌───────────────────────────────────────────────────────────────────┐
   │  DataFoundationAgent.process(raw_dict)        [Phase 2]           │
   │                                                                   │
   │   parse → asset lookup → bearing lookup → range checks            │
   │     → accuracy → consistency → completeness → enrich              │
   │     → composite data_quality_score → VALID / FLAGGED / REJECTED   │
   │                                                                   │
   │   Output: TrustedBearingSignal                                    │
   │     • raw                  (original BearingSignalFact)           │
   │     • asset_ctx, bearing_ctx (joined master rows + baselines)     │
   │     • validation           (per-check pass/fail + reasons)        │
   │     • data_quality_score   (0–1, six DAMA dimensions)             │
   │     • validation_status    (VALID / FLAGGED / REJECTED)           │
   │     • quality_report       (per-dimension breakdown + fix hints)  │
   │     • imputed_fields       (if remediation ran)                   │
   └─────────────┬─────────────────────────────────────────────────────┘
                 │   (only records where is_processable == True flow on)
                 ▼
   ┌───────────────────────────────────────────────────────────────────┐
   │  MonitoringAgent.process(trusted)              [Phase 3]          │
   │                                                                   │
   │   suppress startup/non-running → collect usable signals           │
   │     → Hotelling T² on covariance sub-matrix                       │
   │     → EWMA control chart per signal (state persisted)             │
   │     → confidence (sensor + coverage + DQ)                         │
   │     → emit if T² fired OR EWMA fired                              │
   │                                                                   │
   │   Output: AnomalyEvent  (or None when healthy)                    │
   │     • anomaly_score    (max of T² score and EWMA score, 0–1)      │
   │     • confidence_score (0–1)                                      │
   │     • triggered_features (which signals breached)                 │
   │     • evidence         (T² stats, EWMA stats, regime, methods)    │
   └─────────────┬─────────────────────────────────────────────────────┘
                 │
                 ▼
        [Phase 4] Failure Intelligence Agent — classifies the fault from
        band energies + kurtosis + fault_taxonomy.json (see §3A).
```

### What changes hands between the agents

| Contract field | Set by DFA | Consumed by Monitoring | Why |
|---|---|---|---|
| `raw` | yes | yes | The actual sensor values |
| `asset_ctx.rated_rpm` | enrichment | regime classifier | Distinguish at-rated vs partial-load running |
| `bearing_ctx.baseline_*` | enrichment | T² + EWMA + z-scores | All baseline statistics for scoring |
| `bearing_ctx.vib/temp_min_valid/max_valid` | enrichment | (already used by DFA) | Per-bearing sensor bounds |
| `validation_status` | DFA verdict | filter (is_processable) | REJECTED records never reach Monitoring |
| `data_quality_score` | DFA | confidence weight | Low-quality data → low-confidence anomaly |
| `imputed_fields` | remediation step | excluded from T²/EWMA | We never alarm on a value we invented |

### Where the configuration lives

| Config file | Loaded by | Controls |
|---|---|---|
| [config/dfa_config.json](../config/dfa_config.json) | `DFAConfig` | DFA thresholds, weights, critical fields, remediation policy |
| [config/monitoring_config.json](../config/monitoring_config.json) | `MonitoringConfig` | Anomaly threshold, T² signal order, EWMA alpha/L, regime rules |

Edit JSON, restart the process. No code change for tuning.

### Where the reference data lives

| Data file | Loaded by | Contains |
|---|---|---|
| [data/asset_master.json](../data/asset_master.json) | `load_asset_master` | Asset metadata: type, criticality, rated RPM, downtime cost |
| [data/bearing_master.json](../data/bearing_master.json) | `load_bearing_master` | Bearing metadata, sensor bounds, baseline statistics |
| [data/fault_taxonomy.json](../data/fault_taxonomy.json) | Failure Intelligence Agent | Fault classification rules (Phase 4) |
| [data/telemetry_scenarios.json](../data/telemetry_scenarios.json) | `load_telemetry_rows` | Test telemetry — replaced by live source in production |
| [data/ewma_state.json](../data/ewma_state.json) | `ewma_store` | **Auto-generated** — per-bearing EWMA values, persists across runs |
| [data/curated_signals.json](../data/curated_signals.json) | `curated_store` | **Auto-generated** — validated records routed downstream |

---

## 2. Data Foundation Agent (Phase 2)

**Source file:** [src/agents/data_foundation_agent.py](../src/agents/data_foundation_agent.py)
**Role:** Gatekeeper between the raw historian feed and every downstream agent.

### 2.1 Purpose

Every raw telemetry record must pass through `DataFoundationAgent.process()` before the Monitoring Agent sees it. The DFA has three jobs:

1. **Validate** — confirm the record is parseable, the identity keys resolve, the values are physically plausible, and the timestamp is fresh.
2. **Enrich** — join asset and bearing master context onto the signal so downstream agents do not need to hit the masters again.
3. **Score & classify** — compute a composite `data_quality_score` and label the record `VALID`, `FLAGGED`, or `REJECTED`.

It never raises. Every failure is captured inside the returned `TrustedBearingSignal` so the pipeline keeps moving and the audit trail is intact.

### 2.2 Class overview

```
DataFoundationAgent
├── __init__(asset_lookup, bearing_lookup, channel_lookup, cfg=None)
├── process(raw: dict) -> TrustedBearingSignal
├── process_batch(raw_records: list) -> list
└── from_data_files() -> DataFoundationAgent     # factory
```

The agent is **stateless**. Lookups and config are injected at construction — instantiate once at startup, call per record. Safe to share across threads.

### 2.3 Input — `BearingSignalFact` (raw dict)

Defined in [src/schemas/bearing_signal.py](../src/schemas/bearing_signal.py).

#### Required (identity)

| Field | Type | Notes |
|---|---|---|
| `telemetry_id` | str | Unique record id |
| `timestamp_utc` | str (ISO-8601) | When the sample was taken |
| `asset_id` | str | Must resolve in `asset_master.json` |
| `bearing_id` | str | Must resolve in `bearing_master.json` |
| `channel_id` | str | Must resolve to the **same** bearing row as `bearing_id` |

Missing any of these → `from_dict()` raises → record is **REJECTED**.

#### Optional payload

| Group | Fields | Units |
|---|---|---|
| Operating | `rpm`, `load_pct`, `machine_state`, `startup_shutdown_flag` | rpm, %, enum, bool |
| Vibration | `vib_rms_mm_s`, `vib_peak_g`, `kurtosis` | mm/s, g, ratio |
| Thermal | `temp_c` | °C |
| Band energies | `bpfo_energy`, `bpfi_energy`, `bsf_energy`, `ftf_energy`, `envelope_peak_hz` | non-negative |
| Electrical | `motor_current_a`, `voltage_v`, `current_deviation` | A, V, % |
| Data quality | `signal_quality_score` (0–1), `data_source` | — |

`null` / `None` in the JSON is treated identically to "key absent" by the completeness check.

### 2.4 Output — `TrustedBearingSignal`

| Field | Type | Populated when |
|---|---|---|
| `raw` | `BearingSignalFact` | Always |
| `asset_ctx` | `AssetContext \| None` | Both mapping checks passed |
| `bearing_ctx` | `BearingContext \| None` | Both mapping checks passed |
| `validation` | `ValidationDetail` | Always |
| `data_quality_score` | float in `[0.0, 1.0]` | Always |
| `validation_status` | `VALID` / `FLAGGED` / `REJECTED` | Always |
| `quality_report` | `QualityReport` | Always (with per-dimension breakdown) |
| `processed_at` | ISO-8601 UTC | Always |
| `remediation_action` | `""` / `IMPUTE` / `DROP` / `KEEP` | Only if remediation ran |
| `imputed_fields` | `list[str]` | If imputation happened |
| `imputation_method` | str | e.g. `"baseline_mean"` |

Downstream check: `TrustedBearingSignal.is_processable` → `True` when status is `VALID` or `FLAGGED`.

### 2.5 Pipeline — `process()` step by step

| Step | What | Outcome on failure |
|---|---|---|
| 1 | Parse raw dict → `BearingSignalFact` | REJECTED |
| 2 | Asset mapping (`validate_asset_id`) | REJECTED |
| 3 | Bearing + channel mapping (`validate_bearing_id`) | REJECTED |
| 4 | Value ranges (`validate_ranges`) | adds substantive reason |
| 5 | Accuracy (`compute_accuracy_score`) | scores 0–1 from historian's `signal_quality_score` |
| 6 | Consistency (`check_consistency`) | adds substantive reason |
| 7 | Completeness (`compute_completeness_score`) | adds substantive reason |
| 8 | Enrich (`build_asset_context`, `build_bearing_context`) | — |
| 9 | Composite quality score (`compute_data_quality_score`) | — |
| 10 | Status classification | VALID / FLAGGED |
| 11 | Build `QualityReport` for audit | — |

Steps 2-3 are **hard rejections** with early returns. Steps 4-7 contribute to the score but don't reject — they get caught by the threshold in Step 10.

#### Status classification (Step 10)

```python
substantive_reasons = [r for r in validation.reasons
                       if no advisory keyword in r]

if dq_score >= cfg.flagged_threshold and not substantive_reasons:
    status = VALID
else:
    status = FLAGGED
```

Advisory keywords (from config): `startup_shutdown` and similar tags that don't impair downstream processing. Startup/shutdown records always stay VALID, with a note telling Monitoring to suppress anomaly detection.

### 2.6 Configuration — `dfa_config.json`

Key blocks:

| Block | Controls |
|---|---|
| `validation` | RPM/load/kurtosis bounds, critical fields, advisory reason keywords |
| `consistency` | running/stopped state lists, RPM/vibration thresholds |
| `quality_score` | `flagged_threshold` (0.80), six dimension weights, labels, descriptions, improvement hints |
| `remediation` | default action, imputable fields → baseline-stat mapping, never-impute list |
| `fallback_sensor_bounds` | Used only when BearingMaster has no per-bearing bounds |
| `logging` | What to log (rejected / flagged / valid) |

Loaded once at startup via `load_config()` from [src/tools/config_loader.py](../src/tools/config_loader.py). Validated on load — weights must sum to 1.0, threshold in (0,1], critical fields non-empty.

### 2.7 The six DAMA quality dimensions

| Dimension | Weight | DAMA bucket | Hard/Soft |
|---|---|---|---|
| `asset_mapping` | 0.25 | Integrity | Hard — failure REJECTED |
| `bearing_mapping` | 0.20 | Integrity | Hard — failure REJECTED |
| `completeness` | 0.20 | Completeness | Soft — continuous score |
| `ranges_valid` | 0.15 | Validity / Conformity | Soft — per-bearing bounds preferred |
| `accuracy` | 0.10 | Accuracy | Soft — uses historian's `signal_quality_score` |
| `consistency` | 0.10 | Consistency | Soft — cross-field rules (state vs rpm vs vib) |

```
data_quality_score = Σ (weight_i × score_i)
```

#### Per-null completeness math

```
completeness = 1.0 - (missing_critical_fields / total_critical_fields)
```

With 5 critical fields and weight 0.20, each missing field drops the composite by `0.20 / 5 = 0.04`. See [src/tools/validators.py:284](../src/tools/validators.py#L284).

### 2.8 Quality Report

`QualityReport` ([src/schemas/bearing_signal.py](../src/schemas/bearing_signal.py)) pairs every dimension score with the reason it failed and a concrete fix hint pulled from `dfa_config.json → improvement_hints`.

- `quality_report.dimensions` — one `QualityDimension` per axis
- `quality_report.failed_dimensions` — only the ones that didn't score 1.0
- `quality_report.improvement_actions` — `[(label, hint), …]` ready for a data-quality ticket
- `quality_report.as_text()` — human-readable rendering used by `run_demo.py`

### 2.9 Logging

After classification the agent emits a single INFO log per record:

```
{STATUS} [{telemetry_id}] asset={asset_id} bearing={bearing_id} score={dq_score:.3f} status={status}
```

What gets logged is controlled by `dfa_config.json → logging`.

### 2.10 Remediation (post-DFA, pre-Monitoring)

The DFA scores and classifies but does not modify the data. Remediation lives in [src/tools/remediation.py](../src/tools/remediation.py) and runs after DFA in `run_demo.py`. For FLAGGED records with missing fields:

- **IMPUTE** — fill imputable fields from baseline (e.g. `vib_rms_mm_s` from `baseline_vib_rms_mean`)
- **DROP** — discard the record entirely
- **KEEP** — flow downstream as-is, still flagged

Imputation provenance is recorded back on the `TrustedBearingSignal` (`imputed_fields`, `imputation_method`) so the Monitoring Agent can exclude imputed values from scoring.

---

## 3. Monitoring Agent (Phase 3)

**Source file:** [src/agents/monitoring_agent.py](../src/agents/monitoring_agent.py)
**Role:** Detect anomalies on validated telemetry using **two parallel detectors**: Hotelling T² (point-in-time multivariate) and EWMA (per-signal drift).

### 3.1 Purpose

The Monitoring Agent answers: *"Does this trustworthy reading indicate a bearing problem?"*

Two distinct things the DFA does **not** do:
- **Compare the reading to the bearing's own healthy baseline** — DFA only checks whether values are physically plausible.
- **Track how a signal evolves over time** — DFA is per-record; some faults only show up as drift.

It returns an `AnomalyEvent` if either detector fires, else `None`.

### 3.2 Class overview

```
MonitoringAgent
├── __init__(cfg: MonitoringConfig = None)
├── process(trusted: TrustedBearingSignal) -> Optional[AnomalyEvent]
├── process_batch(trusted_list: list) -> list[AnomalyEvent]   # filters out None
└── from_config() -> MonitoringAgent                          # factory
```

Stateless **in memory** — EWMA state is read from / written to `data/ewma_state.json` per call so it survives restarts and persists across runs.

### 3.3 Input — `TrustedBearingSignal`

The exact dataclass the DFA returns. The Monitoring Agent uses:

| Field | Used for |
|---|---|
| `raw` | Current signal values, identity, timestamp |
| `bearing_ctx.baseline_*_mean/std` | T² mean vector, EWMA reference |
| `asset_ctx.rated_rpm` | Regime classification (at-rated vs partial-load) |
| `imputed_fields` | Excluded from scoring (configurable) |
| `data_quality_score` | Confidence calculation |
| `validation_status` | Logged in evidence |

REJECTED records never arrive — caller filters with `trusted.is_processable`.

### 3.4 Output — `AnomalyEvent`

Defined in [src/schemas/anomaly.py](../src/schemas/anomaly.py).

| Field | Type | Notes |
|---|---|---|
| `case_id` | str | Stable: `ANOM-{bearing_id}-{timestamp}` |
| `asset_id`, `bearing_id`, `channel_id` | str | Identity carried from the signal |
| `timestamp_utc` | str | Reading timestamp |
| `anomaly_score` | float | `max(t2_score, ewma_score)`, 0–1 |
| `confidence_score` | float | Weighted combo of sensor quality, coverage, DQ score |
| `z_score` | float | Largest `|z|` across signals (primary deviation) |
| `triggered_features` | `list[str]` | Signals whose z crossed `trigger_z` (any method) |
| `regime` | str | `normal_load@rated`, `low_load`, `high_load`, etc. |
| `evidence` | dict | T² stats, EWMA stats, per-signal z, excluded fields, methods |
| `baseline_ref` | str | Bearing whose baseline was used |
| `processed_at` | ISO-8601 UTC | When Monitoring ran |

#### Evidence dict structure

```python
{
  "method":             "hotelling_t2",          # backward-compat
  "triggered_methods":  ["hotelling_t2", "ewma"], # which fired
  "t2_statistic":       64.86,
  "t2_degrees_freedom": 4,
  "t2_anomaly_score":   1.0,
  "t2_fired":           True,
  "signals": { "vib_rms_mm_s": {"value":4.1,"mean":2.1,"std":0.3,"z":6.66}, ... },
  "regime": { ... },
  "excluded_imputed": [],
  "data_quality_score": 0.999,
  "validation_status":  "VALID",
  "cov_inv_fallback":   False,
  "ewma": {
    "fired":              True,
    "score":              0.994,
    "alpha":              0.2,
    "control_limit_L":    3.0,
    "triggered_features": ["vib_rms_mm_s", "kurtosis", ...],
    "signals": { "vib_rms_mm_s": {"value":4.1,"ewma":2.676,"mean":2.1,"std":0.3,"z":5.76}, ... }
  }
}
```

### 3.5 Pipeline — `process()` step by step

| Step | What | Outcome |
|---|---|---|
| 1 | Suppress startup/shutdown records | return `None` |
| 2 | Classify operating regime; suppress if not running | return `None` |
| 3 | Collect usable signals (non-null, non-imputed) | build x, μ, σ vectors |
| 4 | Build covariance sub-matrix from per-bearing correlations and invert | singular → fall back to diagonal |
| 5 | Compute T² and normalise to 0–1 via chi-square CDF | `t2_anomaly_score` |
| 6 | Per-signal z-scores for evidence | `triggered_features` |
| 7 | Confidence (sensor fidelity + coverage + DQ score) | `confidence_score` |
| 7.5 | **EWMA control chart** per signal (parallel detector) | `ewma_score`, `ewma_fired` |
| 8 | Verdict — emit if T² fired OR EWMA fired, else `None` | `AnomalyEvent` |

### 3.6 Configuration — `monitoring_config.json`

| Block | Controls |
|---|---|
| `anomaly` | T² threshold (0.85), signal order, min signals, signal→baseline mapping, trigger_z, exclude imputed |
| `regime` | Running state names, min RPM, rated tolerance, load bands |
| `ewma` | enabled, alpha, control_limit_L, signals, state_file path, exclude_imputed |
| `confidence` | Weight mix (signal_quality / coverage / data_quality) |
| `logging` | log_anomalies, log_healthy |

Loaded via `load_monitoring_config()` in [src/tools/config_loader.py](../src/tools/config_loader.py). Validated on load (threshold range, EWMA alpha in (0,1], L positive).

### 3.7 Detection method 1 — Hotelling T² (point-in-time)

```
T² = (x − μ)ᵀ Σ⁻¹ (x − μ)
```

- `x` — current signal vector (4 signals by default: vib_rms, kurtosis, temp, bpfo_energy)
- `μ` — per-bearing baseline mean vector
- `Σ` — per-bearing covariance matrix (reconstructed from baseline stds and the correlation structure in `bearing_master`)

T² is normalised to a 0–1 anomaly score via the chi-square CDF with p degrees of freedom (p = number of usable signals). When some signals are null or imputed, T² is computed on a **sub-matrix** rather than the full 4×4 — chi-square df adjusts accordingly.

**Fires when** `t2_anomaly_score >= cfg.anomaly_threshold` (default 0.85).

**Why T² over individual z-scores?** A bearing fault causes vibration, kurtosis, temperature, and band energy to rise together in a correlated pattern. T² treats them as a vector and accounts for the baseline correlation — it flags joint deviation rather than scoring each signal independently. Catches:

- Correlated rises that are individually borderline but jointly anomalous
- Unusual deviation **patterns** (e.g. temp rising faster than vib) invisible to per-signal z-scores

Math lives in [src/tools/baseline_features.py](../src/tools/baseline_features.py) — `compute_hotelling_t2`, `t2_anomaly_score`, plus the covariance helpers.

### 3.8 Detection method 2 — EWMA control chart (drift)

EWMA = Exponentially Weighted Moving Average. Used in parallel to T² to catch **slow drift** that point-in-time scoring misses.

**Update formula** (per (bearing, signal) pair):

```
EWMA_t = α × x_t + (1 − α) × EWMA_{t-1}
```

**Control limit** (steady-state std of the EWMA series, then scaled by L):

```
σ_ewma = σ × √(α / (2 − α))
fires when |EWMA − μ| ≥ L × σ_ewma
```

With default `α = 0.2`, `L = 3.0`, the EWMA z-score is `(EWMA − μ) / σ_ewma`. Alarm if any tracked signal's `|z|` ≥ L.

**Cold-start**: when a bearing has no prior EWMA value, it's seeded at the baseline mean so the first reading doesn't spike artificially.

**State persistence**: see §3.9.

Math lives in [src/tools/baseline_features.py](../src/tools/baseline_features.py) — `update_ewma`, `ewma_control_sigma`, `ewma_z`, `ewma_anomaly_score`.

#### T² and EWMA roles

| | Detects | Memory | Math |
|---|---|---|---|
| **T²** | Sharp, correlated jumps across multiple signals at one instant | None | Multivariate Mahalanobis distance |
| **EWMA** | Sustained drift over time in any single signal | Yes — per (bearing, signal) | Univariate control chart |

The two detectors are **independent**. Turning EWMA on or off does not affect T². They both feed into one `AnomalyEvent`; `triggered_methods` lists which one(s) fired.

### 3.9 EWMA state persistence — `ewma_store`

[src/tools/ewma_store.py](../src/tools/ewma_store.py) provides JSON-sidecar persistence:

```
data/ewma_state.json
{
  "BRG_001": {
    "vib_rms_mm_s": 4.6365,
    "kurtosis":     4.7584,
    "temp_c":       68.13,
    "bpfo_energy":  3.006
  },
  "BRG_002": { ... }
}
```

Load + save happen per `process()` call (atomic, no partial-batch loss). Path is configurable via `cfg.ewma_state_file`. To reset, delete the file — the next run cold-starts from baseline means.

### 3.10 Suppression rules

Anomalies are **never emitted** in these cases:

1. `raw.startup_shutdown_flag == True` — startup transients aren't faults
2. `bearing_ctx is None` — can't score without baselines (should never happen post-DFA)
3. Regime is not running (idle / shutdown / unknown state)
4. Fewer than `cfg.min_signals_for_t2` usable signals available after null + imputed filtering

REJECTED records never reach the agent because the caller filters with `is_processable`.

### 3.11 Logging

Per record:

```
ANOMALY [TEL_xxxx] {asset}/{bearing} methods=['hotelling_t2','ewma'] T²=64.86 t2_score=1.000 ewma_score=0.994 conf=0.99 triggered=[vib_rms_mm_s, ...]
```

Healthy records are logged only if `cfg.log_healthy = true`.

---

## 3A. Failure Intelligence Agent (Phase 4)

**Source file:** [src/agents/failure_intelligence_agent.py](../src/agents/failure_intelligence_agent.py)
**Role:** Turn an `AnomalyEvent` into a *named fault* — what is wrong, at what ISO stage, and how severe.

### 3A.1 Purpose

The Monitoring Agent says *"this bearing is deviating"*. The Failure Intelligence Agent answers *"it's an outer-race fault at ISO stage 3, critical severity"* by matching the telemetry fingerprint against the fault taxonomy ([data/fault_taxonomy.json](../data/fault_taxonomy.json), Table 4).

It is **stateless** and **never raises** — an anomaly that matches no rule returns a `FaultDiagnosis` with `fault_mode="undetermined"` and a manual-inspection recommendation, so the pipeline contract is identical to the DFA's.

### 3A.2 Class overview

```
FailureIntelligenceAgent
├── __init__(taxonomy_rules: list, cfg: FailureIntelligenceConfig = None)
├── process(anomaly: AnomalyEvent, trusted: TrustedBearingSignal) -> FaultDiagnosis
├── process_batch(pairs: list[(anomaly, trusted)]) -> list[FaultDiagnosis]
└── from_data_files() -> FailureIntelligenceAgent     # factory
```

Taxonomy rules + config injected at construction. Instantiate once, call per anomaly.

### 3A.3 Inputs

| Source | Used for |
|---|---|
| `trusted.raw.bpfo_energy / bpfi_energy / bsf_energy / ftf_energy` | Band-energy thresholds (direct values — **no baseline multiplication**) |
| `trusted.raw.kurtosis` | Fault-type kurtosis gate |
| `trusted.raw.temp_c` + `bearing_ctx.baseline_temp_mean` | Derived `temp_rise` |
| `trusted.raw.vib_rms_mm_s` + `bearing_ctx.baseline_vib_rms_mean` | Derived `vib_ratio` + broadband detection |
| `asset_ctx.criticality / is_bottleneck` | Severity escalation |
| `anomaly.confidence_score / anomaly_score` | Confidence blend + evidence |

### 3A.4 Output — `FaultDiagnosis`

Defined in [src/schemas/diagnosis.py](../src/schemas/diagnosis.py). The 12 required contract fields (`fault_mode`, `fault_code`, `iso_stage`, `severity`, `confidence`, `bpfo_multiple`, `bpfi_multiple`, `kurtosis_at_detection`, `evidence`, `recommended_checks`, `narrative`, `processed_at`) plus identity (`case_id`, `asset_id`, `bearing_id`) and three additive Phase-5 hand-off fields (`is_bottleneck`, `typical_causes`, `rul_days_estimate`).

`evidence` carries the thresholds crossed, kurtosis gate, temp rise, broadband flag, match strength, and — per the roadmap — **`differential_diagnoses`**: runner-up fault modes with the reason each was *not* the primary (e.g. an inner-race fault notes that BPFO was present but sub-threshold).

### 3A.5 Pipeline — `process()` step by step

| Step | What |
|---|---|
| 1 | Gather observed band energies + kurtosis from `raw` |
| 2 | Derive `temp_rise`, `vib_ratio`, and `broadband_pattern` |
| 3 | `evaluate_candidates()` — score every taxonomy mode in priority order |
| 4 | Primary = first matched; **no match → `undetermined`** |
| 5 | Look up matched rule → `typical_causes`, `rul_days_stage_N`, degradation curve |
| 6 | Severity = stage base, escalated if high-criticality and/or bottleneck |
| 7 | Confidence = `w·anomaly_confidence + w·match_strength` |
| 8 | Build differentials, recommended checks, deterministic narrative |

### 3A.6 Fault matching — `fault_matcher.py`

[src/tools/fault_matcher.py](../src/tools/fault_matcher.py) holds the pure matching logic. Priority order (first matched wins):

| Order | Code | Fault | Fires when |
|---|---|---|---|
| 1 | FT_001 | outer_race_fault | `bpfo_energy` crosses a stage threshold **AND** `kurtosis > 5.0` |
| 2 | FT_002 | inner_race_fault | `bpfi_energy` crosses a stage threshold **AND** `kurtosis > 5.5` |
| 3 | FT_006 | cage_fault | `ftf_energy` dominant **AND** crosses a stage threshold **AND** `kurtosis > 4.5` |
| 4 | FT_003 | lubrication_issue | `broadband_pattern` **AND** `temp_rise > 0` **AND** `kurtosis < 5.0` |

**ISO stage** (`determine_iso_stage`) compares the energy value to `stage_3/2/1_vib_multiple` using **`>=`** — a value exactly on a threshold belongs to the **higher** stage (e.g. `bpfo == 3.5 == stage_3` → stage 3).

**Broadband detection** (`detect_broadband_pattern`): vibration elevated vs baseline (`vib_elevation_ratio`, default 1.3×) **AND** neither BPFO nor BPFI crosses its own stage-1 multiple — i.e. energy is spread with no dominant defect frequency. This is the lubrication fingerprint.

**Not in scope:** FT_004 imbalance (1X) and FT_005 misalignment (2X) — telemetry has no 1X/2X spectral fields yet, so these are deferred. An anomaly with no match returns `undetermined`.

### 3A.7 Configuration — `fi_config.json`

| Block | Controls |
|---|---|
| `severity` | Stage→base label, escalation order, escalate-if-criticality / escalate-if-bottleneck flags |
| `broadband` | `vib_elevation_ratio` for the lubrication fingerprint |
| `fault_checks` | Per-fault recommended inspection checklists (merged with taxonomy `typical_causes`) |
| `confidence` | Weight mix (`anomaly_confidence` / `match_strength`), must sum to 1.0 |
| `logging` | `log_diagnoses` toggle |

Loaded via `load_fi_config()` in [src/tools/config_loader.py](../src/tools/config_loader.py). Validated on load (stages 1–3 present, weights sum to 1.0).

### 3A.8 Severity

Base from ISO stage (`1→low`, `2→medium`, `3→high`), then escalated **one level** (up to `critical`) if the asset is high-criticality and/or a production bottleneck. So a stage-3 outer-race fault on the bottleneck gearbox (TEL_0020) escalates `high → critical`.

### 3A.9 Logging

```
DIAGNOSIS [TEL_0005] AST_MTR_001/BRG_001 fault=FT_001 stage=3 severity=critical conf=0.97 rul=7d
```

Gated by `fi_config.json → logging.log_diagnoses`.

---

## 4. Asset-level reporting

[test_monitoring_agent.py](../test_monitoring_agent.py) demonstrates how to **roll up per-record `AnomalyEvent`s into per-asset verdicts** — the format you want for dashboards, alerting, and operator triage.

Pattern (worst-record-wins):

```python
by_asset = {}
for trusted, event in pipeline:
    a = trusted.raw.asset_id
    b = by_asset.setdefault(a, {"anomalies": [], "worst": None})
    if event is not None:
        b["anomalies"].append(event)
        if b["worst"] is None or event.anomaly_score > b["worst"].anomaly_score:
            b["worst"] = event
```

Each asset surfaces:

- Anomaly count, score (worst case), confidence
- Method(s) that fired (T² / EWMA / both)
- Triggered signals (the fault fingerprint — used downstream for fault classification)
- Source scenarios / records

This is the natural place to extend with **time windows** (rolling 1h / 24h / 7d), **per-source breakdowns** (which historian / Kafka topic supplied the worst record), and **persistent storage** (to chart trends).

---

## 5. Module index

### Agents

| File | Role |
|---|---|
| [src/agents/data_foundation_agent.py](../src/agents/data_foundation_agent.py) | DFA — validates, enriches, scores |
| [src/agents/monitoring_agent.py](../src/agents/monitoring_agent.py) | Monitoring — T² + EWMA anomaly detection |
| [src/agents/failure_intelligence_agent.py](../src/agents/failure_intelligence_agent.py) | Failure Intelligence — classifies anomaly into fault + ISO stage + severity |

### Schemas (data contracts)

| File | Contains |
|---|---|
| [src/schemas/bearing_signal.py](../src/schemas/bearing_signal.py) | `BearingSignalFact`, `TrustedBearingSignal`, `AssetContext`, `BearingContext`, `ValidationDetail`, `QualityReport`, `ValidationStatus` |
| [src/schemas/anomaly.py](../src/schemas/anomaly.py) | `AnomalyEvent` |
| [src/schemas/diagnosis.py](../src/schemas/diagnosis.py) | `FaultDiagnosis` |
| [src/schemas/asset.py](../src/schemas/asset.py) | `AssetMaster`, `BearingMaster` |

### Tools

| File | Role |
|---|---|
| [src/tools/validators.py](../src/tools/validators.py) | Pure DFA check functions, one per dimension |
| [src/tools/enrichment.py](../src/tools/enrichment.py) | Master → context join helpers |
| [src/tools/baseline_features.py](../src/tools/baseline_features.py) | T² math, EWMA math, regime classifier, confidence |
| [src/tools/fault_matcher.py](../src/tools/fault_matcher.py) | Pure fault-taxonomy matching: stage, broadband, candidates |
| [src/tools/ewma_store.py](../src/tools/ewma_store.py) | JSON-sidecar EWMA state persistence |
| [src/tools/data_loader.py](../src/tools/data_loader.py) | JSON loaders for masters and scenarios |
| [src/tools/config_loader.py](../src/tools/config_loader.py) | `DFAConfig`, `MonitoringConfig` + loaders |
| [src/tools/remediation.py](../src/tools/remediation.py) | Impute / drop / keep policy for FLAGGED records |
| [src/tools/curated_store.py](../src/tools/curated_store.py) | Writes the curated (post-validation) table |

### Configuration

| File | Owns |
|---|---|
| [config/dfa_config.json](../config/dfa_config.json) | DFA thresholds, weights, hints, remediation policy |
| [config/monitoring_config.json](../config/monitoring_config.json) | T² + EWMA thresholds, regime rules, confidence weights |
| [config/fi_config.json](../config/fi_config.json) | Severity matrix, broadband detection, fault checklists, confidence weights |

### Reference data

| File | Role |
|---|---|
| [data/asset_master.json](../data/asset_master.json) | Table 3 — asset metadata |
| [data/bearing_master.json](../data/bearing_master.json) | Table 2 — bearing metadata, sensor bounds, baselines |
| [data/fault_taxonomy.json](../data/fault_taxonomy.json) | Fault classification rules (used by Phase 4) |
| [data/telemetry_scenarios.json](../data/telemetry_scenarios.json) | Test telemetry — replaced by live source in production |

### Auto-generated files

| File | Created by | Purpose |
|---|---|---|
| `data/ewma_state.json` | `MonitoringAgent` | Persisted per-bearing EWMA values |
| `data/curated_signals.json` | `run_demo.py` | Routed records for downstream |
| `logs/dfa_quality_audit.log` | `run_demo.py` | Human-readable DFA audit trail |

### Tests / demos

| File | Purpose |
|---|---|
| [run_demo.py](../run_demo.py) | End-to-end DFA run with interactive remediation |
| [test_monitoring_agent.py](../test_monitoring_agent.py) | Asset-level anomaly rollup (the production-report format) |
| [tests/test_data_foundation.py](../tests/test_data_foundation.py) | DFA unit tests across all demo scenarios |

---

## 6. How to extend

### Add a new DFA validator (e.g. a new sensor field)

1. Add the field to `BearingSignalFact` in [src/schemas/bearing_signal.py](../src/schemas/bearing_signal.py).
2. Add a check function to [src/tools/validators.py](../src/tools/validators.py) following the pattern: take the value, return `(ok, reason)`.
3. Wire it into the relevant step in `DataFoundationAgent.process()`.
4. If the field affects the composite score, add it to a dimension (usually `ranges_valid` or `completeness`).

### Add a new DFA quality dimension

1. Add a weight key under `quality_score.weights` in [config/dfa_config.json](../config/dfa_config.json). All weights must still sum to 1.0.
2. Add a scoring function in [src/tools/validators.py](../src/tools/validators.py).
3. Add it to `compute_data_quality_score()`'s signature and weighted sum.
4. Add label/description/improvement hint to the same config block.

### Add a new anomaly detection method

1. Add a new config block to [config/monitoring_config.json](../config/monitoring_config.json) with its own threshold + parameters.
2. Add fields to `MonitoringConfig` in [src/tools/config_loader.py](../src/tools/config_loader.py).
3. Add the math to [src/tools/baseline_features.py](../src/tools/baseline_features.py) — keep it pure (no I/O).
4. Add a step in `MonitoringAgent.process()` between the existing detectors and the verdict step. Compute the verdict as `t2_fired or ewma_fired or new_method_fired`.
5. Add the new method to `triggered_methods` and to `evidence` so downstream agents can introspect it.
6. Surface its anomaly score in the combined `anomaly_score = max(...)` calculation.

### Track an additional signal under EWMA

Edit [config/monitoring_config.json](../config/monitoring_config.json):

```json
"ewma": {
  "signals": ["vib_rms_mm_s", "kurtosis", "temp_c", "bpfo_energy", "bpfi_energy"]
}
```

Also ensure the signal is mapped in `signal_baselines` (mean + std attribute names on `BearingContext`). No code change required.

### Replace `telemetry_scenarios.json` with a live source

The agents accept dicts — they don't care where the dict comes from. Build an ingestion layer that translates from your source (PI, OPC-UA, Kafka, SQL) into `BearingSignalFact`-shaped dicts and calls `agent.process()` per record. See discussion in any conversation around "Layer 1 connectors". The master tables can also move to live sources by replacing the loader functions in [src/tools/data_loader.py](../src/tools/data_loader.py).

---

## 7. Running locally

### One-shot DFA demo (interactive)

```powershell
python run_demo.py            # all assets, human-readable
python run_demo.py --json     # machine-readable
python run_demo.py --remediate # impute / drop / keep prompts for FLAGGED records
```

Output written to `logs/dfa_quality_audit.log` (audit trail) and `data/curated_signals.json` (routed records).

### Asset-level Monitoring report

```powershell
python test_monitoring_agent.py
```

Wipes `data/ewma_state.json`, runs every scenario through DFA → Monitoring, and prints the per-asset rollup with evidence.

### Unit tests

```powershell
python -m pytest tests/ -v
```

### Per-dimension data-quality check (ad-hoc script)

```powershell
python test_null_comparison.py    # how completeness scales with null count
python test_asset_comparison.py   # asset-level DQ rollup with per-dimension breakdown
```

---

## 8. Glossary

| Term | Meaning |
|---|---|
| **DAMA dimensions** | The six axes used to score data quality: integrity (asset, bearing), completeness, validity, accuracy, consistency. |
| **DFA** | Data Foundation Agent — Phase 2. |
| **EWMA** | Exponentially Weighted Moving Average — a smoothing filter used here as a control-chart drift detector. |
| **Hotelling T²** | Multivariate generalisation of the z-score. Distance of a point from a baseline mean, scaled by the inverse covariance. |
| **Imputation** | Filling missing values with a statistical proxy (here, the baseline mean for that bearing/signal). |
| **Regime** | Operating-state label: at-rated, low-load, high-load, idle, etc. Anomaly detection is suppressed off-regime. |
| **Substantive vs advisory reason** | Substantive reasons can downgrade a record from VALID to FLAGGED; advisory ones (startup, stale timestamp on a historical feed) cannot. |
| **Trigger_z** | The per-signal z-score threshold above which a signal lands in `triggered_features` for evidence. Distinct from the T² firing threshold and the EWMA control limit. |
