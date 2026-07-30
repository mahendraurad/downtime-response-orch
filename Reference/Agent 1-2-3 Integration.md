<!-- ************** Added by Prateek Mittal on 20th July 2026 ****************** -->
<!-- Contract and test record for the Data Foundation, Monitoring, and Failure Intelligence chain. -->

# Agent 1-2-3 Integration

## Purpose

This document certifies the backend handoff through:

```text
Raw telemetry
  -> Agent 1: Data Foundation
  -> Agent 2: Monitoring
  -> Agent 3: Failure Intelligence
```

The integration rule is that downstream agents run only when the preceding
agent emits an explicitly eligible output. A missing output is not interpreted
as a fault or silently converted into another outcome.

## Input and output contracts

| Boundary | Input | Output |
|---|---|---|
| Caller -> Agent 1 | Python dictionary / JSON telemetry object | `TrustedBearingSignal` for every input, including malformed input |
| Agent 1 -> Agent 2 | `TrustedBearingSignal` with `downstream_eligible=true` | `MonitoringResult`; anomaly path also contains `AnomalyEvent` |
| Agent 2 -> Agent 3 | `AnomalyEvent` plus the exact originating `TrustedBearingSignal` | `FaultDiagnosis` |

Agent 1 rejects non-object input safely. Agent 2 is not called by the
orchestrator when Agent 1 blocks the record, although its direct `assess()` API
also returns an explicit `ineligible` result. Agent 3 runs only when Agent 2
emits an `AnomalyEvent`.

## Authoritative outcome matrix

| Agent 1 | Agent 2 | Agent 3 | Terminal meaning |
|---|---|---|---|
| `VALID`, route `monitoring` | `healthy` | Not called | Valid reading, no abnormal condition |
| `VALID`, route `monitoring` | `suppressed` | Not called | Startup, idle, or other configured suppression |
| `VALID`, route `monitoring` | `pending_alert` or `cooldown` | Not called | Detector evidence exists but alert policy prevents duplicate/noisy emission |
| `FLAGGED`, route `data_review` | Direct call returns `ineligible` | Not called | Data requires review/remediation |
| `REJECTED`, route `stop` | Direct call returns `ineligible` | Not called | Unusable identity/schema input |
| Duplicate, route `stop` | `duplicate` | Not called | Idempotency stop |
| Out of order, route `data_review` | `out_of_order` | Not called | Late event retained for audit but not applied to monitoring state |
| `VALID`, route `monitoring` | `anomaly` | `diagnosed` | Known taxonomy signature matched |
| `VALID`, route `monitoring` | `anomaly` | `undetermined` | Genuine anomaly with no supported signature; manual review required |
| Valid anomaly with invalid cross-agent identity | `anomaly` | `invalid_input` | Agent 3 refuses inconsistent handoff |

## Supported end-to-end diagnoses

| Scenario context | Agent 3 result | Taxonomy evidence |
|---|---|---|
| Outer-race degradation | `FT_001 / outer_race_fault` | BPFO and kurtosis |
| Inner-race degradation | `FT_002 / inner_race_fault` | BPFI and kurtosis |
| Lubrication degradation | `FT_003 / lubrication_issue` | Broadband vibration, temperature rise, low discrete-fault kurtosis |
| Rolling-element fault on gearbox asset | `FT_007 / rolling_element_fault` | Dominant BSF and kurtosis |

The gearbox scenario name identifies the host asset. The main roadmap requires
high BSF to diagnose a ball/rolling-element bearing fault.

## Identity contract

The following values must remain linked:

```text
AnomalyEvent.asset_id    == TrustedBearingSignal.raw.asset_id
AnomalyEvent.bearing_id  == TrustedBearingSignal.raw.bearing_id
AnomalyEvent.channel_id  == TrustedBearingSignal.raw.channel_id
AnomalyEvent.timestamp   == TrustedBearingSignal.raw.timestamp
FaultDiagnosis.case_id   == AnomalyEvent.case_id
```

Agent 3 rejects a mismatch rather than diagnosing telemetry from the wrong
asset, bearing, channel, or event time.

## Provenance contract

The tests confirm that Agent 3 can trace a decision through:

```text
Agent 1 schema/config/master versions
  -> Agent 2 source versions + monitoring config/detector version
  -> Agent 3 source versions + FI config/taxonomy version
```

All three outputs serialize to JSON using `to_dict()`.

## Normalization integration policy

Configured Agent 1 aliases and unit conversions are applied before Monitoring:

```text
vibration_rms -> vib_rms_mm_s
temperature_f -> temp_c
speed_rps     -> rpm
```

