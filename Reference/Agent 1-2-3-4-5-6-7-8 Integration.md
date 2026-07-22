<!-- ************** Added by Prateek Mittal on 20th July 2026 ****************** -->
# Agent 1-8 Integration

```text
Telemetry -> trusted signal -> anomaly -> diagnosis -> risk -> grounded guidance
          -> ranked recommendation -> approved execution -> confirmed learned case
```

Safety gates stop bad data, healthy signals, invalid diagnosis/risk, ungrounded guidance, invalid recommendations, missing/rejected approval, failed execution and invalid feedback at their owning boundary.

The orchestrator now contains Prescriptive, Executor and Learning nodes. It stops at a pending recommendation by default, executes only after approval, and learns only when closure feedback exists.

`tests/test_agent1_to_agent8_integration.py` contains 15 complete-chain tests:

- Four supported fault families complete all eight agents.
- Healthy, startup, dropout and unknown-asset paths never create work.
- Pending approval blocks execution/learning.
- Provenance links through Agent 8.
- All outputs serialize as JSON.
- Learned outcomes are searchable.
- Ungrounded Agent 5 guidance stops Agents 6/7.
- Tampered Agent 6 actions are rejected by Agent 7.
- Failed executions cannot poison Agent 8 memory.

```text
Complete Agent 1-8 suite: 15 passed
All integration suites: 154 passed
Full repository: 594 passed, 1 skipped, 0 failed
```
<!-- *********************** -->
