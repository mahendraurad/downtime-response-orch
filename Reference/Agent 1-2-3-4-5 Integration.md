<!-- ************** Added by Prateek Mittal on 20th July 2026 ****************** -->

# Agent 1-2-3-4-5 Integration

## Complete backend flow

```text
Telemetry
 -> TrustedBearingSignal
 -> MonitoringResult / AnomalyEvent
 -> FaultDiagnosis
 -> RiskAssessment
 -> KnowledgeGuidance
```

Only eligible outputs advance. Healthy, suppressed, pending/cooldown, flagged,
rejected, duplicate, and out-of-order outcomes terminate before diagnosis,
risk, or guidance as appropriate.

## Final outcome matrix

| Condition | Final output |
|---|---|
| Healthy/startup/idle | Monitoring terminal result; no downstream case guidance |
| Bad/stale/unmapped data | Agent 1 review/stop; no downstream outputs |
| Known fault | Grounded diagnosis, risk, and cited SOP guidance |
| Unknown fault signature | Undetermined diagnosis, monitor risk, `no_guidance` for HITL |
| Invalid identity/provenance | Explicit invalid output; downstream routing stops |
| Retrieval adapter failure | `retrieval_failed`; no fabricated instructions |

## Agent 1-5 complete-chain tests

`tests/test_agent1_agent2_agent3_agent4_agent5_integration.py` contains 41
complete-flow cases:

- Four supported diagnosed fault families.
- Four non-anomaly terminal scenarios.
- Five malformed payload types.
- Undetermined/monitor/no-guidance flow.
- OPC-UA normalized fault flow.
- Full provenance linkage.
- JSON serialization of all five outputs.
- Mixed-batch isolation.
- Citation completeness across every supported fault.
- Stale live, timezone-free, low-quality, and physically invalid telemetry.
- Canonical/alias conflicts, unknown-field audit, and caller immutability.
- Durable duplicate, out-of-order, equal-time, and persistence-failure behavior.
- Agent 2 decision-persistence failure and alert cooldown behavior.
- Fresh Event Hub source-profile normalization and freshness.
- Agent 2 identity, Agent 3 taxonomy, and Agent 4 risk-case tampering.
- Retriever outage, missing citation, wrong-fault hit, and non-finite score handling.
- Deterministic Agent 5 replay apart from its processing timestamp.

## Total integration certification

| Boundary suite | Tests |
|---|---:|
| Agent 1 -> 2 | 8 |
| Agent 2 -> 3 | 6 |
| Agent 1 -> 3 | 32 |
| Agent 3 -> 4 | 10 |
| Agent 1 -> 4 | 33 |
| Agent 4 -> 5 | 9 |
| Agent 1 -> 5 | 41 |
| **Total** | **139** |

```text
All Agent 1-5 integration suites: 139 passed
Full repository: 485 passed, 1 skipped, 0 failed
```

Run all boundary suites:

```powershell
python -m pytest tests/test_agent1_agent2_integration.py tests/test_agent2_agent3_integration.py tests/test_agent1_agent2_agent3_integration.py tests/test_agent3_agent4_integration.py tests/test_agent1_agent2_agent3_agent4_integration.py tests/test_agent4_agent5_integration.py tests/test_agent1_agent2_agent3_agent4_agent5_integration.py -q
```

<!-- *********************** -->
