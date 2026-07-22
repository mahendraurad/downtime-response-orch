<!-- ************** Added by Prateek Mittal on 20th July 2026 ****************** -->
# Reflexion Agent Changes

`src/agents/reflexion_agent.py` is a bounded response-quality agent placed after response composition.

It checks:

- Response exists and respects configured length.
- Execution claims are supported by a successful/partial Agent 7 result.
- Sources are derived from successful orchestrator nodes.
- Unsupported execution statements are replaced with an approval-safe statement.
- Malformed drafts are blocked.

Reflexion does not change telemetry, diagnosis, risk, RUL, sources, recommendation, approval or execution state. The current implementation is deterministic. A future injected LLM may refine wording only behind the same invariant checks.

Configuration: `config/orchestrator_config.json → reflection`.

Tests cover acceptance, refinement, unsupported execution claims, malformed drafts and output-length limits.
<!-- *********************** -->
