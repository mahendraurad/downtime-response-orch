# Group A Backend Changes

This record covers the completed Group A backend scope and its minimum frontend
contract wiring. A4's visual countdown timer remains intentionally excluded.

## Decisions confirmed

- `md` is a backward-compatible alias for the `executive` persona.
- Demo costs and persona authority limits are explicitly labelled and loaded
  from `config/decision_support_config.json`; they can be replaced without code
  changes. Missing data still returns `unavailable` and is never fabricated.
- Historical cases are retrieved by Agent 8 and included as traceable
  citations in the final recommendation contract.
- Recommendation rejection feedback is routed to Agent 8.
- Reliability Engineering review is required after two consecutive rejections
  for the same asset and fault within 30 days.
- `other` requires free text in the frontend. A6 ownership, deadlines, and the
  Supervisor → Manager → VP Operations escalation path are configurable.

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

The current development model contains configured demo figures and authority
limits. Every response carries `cost_data_status=configured_demo`, the currency,
calculation basis, and a stable policy version so these figures cannot be
mistaken for ERP/accounting facts. The approval card reads this nested typed
contract directly.

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
The frontend submits one reason using radio selection and requires text only
when `other` is selected.

## A6 — Execution trace

Agent 7 owns and returns the execution trace. React does not recreate it.
Non-monitoring work has four steps: work order, notification, parts, and
maintenance window. Each contains status, owner, deadline, and applicable
escalation rules. Default adapters are explicitly labelled as mocks.

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

Persona behavior is configured in `config/personas.json`. Cost, authority, and
execution policies are configured in `config/decision_support_config.json`.
The implementation does not duplicate seven separate agent pipelines.

## Tests

The Group A certification suite is `tests/test_group_a_backend.py`. It covers:

- the exact seven-persona registry and `md` alias;
- rejection of unknown personas;
- grounded verdict-first Agent 6 output;
- configured demo costs, provenance, and persona-specific authority limits;
- parts ETA versus RUL;
- SOP and Agent 8 citation separation;
- traceable historical-case retrieval;
- two-rejection escalation inside 30 days;
- expiration outside 30 days;
- approval resetting the rejection sequence;
- API routing of rejection feedback to Agent 8;
- API rejection of unsupported reason codes.
- Agent 7 four-step trace ownership/deadline/escalation fields;
- frontend/backend Decision Support, rejection, dashboard, and work-order
  contract wiring;
- 50 deterministic and 50 live-LLM chat QC scenarios with Excel evidence.

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_group_a_backend.py -q
```

## Deferred or out of scope

- Replace the labelled demo economics with governed ERP/finance values.
- A4 frontend escalation countdown timer.
- Production CMMS, inventory, notification, historian, and scheduler adapters.
- Group B/C visual certification that requires a frontend test runner.
