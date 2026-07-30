# Group A Backend Changes

This record covers only the backend scope authorized from the DRO Enhancement
Specification. No frontend, Group B, or Group C implementation is included.

## Decisions confirmed

- `md` is a backward-compatible alias for the `executive` persona.
- Cost data is deferred. Missing costs are returned as unavailable and are
  never estimated or fabricated.
- Historical cases are retrieved by Agent 8 and included as traceable
  citations in the final recommendation contract.
- Recommendation rejection feedback is routed to Agent 8.
- Reliability Engineering review is required after two consecutive rejections
  for the same asset and fault within 30 days.
- Free-text rejection enforcement and execution-trace ownership/deadline rules
  remain deferred.

## A1 — Prescriptive response contract

Agent 6 now produces a typed, verdict-first recommendation containing:

- verdict and reasoning;
- measured condition and thresholds;
- time, financial, and cascade consequences;
- ranked actions with rationale, urgency, and prescriptive score;
- fault, RUL, recommendation, and data-confidence values;
- explicit warnings for lower-confidence evidence.

The LLM may improve wording only. Facts, actions, measurements, risk values, and
citations originate from validated upstream agent outputs.

## A2 — Persona context

A single data-driven registry defines the seven personas:

1. Supervisor
2. Engineer
3. Maintenance
4. Manager
5. Executive
6. OT
7. Safety

The orchestrator builds one typed `PersonaContext` and passes it into every
Agent 1–8 call. The context contains the selected persona's display name,
domain, response depth, focus, details, actions, tags, and suppressed fields.
Unknown persona identifiers fail validation instead of silently receiving
another persona's response.

## A3 — Decision Support backend portion

The recommendation API contract now includes:

- cost if approved and cost if deferred;
- cost-data status;
- parts ETA compared with the RUL window;
- Agent 8 historical-case citations;
- authority-check status and reason.

Cost and authority values are deliberately `unavailable` / `not_evaluated`
until approved data and authority rules are provided. The approval-card UI is
outside this backend-only change.

## A5 — Rejection-learning backend portion

The backend accepts exactly five structured reason codes:

- `diagnosis_wrong`
- `parts_concern`
- `second_opinion`
- `wrong_window`
- `other`

`POST /api/recommendations/reject` records the rejection through Agent 8.
Two consecutive rejections for the same asset and fault within 30 days set
`reliability_review_required=true`. An approval breaks the consecutive sequence.
Equal timestamps are deterministically ordered by persisted event sequence.

## Agent 8 citations

Historical results contain:

- case ID;
- source label;
- fault mode;
- action taken;
- outcome;
- recorded time;
- relevance score.

SOP citations remain separate from historical-case citations so consumers can
distinguish procedure evidence from learned operational evidence.

## Configuration

Persona behavior is configured in `config/personas.json`. It includes the seven
persona templates and the `md` alias. The implementation does not duplicate
seven separate agent pipelines.

## Tests

The Group A certification suite is `tests/test_group_a_backend.py`. It covers:

- the exact seven-persona registry and `md` alias;
- rejection of unknown personas;
- grounded verdict-first Agent 6 output;
- explicit absence of unapproved cost/authority data;
- parts ETA versus RUL;
- SOP and Agent 8 citation separation;
- traceable historical-case retrieval;
- two-rejection escalation inside 30 days;
- expiration outside 30 days;
- approval resetting the rejection sequence;
- API routing of rejection feedback to Agent 8;
- API rejection of unsupported reason codes.

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_group_a_backend.py -q
```

## Deferred or out of scope

- Approved maintenance and deferred-failure cost sources and calculations.
- Persona authority thresholds and financial approval rules.
- Requiring free text conditionally for selected rejection reason codes.
- A6 four-step execution trace owners, deadlines, and escalation rules.
- A3/A5 frontend presentation and interaction.
- A4 frontend escalation timer.
- All Group B and Group C work.