The integration suite confirms that normalized outer-race telemetry remains
detectable and diagnosable. If a canonical value and an alias are both present,
the canonical value wins and the unused alias is reported in `unknown_fields`.
The caller's original input dictionary remains unchanged.

## Persistence and ordering policy

- Agent 1 persistence failure blocks Agents 2 and 3 because a trusted source
  record was not established.
- Agent 2 decision-persistence failure is visible in
  `persistence_status="failed"`, but it does not erase an anomaly already
  computed from a valid trusted signal. This is the current availability-first
  monitoring audit policy.
- Duplicate telemetry IDs do not update Agent 2 state or reach Agent 3.
- Unique late events are audited but do not update Agent 2 state or reach
  Agent 3.
- Equal timestamps with different telemetry IDs are allowed.
- Rejected Agent 1 decisions remain auditable when durable ingestion is used.

## Complete new three-agent test catalogue

The authoritative new suite is
`tests/test_agent1_agent2_agent3_integration.py` and contains 32 executable
cases.

| # | Case | Expected integrated behavior |
|---:|---|---|
| 1 | Outer-race fault | Valid -> anomaly -> FT_001 stage 3 |
| 2 | Inner-race fault | Valid -> anomaly -> FT_002 stage 3 |
| 3 | Lubrication issue | Valid -> anomaly -> FT_003 stage 2 |
| 4 | BSF fault on gearbox asset | Valid -> anomaly -> FT_007 rolling-element stage 3 |
| 5 | Healthy telemetry | Agent 2 healthy; Agent 3 not called |
| 6 | Startup telemetry | Agent 2 startup-suppressed; Agent 3 not called |
| 7 | Idle/stopped regime | Agent 2 regime-suppressed; Agent 3 not called |
| 8 | Repeated anomaly in configured cooldown | No second anomaly or diagnosis |
| 9 | Signal dropout | Agent 1 data-review route; downstream blocked |
| 10 | Stale live telemetry | Agent 1 freshness failure; downstream blocked |
| 11 | Unknown asset | Agent 1 rejection/stop; downstream blocked |
| 12 | Empty object | Graceful Agent 1 rejection |
| 13 | `None` input | Graceful Agent 1 rejection |
| 14 | String input | Graceful Agent 1 rejection |
| 15 | Numeric input | Graceful Agent 1 rejection |
| 16 | List input | Graceful Agent 1 rejection |
| 17 | Genuine anomaly without known signature | Agent 3 `undetermined`, manual checks present |
| 18 | Alias and unit-conversion payload | Normalized telemetry remains diagnosable |
| 19 | Canonical/alias conflict | Canonical wins; alias is reported; diagnosis remains correct |
| 20 | Input immutability | Original caller dictionary is unchanged |
| 21 | Identity/event linkage | Asset, bearing, channel, time, and case remain consistent |
| 22 | Provenance linkage | Version identifiers propagate through all agents |
| 23 | JSON output contract | All three typed results serialize with expected fields |
| 24 | Compatibility APIs | `process()` APIs still produce the expected event and diagnosis |
| 25 | Duplicate durable telemetry | Stored once; Agent 2 duplicate; no Agent 3 call |
| 26 | Out-of-order durable telemetry | Review route; no monitoring-state update or Agent 3 call |
| 27 | Equal timestamp, unique IDs | Both remain processable |
| 28 | Durable rejected telemetry | Agent 1 and Agent 2 audit records stored; no diagnosis |
| 29 | Agent 1 persistence failure | Chain stops before Monitoring |
| 30 | Agent 2 persistence failure | Failure visible; valid anomaly remains diagnosable |
| 31 | Mixed batch | Healthy, fault, flagged, BSF, and malformed rows remain isolated |
| 32 | Repeated Agent 3 call | Deterministic output except processing timestamp |

## Total integration coverage

| Suite | Tests |
|---|---:|
| `test_agent1_agent2_integration.py` | 8 |
| `test_agent2_agent3_integration.py` | 6 |
| `test_agent1_agent2_agent3_integration.py` | 32 |
| **Total integration tests** | **46** |

Current results:

```text
Integration suites: 46 passed
Full repository:    276 passed, 2 skipped, 0 failed
```

Run only the integration certification:

```powershell
python -m pytest tests/test_agent1_agent2_integration.py tests/test_agent2_agent3_integration.py tests/test_agent1_agent2_agent3_integration.py -q
```

Run the full repository:

```powershell
python -m pytest -q
```

## Completion statement

Agents 1, 2, and 3 are complete for the current deterministic development and
pilot contracts, including their stitched backend boundary. This coverage
substantially reduces integration risk but does not claim that software can be
proven bug-free. New connectors, schema fields, taxonomy modes, or orchestrator
state changes must add corresponding rows to this outcome matrix and executable
integration tests before release.

<!-- *********************** -->
