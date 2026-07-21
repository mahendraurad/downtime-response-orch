# DRO Agent Pipeline — Monitoring → Failure Intelligence → Predictive Risk

End-to-end reference for the three core diagnostic agents: what each one
consumes, what it emits, the step-by-step process inside `process()`, and the
exact tool functions it calls.

The three agents form a linear hand-off. Each is **stateless** (config + rules
injected at construction) and reads the typed output of the agent before it:

```
TrustedBearingSignal ─▶ Monitoring ─▶ AnomalyEvent ─▶ Failure Intelligence ─▶ FaultDiagnosis ─▶ Predictive Risk ─▶ RiskAssessment
   (Data Foundation)        Agent      (or None)            Agent                                    Agent
```

A signal that looks healthy stops at the Monitoring Agent (`process` returns
`None`) and never reaches the downstream two. Every agent stamps a UTC
ISO-8601 `processed_at` and is designed never to raise on bad/missing data.

---

## Shared vocabulary

| Term | Meaning |
|---|---|
| **TrustedBearingSignal** | Data Foundation Agent output: the raw reading (`raw`), joined `asset_ctx` (criticality, bottleneck, downtime cost) and `bearing_ctx` (baselines), a data-quality score, and validation status. |
| **ISO stage** | Severity stage of a bearing defect: **1** (incipient) → **2** (developing) → **3** (severe, near failure). `0` = no stage crossed / undetermined. |
| **Baseline** | Per-bearing healthy mean + std (and pairwise correlations) stored in `bearing_ctx`, learned during healthy operation. |
| **Band energy** | `bpfo_energy` (outer race), `bpfi_energy` (inner race), `bsf_energy` (rolling element), `ftf_energy` (cage). In this dataset these are **direct threshold values** — compared straight to the taxonomy multiples, no baseline scaling. |

---

# Agent 2 — Monitoring Agent

**File:** [src/agents/monitoring_agent.py](../src/agents/monitoring_agent.py)
**Question it answers:** *"Is anything abnormal right now?"*

### Input → Output

| | Type | Source |
|---|---|---|
| **Input** | `TrustedBearingSignal` | Data Foundation Agent |
| **Output** | `Optional[AnomalyEvent]` | `None` when healthy/suppressed |

### Algorithm in one line
Primary detector is **Hotelling T²** — it treats `[vib_rms, kurtosis, temp, bpfo]`
as one correlated vector and scores the joint deviation against the baseline
covariance, normalised to 0–1 via the chi-square CDF. A second **EWMA control
chart** runs in parallel to catch slow drift. **Either** detector firing
produces an `AnomalyEvent`.

### Process (`process(trusted)`), step by step

| Step | What happens | Functions used |
|---|---|---|
| 1 | **Startup/shutdown suppression** — drop transient records (`raw.startup_shutdown_flag`). Drop if no `bearing_ctx`. | — |
| 2 | **Regime classification** — only score when genuinely *running*; suppress idle/startup. | `classify_regime()` |
| 3 | **Collect usable signals** — walk `cfg.signal_order`, skip nulls and (optionally) imputed fields; gather current values, baseline means, stds. Require ≥ `min_signals_for_t2`. | — |
| 4 | **Build & invert covariance sub-matrix** — assemble the correlation matrix, scale to covariance, invert; fall back to a diagonal (independent z-scores) if singular. | `build_correlation_matrix()`, `build_covariance_matrix()`, `invert_covariance()`, `diagonal_inverse()` |
| 5 | **T² statistic → anomaly score** — compute T², normalise to 0–1 with the chi-square CDF (p = number of usable signals). | `compute_hotelling_t2()`, `t2_anomaly_score()` (→ `chi2_cdf()`) |
| 6 | **Z-scores for evidence** — per-signal signed deviations; flag `triggered_features` where `|z| ≥ trigger_z`. | `compute_z_score()` |
| 7 | **Confidence** — blend sensor fidelity, signal coverage, and DFA data-quality. | `compute_confidence()` |
| 7.5 | **EWMA control chart** — per signal, update the smoothed value from persisted state, compute EWMA-sigma z, fire if `|z| ≥ control_limit_L`. | `update_ewma()`, `ewma_z()` (→ `ewma_control_sigma()`), `ewma_anomaly_score()`; state via `load_state()` / `save_state()` |
| 8 | **Verdict** — fire if `t2_fired` **or** `ewma_fired`. Combined score = `max(t2, ewma)`; merge triggered features. Build the business-readable `reason`. Else return `None`. | `build_anomaly_reason()` |

