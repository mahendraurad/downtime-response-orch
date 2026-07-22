<!-- ************** Added by Prateek Mittal on 20th July 2026 ****************** -->
<!-- Complete deterministic backend flow through Predictive Risk. -->

# Agent 1-2-3-4 Integration

## Certified flow

```text
Telemetry dictionary / JSON
    |
    v
Agent 1: TrustedBearingSignal
    | downstream_eligible=true
    v
Agent 2: MonitoringResult + optional AnomalyEvent
    | anomaly_event exists
    v
Agent 3: FaultDiagnosis
    | diagnostic_eligible=true
    v
Agent 4: RiskAssessment
    | risk_eligible=true
    v
Knowledge / later orchestration
```

Every stop is explicit. Healthy, suppressed, cooldown, flagged, rejected,
duplicate, and out-of-order outcomes do not create diagnosis or risk objects.

## Input/output boundary

| Agent | Expected input | Output |
|---|---|---|
| Agent 1 | One telemetry dictionary/JSON object | `TrustedBearingSignal` |
| Agent 2 | Eligible `TrustedBearingSignal` | `MonitoringResult`, optionally `AnomalyEvent` |
| Agent 3 | `AnomalyEvent` plus its originating trusted signal | `FaultDiagnosis` |
| Agent 4 | Diagnosis, anomaly, and originating trusted signal | `RiskAssessment` |

Malformed caller input is contained by Agent 1. Identity or provenance
tampering at later boundaries becomes an explicit invalid audit result.

## Smooth terminal paths

| Data/condition | Final outcome |
|---|---|
| Healthy | Agent 2 `healthy`; no diagnosis/risk |
| Startup or idle | Agent 2 `suppressed`; no diagnosis/risk |
| Cooldown/pending alert | No repeated anomaly, diagnosis, or risk |
| Flagged/stale/low-quality | Agent 1 `data_review`; chain stops |
| Rejected/malformed/unknown asset | Agent 1 `stop`; chain stops |
| Duplicate | Stored once; no repeated monitoring/risk |
| Out of order | Audited and reviewed; no monitoring/risk |
| Known fault | Agent 3 `diagnosed`; Agent 4 `assessed` |
| Unknown fault signature | Agent 3 `undetermined`; Agent 4 `monitor` |
| Invalid Agent 3 handoff | Agent 4 `invalid_input`; Knowledge route stops |

## Supported diagnosed outputs

| Signal pattern | Agent 3 | Agent 4 example |
|---|---|---|
| BPFO | `FT_001 outer_race_fault` | Stage-3 RUL `0–7 days` |
| BPFI | `FT_002 inner_race_fault` | Stage-3 RUL `0–5 days` |
| Broadband + temperature | `FT_003 lubrication_issue` | Stage-2 RUL `5–20 days` |
| Dominant BSF | `FT_007 rolling_element_fault` | Stage-3 RUL `0–10 days` |

## Complete-chain edge catalogue

The authoritative suite is
`tests/test_agent1_agent2_agent3_agent4_integration.py` with 33 cases.

| # | Case |
|---:|---|
| 1-4 | Four supported fault families reach correct diagnosis, risk level, and RUL |
| 5 | Undetermined anomaly becomes monitor risk |
| 6-9 | Healthy, startup, dropout, and unknown-asset terminal paths |
| 10-14 | `None`, empty object, string, number, and list malformed inputs |
| 15 | Global aliases/unit conversions retain risk semantics |
| 16 | OPC-UA source profile retains risk semantics |
| 17 | Canonical value wins over conflicting alias |
| 18 | Unknown connector field is audited without changing risk |
| 19 | Stale live telemetry stops at Agent 1 |
| 20 | Timezone-free timestamp stops at Agent 1 |
| 21 | Low signal quality stops at Agent 1 |
| 22 | Cooldown prevents repeated risk cards |
| 23 | Complete Agent 1-4 provenance linkage |
| 24 | Tampered taxonomy provenance is rejected by Agent 4 |
| 25 | Tampered event identity is rejected by Agents 3 and 4 |
| 26 | Agent 4 does not mutate upstream outputs |
| 27 | Complete output chain is JSON serializable |
| 28 | Invalid diagnosis/risk stops orchestrator routes |
| 29 | Valid diagnosis/risk follows orchestrator routes |
| 30 | Mixed batch keeps outputs isolated |
| 31 | Durable duplicate never creates a second risk |
| 32 | Durable late event never creates risk |
| 33 | Equal timestamp with unique IDs can create independent risk cards |

## Combined integration coverage

| Suite | Tests |
|---|---:|
| Agent 1 -> Agent 2 | 8 |
| Agent 2 -> Agent 3 | 6 |
| Agent 1 -> Agent 2 -> Agent 3 | 32 |
| Agent 3 -> Agent 4 | 10 |
| Agent 1 -> Agent 2 -> Agent 3 -> Agent 4 | 33 |
| **Total** | **89** |

Results:

```text
All Agent 1-4 integration suites: 89 passed
Full repository: 384 passed, 2 skipped, 0 failed
```

Run the complete integration certification:

```powershell
python -m pytest tests/test_agent1_agent2_integration.py tests/test_agent2_agent3_integration.py tests/test_agent1_agent2_agent3_integration.py tests/test_agent3_agent4_integration.py tests/test_agent1_agent2_agent3_agent4_integration.py -q
```

## Release rule

Any new connector profile, telemetry field, monitoring state, fault taxonomy
mode, risk input, or routing outcome must add an executable complete-chain case
before release. Testing materially reduces integration risk; it does not justify
claiming that any non-trivial system can be mathematically bug-free.

<!-- *********************** -->
