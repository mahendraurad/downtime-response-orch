# DRO LangSmith Evaluation Plan

## Purpose

Use the same versioned 50-case chat corpus for local quality control and
LangSmith experiments. The local Excel report is the review artefact; LangSmith
is the trace, comparison, scoring, and production-feedback system.

The workflow follows the official LangSmith model: curate a dataset, define a
target and evaluators, run an offline experiment, compare results, then promote
useful production failures back into the dataset.

References:

- https://docs.langchain.com/langsmith/evaluation
- https://docs.langchain.com/langsmith/evaluate-llm-application
- https://docs.langchain.com/langsmith/pytest

## Dataset

Dataset name: `DRO Chat QC 50`

Source: `quality/chat_qc_questions.json`

Each example contains:

- question, persona, and optional evidence context;
- expected route/intent and clarification state;
- required or prohibited agent calls;
- required missing fields for clarification;
- expected multi-asset result identities;
- the complete actual API response and routing trace after execution.

The corpus covers concepts, known and unknown assets, missing context,
single-asset and multi-asset plans, seven personas, HITL-producing full runs,
Agent 8 history, conversational/reset behavior, adversarial requests, duplicate
asset mentions, evidence requirements, and graceful fallbacks.

## Offline evaluation gates

Every pull request affecting orchestration, agents, prompts, personas, chat, or
retrieval should run these deterministic gates:

1. `http_success`: `/api/chat` returns the expected HTTP status.
2. `intent_accuracy`: returned intent equals the labelled intent.
3. `clarification_accuracy`: clarification is raised only when labelled.
4. `minimum_agent_route`: all required nodes ran and irrelevant agents did not.
5. `evidence_gate`: asset/fleet conclusions are absent when evidence is absent.
6. `multi_asset_completeness`: every named, evidenced asset appears once.
7. `persona_contract`: the selected persona is preserved and restricted fields
   are not exposed to unauthorised personas.
8. `citation_integrity`: SOP/learned-case statements have source identifiers;
   no citation is invented when retrieval returns no source.
9. `hitl_integrity`: gated work includes the correct gate, authorised resolver,
   resumable run ID, and no side effect before approval.
10. `reflection_bound`: iteration count is at most the configured maximum of 3
    and the termination reason is recorded.
11. `error_gracefulness`: defined and unexpected dependency failures return a
    useful response without fabricated decisions.
12. `latency`: report p50/p95 by intent; thresholds are established after three
    baseline runs rather than invented in advance.

Release gate: all deterministic safety/routing assertions pass. LLM-quality
scores are reviewed as trends and do not override a failed evidence or HITL
gate.

## LLM-as-judge rubric

After the deterministic gates are stable, bind an LLM evaluator to the dataset
with a 0-4 rubric for:

- directness and verdict-first structure;
- consequence framing and action rationale;
- persona relevance and appropriate detail;
- groundedness in the supplied agent output;
- clarification usefulness;
- citation correctness;
- absence of fabricated measurements, RUL, costs, inventory, or fleet claims.

Score 0 is unsafe/incorrect; 2 is usable with material omissions; 4 is correct,
grounded, persona-appropriate, and concise. Any fabrication score is also a
binary release blocker.

## Production evaluation

Apply reference-free online evaluators to a sampled set of `/api/chat` root
traces for response structure, evidence presence, clarification quality,
reflection bounds, exceptions, and latency. Do not send secrets, raw credentials,
or unnecessary operator free text to traces. Review low-scoring traces weekly,
redact them, label the expected behavior, and add them to a regression dataset.

## Commands

Run locally and generate Excel:

```powershell
python scripts/run_chat_qc.py
```

Run locally and upload the 50 inputs, outputs, expectations, and trace metadata:

```powershell
python scripts/run_chat_qc.py --publish-langsmith --dataset-name "DRO Chat QC 50"
```

Run without external LLM calls for deterministic routing diagnosis:

```powershell
python scripts/run_chat_qc.py --disable-llm
```

The project must provide `LANGSMITH_TRACING`, `LANGSMITH_ENDPOINT`,
`LANGSMITH_API_KEY`, and `LANGSMITH_PROJECT` through `.env` or its deployment
secret provider. Secrets are never stored in the dataset or workbook.

