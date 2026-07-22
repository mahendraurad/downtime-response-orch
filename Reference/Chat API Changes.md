<!-- ************** Added by Prateek Mittal on 20th July 2026 ****************** -->
# Chat API Changes

## 2026-07-22 - LangSmith agent tracing

- The chat request, orchestrator, Agents 1-8, Agent 8 history retrieval, and
  Reflexion are explicit nested LangSmith trace runs.
- Trace tags identify `agent-1` through `agent-8`, `chat-api`, `orchestrator`,
  and `reflexion` for filtering in project `DRO`.
- Actual inputs, outputs, routing decisions, latency, and errors are observable.
- Tracing is environment-controlled, optional, and fail-open.
- `.env.example` contains placeholders only; the real API key remains in the
  Git-ignored `.env`.
- Remote smoke testing must be performed from an organisation-approved machine
  because traces can contain operational telemetry and user context.

## 2026-07-22 - Multi-asset weekly action plans

- Explicitly named assets are collected in the order requested and deduplicated.
- Each asset is evaluated independently through the full orchestrated pipeline.
- Every result is labelled with its display ID, action, deadline, status, and
  asset-specific pipeline log.
- One asset's failure or data-quality rejection does not suppress the remaining
  asset results.
- The React chat no longer returns after the first recognised asset; it sends
  the complete multi-asset request to `POST /api/chat` and renders the returned
  plan, actions, details, and clarification prompts.
- A preselected single-asset UI context cannot override multiple asset IDs that
  the user explicitly names in the message.

The exact project-manager example for `M-104`, `P-207`, and `C-301` now returns
three ordered entries in `multi_asset_results`. Coverage is in
`tests/test_chat_multi_asset.py`; the current full repository result is `683 passed`.

## 2026-07-22 - Structured multi-turn clarification

- Conceptual RUL and signal questions are answered without inventing an asset.
- Asset-specific questions return exact missing fields before any agent runs.
- Unknown assets request authorized registration and do not enter the pipeline.
- Fleet questions request timeframe and an approved fleet snapshot rather than
  misusing the single-asset agent chain.
- `conversation_id` retains bounded clarification context across follow-up
  turns; the maximum in-memory session count is configurable.
- Incomplete telemetry is stopped before Agent 1 with field-specific questions.
- Complete follow-up context resumes the original intent at the correct depth.

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
