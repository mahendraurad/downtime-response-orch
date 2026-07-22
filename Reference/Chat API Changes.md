<!-- ************** Added by Prateek Mittal on 20th July 2026 ****************** -->
# Chat API Changes

## 2026-07-22 - Unseen telemetry and learned-history routing

- Arbitrary canonical `context.signal` inputs are tested across status,
  anomaly, diagnosis, risk, guidance, and recommendation depth.
- Natural `anomalous` and `maintenance action` phrasing routes correctly.
- SOP requests stop after Agent 5 instead of invoking Agent 6.
- Recent-failure questions route to Agent 8 without running Agents 1-7.
- MD/executive responses return up to three newest validated closed cases,
  references, structured outputs, and an audited history-query log entry.
- Empty history is explicit; failures and lessons are never fabricated.

## Previous state

`POST /api/chat` used canned keyword responses and otherwise told the caller to invoke the pipeline manually. It did not call agents, expose a call plan, reflect answers or audit the interaction.

## Current behavior

The endpoint now:

1. Validates message length/content.
2. Creates a correlation `run_id`.
3. Uses `query_router.plan_query()` to select the minimum agent chain.
4. Accepts either `context.signal` or `context.scenario` plus optional inventory/operations context.
5. Runs the orchestrator at the required depth.
6. Formats for the requested persona.
7. Applies the Reflexion Agent.
8. Returns intent, call plan, actual pipeline log, sources, structured Agent 6/7 outputs and audit metadata.

Execution language in chat never constitutes approval. Execution requests reach Agent 6 and pause with `approval_status=pending` unless an approved execution workflow is invoked separately.

## Open-ended questions

- Asset-specific questions without telemetry return `needs_context=true`; no result is fabricated.
- General reliability questions receive a scoped workflow explanation and request asset context for decisions.
- Unknown intent routes to general/reflexion, not to every agent.
- Signal-backed questions invoke only the minimum required agent chain shown in the wiring document.

## Errors

- Defined request/schema/length/not-found errors return explicit 4xx responses.
- Unexpected errors return HTTP 500 with `INTERNAL_ERROR`, a correlation `error_id`, a safe message and retryability—never raw exception details.
- Unexpected failures are audited using their correlation ID.

`tests/test_orchestrator_chat_reflexion.py` contains 24 tests spanning routing, reflection, audit, chat behavior, agent-backed responses, approval protection, schema errors and sanitized unexpected failures.
<!-- *********************** -->