### Key output fields (`AnomalyEvent`)
`case_id`, `asset_id`/`bearing_id`, `anomaly_score` (combined 0–1),
`confidence_score`, `z_score` (primary), `triggered_features`,
`reason` (plain-language "why it was flagged"), `regime`,
`evidence` (T² statistic, dof, per-signal z, EWMA detail, excluded fields), `processed_at`.

> **`reason` is deterministic — no LLM, and written for business / ops users.**
> `build_anomaly_reason()` assembles a plain-language explanation from the same
> evidence the score uses (which readings deviated and how far, which detector
> fired, drift, regime, confidence) but with **no statistics jargon** (σ, T²,
> EWMA), acronyms, or units, e.g. *"This bearing was flagged because the overall
> vibration is far higher than normal, a vibration pattern that points to
> outer-ring bearing wear is well higher than normal … Together these readings
> form a pattern that healthy equipment does not show. … We are highly confident
> this is a real problem and not a sensor glitch."* Raw field names map to plain
> English via `SIGNAL_LABELS`; z-magnitudes become words ("far/well/slightly
> higher than normal") and the regime label becomes "running normally (at normal
> load and its rated speed)".

### Tool module
[src/tools/baseline_features.py](../src/tools/baseline_features.py) (pure feature math),
[src/tools/ewma_store.py](../src/tools/ewma_store.py) (EWMA state persistence),
config via `load_monitoring_config()`.

> **Note:** T² is the primary score; z-scores are retained for explanation only.
> Reduced T² (a sub-matrix) is used when some signals are null/imputed, with the
> chi-square degrees of freedom `p` adjusted accordingly.

---

# Agent 3 — Failure Intelligence Agent

**File:** [src/agents/failure_intelligence_agent.py](../src/agents/failure_intelligence_agent.py)
**Question it answers:** *"What exactly is wrong, and how severe?"*

### Input → Output

| | Type | Source |
|---|---|---|
| **Input** | `AnomalyEvent` + `TrustedBearingSignal` + taxonomy rules | Monitoring Agent + `data/fault_taxonomy.json` |
| **Output** | `FaultDiagnosis` (never `None` — emits `undetermined` if nothing matches) | |

### Classification logic — **data-driven (no hardcoded per-fault code)**
Compares band energies + kurtosis against per-fault stage thresholds in the
taxonomy. **Direct comparison** — `bpfo_energy`/`bpfi_energy`/`ftf_energy` are
matched straight to `stage_N_vib_multiple` (no baseline multiplication). Each
fault has a **kurtosis gate** that must also be crossed:

| Fault | Code | Dominant band | Kurtosis gate | Priority |
|---|---|---|---|---|
| outer_race_fault | FT_001 | BPFO | > 5.0 | 1 |
| inner_race_fault | FT_002 | BPFI | > 5.5 | 2 |
| cage_fault | FT_006 | FTF (must dominate) | > 4.5 | 3 |
| lubrication_issue | FT_003 | broadband (no single band) | *below* ceiling 5.0 | 4 |

First matched candidate (lowest `priority`) is the primary diagnosis; the rest
with partial evidence become **differential diagnoses**.

**The matcher reads a structured `detection` block per rule — it does not parse
prose or hardcode fault codes.** Each matchable taxonomy entry carries:

```json
"detection": {
  "priority": 1,
  "signal": "bpfo_energy",
  "kurtosis": { "mode": "above", "threshold_field": "kurtosis_threshold" },
  "require_dominant": false,
  "dominant_among": [],
  "require_broadband": false,
  "require_temp_rise": false
}
```

A single generic evaluator interprets this for every rule, so **adding, removing,
or reordering a fault mode is a pure taxonomy edit — no code change**. The
human-readable `severity_logic` string stays in the taxonomy as documentation
but is no longer parsed or trusted; the `detection` block is the single source of
truth. Thresholds are **extracted, never copied** — a `detection` value may be a
literal or a *reference* that pulls the value from another rule, identified by
its detection `signal` (preferred — survives a `fault_code` rename) or by
`fault_code`:

```json
{ "signal": "bpfo_energy", "field": "kurtosis_threshold" }
```

The lubrication kurtosis ceiling references the BPFO-detecting rule's
`kurtosis_threshold` this way, so changing that one value updates the lubrication
ceiling automatically with no duplicated literal to keep in sync. A rule
**without** a `detection` block is dormant — used for fault
modes whose signal is not in the telemetry schema yet (e.g. FT_004/FT_005
imbalance/misalignment, which need 1X/2X band energy).

### Process (`process(anomaly, trusted)`), step by step

| Step | What happens | Functions used |
|---|---|---|
| 1 | **Gather observed signals** — band energies + kurtosis from `trusted.raw`; baselines from `bearing_ctx`. | — |
| 2 | **Derive inputs** — temperature rise above baseline; vibration ratio; broadband pattern (vib elevated AND no single defect band dominant). The band-dominance thresholds are looked up from the taxonomy *by signal* (not hardcoded to FT_001/FT_002). | `compute_temp_rise()`, `band_stage1_threshold()`, `detect_broadband_pattern()` |
| 3 | **Evaluate every matchable candidate** — read each rule's `detection` block; ISO stage per signal (`>=` boundary → higher stage) + kurtosis gate + dominance/broadband/temp conditions; build a candidate per fault mode with match flag, stage, and a human-readable reason. Sorted by `detection.priority`. | `evaluate_candidates()` (→ `get_detection()`, `determine_iso_stage()`) |
| 4 | **No match → undetermined** — emit a manual-inspection diagnosis (`fault_mode="undetermined"`, `iso_stage=0`); never raises. | `_undetermined()` |
| 5 | **Look up matched rule** — pull RUL days for the assigned stage, typical causes, criticality/bottleneck from `asset_ctx`. | — |
| 6 | **Severity** — base from ISO stage, escalated one level if high-criticality and/or bottleneck. | `_severity()` |
| 7 | **Confidence** — config blend of the anomaly's confidence and how decisively the signature was crossed (`match_strength`). | `_match_strength()` |
| 8 | **Differentials, checks, narrative** — runner-up faults with reasons; inspection checklist; deterministic prose explanation. | `_differentials()`, `_recommended_checks()`, `_narrative()` |

### Key output fields (`FaultDiagnosis`)
`fault_mode`, `fault_code`, `iso_stage`, `severity`, `confidence`,
`bpfo_multiple`/`bpfi_multiple`, `kurtosis_at_detection`, `evidence` (thresholds
crossed, differentials), `recommended_checks`, `narrative`, plus the **Phase-5
hand-off fields**: `is_bottleneck`, `typical_causes`, `rul_days_estimate`
(taxonomy RUL for the assigned stage).

### Tool module
[src/tools/fault_matcher.py](../src/tools/fault_matcher.py) (pure threshold matching),
config via `load_fi_config()`, taxonomy via `load_fault_taxonomy()`.

---

# Agent 4 — Predictive Risk Agent

**File:** [src/agents/predictive_risk_agent.py](../src/agents/predictive_risk_agent.py)
**Question it answers:** *"How long do we have, and what does it cost?"*

### Input → Output

| | Type | Source |
|---|---|---|
| **Input** | `FaultDiagnosis` + `AnomalyEvent` + `TrustedBearingSignal` + taxonomy rules | Failure Intelligence + Monitoring + Data Foundation |
| **Output** | `RiskAssessment` (never raises) | |

### RUL band design — **Design A (taxonomy-driven)**
The RUL window is read straight from the per-fault `rul_days_stage_N` values in
the taxonomy, **not** from fixed ISO bands. A bearing at a given stage has
between the next (worse) stage's RUL and this stage's RUL remaining:

| Stage | (rul_min, rul_max) | FT_001 example | Label |
|---|---|---|---|
| 3 | `(0, rul_days_stage_3)` | `(0, 7)` | `"0–7 days"` |
| 2 | `(rul_days_stage_3, rul_days_stage_2)` | `(7, 30)` | `"7–30 days"` |
| 1 | `(rul_days_stage_2, rul_days_stage_1)` | `(30, 90)` | `"30–90 days"` |
| 0 / undetermined | configured monitor band | `(45, 999)` | `"monitor"` |

Labels are generated from the real numbers, so a cage fault (FT_006) at stage 3
correctly reads `"0–4 days"`, not a hardcoded `"0–7"`. This is exact for the
CASE_001 calibration anchor (FT_001 stage 3 = 7 days).

### Process (`process(diagnosis, anomaly, trusted)`), step by step

| Step | What happens | Functions used |
|---|---|---|
| 1 | **RUL band** — taxonomy-driven `(rul_min, rul_max, label)`; iso_stage 0 / unknown code → monitor band. | `get_rul_band()` |
| 2 | **Failure probability + health index** — config blend of `stage_base[iso_stage]` and `anomaly_score`; `health_index = 1 − failure_probability`. | — |
| 3 | **Business impact + asset context** — read `is_bottleneck` / `criticality` / `downtime_cost_per_hour` from `asset_ctx` (None-safe); raise `business_impact_flag` for bottleneck and/or high-criticality. | — |
| 4 | **Financial exposure** — `rul_max × 24 × downtime_cost_per_hour`; forced to `0` for the monitor band (no quantified timeline). | `compute_financial_exposure()` |
| 5 | **Risk level** — reuses the already-escalated `diagnosis.severity` so risk_level never diverges from the upstream agent. | — |
| 6 | **LLM advisory fallback** *(optional, config-gated, off by default)* — when the rules are insufficient, consult an LLM for an advisory `recommended_action` + `rationale`. | `LLMClient.complete_json()` |

> **Why `rul_max` for exposure (not `rul_min`)?** `rul_min` is `0` at stage 3, which
> would zero out the exposure. `rul_max` is the worst-case time-to-failure horizon
> (7 days for an outer-race stage-3 fault), which matches the calibration figures.

### LLM fallback — *when rules are not sufficient*
Disabled by default (`risk_config.json → llm_fallback.enabled: false`). When
enabled **and** Azure OpenAI credentials are present, the agent calls the LLM for
the cases the rule engine can't assess confidently:
- **Undetermined / monitor band** — an anomaly fired but no taxonomy signature matched (`iso_stage 0` / no `fault_code`).
- **Low confidence** — `diagnosis.confidence` below `trigger_low_confidence_below`.

**The deterministic RUL/exposure numbers remain the auditable source of truth.**
The LLM only *augments*: it always adds `advisory_note` (recommended action +
rationale) and may set `risk_level` **only** for the undetermined case where the
rules had nothing. If the LLM is unavailable, not installed, or errors, the
deterministic assessment is returned unchanged (`assessment_source = "rules"`) —
the call **never raises**. See [src/tools/llm_client.py](../src/tools/llm_client.py)
(lazy `openai` import, graceful degradation).

### Key output fields (`RiskAssessment`)
`risk_level`, `rul_min_days`, `rul_max_days`, `rul_band_label`,
`failure_probability`, `health_index` (0=failed → 1=healthy),
`business_impact_flag`, `financial_exposure` (INR), `confidence`
(carried from the diagnosis), `assessment_source` (`"rules"` |
`"rules+llm_fallback"`), `advisory_note` (LLM advisory when rules were
insufficient), `case_id`/`asset_id`/`bearing_id`, `processed_at`.

### Tool module
[src/tools/rul_calculator.py](../src/tools/rul_calculator.py) (`get_rul_band`,
`compute_financial_exposure`),
[src/tools/llm_client.py](../src/tools/llm_client.py) (optional Azure OpenAI
fallback), config via `load_risk_config()`,
schema [src/schemas/risk.py](../src/schemas/risk.py).

---

# Worked example — Gearbox AST_GBX_001, reading TEL_0020

Raw signal: **Vib = 7.2 mm/s, Kurtosis = 7.0, Temp = 86 °C, BPFO = 3.5**.
Baseline for BRG_011 ≈ 2.8 mm/s ± 0.4.

### 1 — Monitoring Agent
Vib is ~11σ above baseline; T² over the correlated vector is large → normalised
**anomaly_score ≈ 0.89**. Running regime confirmed, confidence high.
→ Emits **`AnomalyEvent`**.
*"Something is seriously wrong with this bearing."*

### 2 — Failure Intelligence Agent
BPFO `3.5` ≥ FT_001 stage-3 threshold (`3.5`) ✓ **and** kurtosis `7.0` > `5.0` ✓.
Both gates crossed → **`fault_mode = outer_race_fault`, `iso_stage = 3`**.
High-criticality bottleneck escalates severity to **critical**.
→ Emits **`FaultDiagnosis`** (with `rul_days_estimate = 7`).
*"It's a stage-3 outer-race crack."*

### 3 — Predictive Risk Agent
Stage 3 outer race → RUL band **`(0, 7, "0–7 days")`**. Asset is a bottleneck,
downtime cost ₹18,000/hr.
**financial_exposure = 7 × 24 × 18,000 = ₹30,24,000.**
risk_level = **critical** (reused from diagnosis), business_impact_flag = **True**.
→ Emits **`RiskAssessment`**.
*"You have less than a week, and it will cost ₹30 lakh if it fails."*

---

# Function index (by module)

**`src/tools/baseline_features.py`** (Monitoring)
`compute_z_score` · `update_ewma` · `ewma_control_sigma` · `ewma_z` ·
`ewma_anomaly_score` · `build_anomaly_reason` · `build_correlation_matrix` ·
`build_covariance_matrix` · `compute_hotelling_t2` · `invert_covariance` ·
`diagonal_inverse` · `chi2_cdf` · `t2_anomaly_score` · `classify_regime` ·
`compute_confidence` · `compute_rolling_trend`

**`src/tools/fault_matcher.py`** (Failure Intelligence)
*Extractors (read values out of a rule):* `get_detection` · `is_matchable` ·
`kurtosis_threshold` · `stage_multiples` · `find_rule_for_signal` ·
`band_stage1_threshold` · `resolve_threshold` (literal-or-reference resolver)
*Matching:* `determine_iso_stage` · `compute_temp_rise` ·
`detect_broadband_pattern` · `evaluate_candidates` · `match_fault_taxonomy`

**`src/tools/rul_calculator.py`** (Predictive Risk)
`get_rul_band` · `compute_financial_exposure`

**`src/tools/llm_client.py`** (Predictive Risk — optional fallback)
`LLMClient.is_configured` · `LLMClient.complete_json`

**`src/tools/config_loader.py`** (all)
`load_monitoring_config` → `MonitoringConfig` · `load_fi_config` →
`FailureIntelligenceConfig` · `load_risk_config` → `RiskConfig`
