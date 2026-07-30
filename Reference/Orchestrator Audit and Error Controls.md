<!-- ************** Added by Prateek Mittal on 20th July 2026 ****************** -->
# Orchestrator Audit and Error Controls

## Audit layers

| Layer | Evidence |
|---|---|
| Agent decisions | Schema/config/master/taxonomy/index provenance already carried by Agents 1–8 |
| Orchestration | Ordered `pipeline_log`: node, status, latency and trigger outcome |
| Chat | Append-only JSONL event with timestamp, run ID, intent, planned agents and status |
| Unexpected API failure | Correlation error ID recorded without exposing exception detail |
| Execution | Agent 7 audit reference, connector result, persistence and idempotency status |
| Learning | Agent 8 linked execution ID, persistence/index status and confirmed labels |

Default audit path: `logs/orchestrator_audit.jsonl`. Payload bodies are excluded by default to reduce sensitive-data leakage. Production should send the same structured contract to immutable centralized logging with access control and retention policy.

## Failure principles

- Data/identity/grounding failures fail closed.
- LLM/reflection failures cannot authorize work or alter authoritative facts.
- Approval rejection always wins.
- Connector partial failure remains visible with completed side effects identified.
- Undefined API exceptions are sanitized and correlated.
- Audit write failure does not hide the user response; it is returned as `audit_write_failed` for operational alerting.
<!-- *********************** -->
