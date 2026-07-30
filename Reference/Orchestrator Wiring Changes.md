<!-- ************** Added by Prateek Mittal on 20th July 2026 ****************** -->
# Orchestrator Wiring Changes

## Runtime call policy

| User need / intent | Agents called | Stop condition |
|---|---|---|
| Status/readings | 1 | Trusted data result |
| Anomaly check | 1→2 | Healthy/no anomaly |
| Diagnosis | 1→2→3 | Diagnosis or invalid handoff |
| Risk/RUL | 1→2→3→4 | Risk card |
| SOP/guidance | 1→2→3→4→5 | Grounded/no-guidance result |
| Recommendation | 1→2→3→4→5→6 | Pending/pre-approved recommendation |
| Execution | 1→…→7 | Only after explicit approval |
| Learning | 1→…→8 | Only after successful/partial execution plus valid closure feedback |

Healthy/startup records stop at Monitoring. Flagged/rejected/duplicate/late data stop at Agent 1. Invalid diagnosis, risk, guidance or recommendation stops at its owning boundary.

The LangGraph now wires all eight domain agents. `pipeline_log` records node, status and latency for every call. API responses expose Agent 6 recommendation, Agent 7 execution and Agent 8 learned-case outputs.

## Tests

`tests/test_end_to_end_graph.py` contains 12 real graph tests for intent depth, Agents 1–6 full flow, approval pause, approved Executor routing, healthy and bad-data termination, audit fields and captured agent exceptions.
<!-- *********************** -->
