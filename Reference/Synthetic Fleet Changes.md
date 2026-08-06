# Synthetic Fleet Changes

## Objective

Expand the original small demo dataset into a configurable fleet that can test
agent behavior, backend APIs, persona views and frontend action paths without
pretending that synthetic values are live plant facts.

## Design decision

`config/synthetic_fleet_config.json` is the editable source for 24 additional
assets. `scripts/generate_synthetic_fleet.py` combines these definitions with
the six original assets and deterministically produces the runtime fixtures.
This keeps identities, bearings, telemetry and work orders aligned and avoids
maintaining several large JSON files manually.

Generated runtime data:

- 30 assets across six equipment classes.
- 60 bearings and 60 unique channels (two per asset).
- 40 named telemetry scenarios, including all original scenarios.
- Twelve ordered historical samples for every generated fleet scenario.
- 18 seeded work orders across critical, high and medium priorities.
- A display-ID-to-canonical-ID/scenario map used by chat and dashboard APIs.

## Scenario coverage

The profile library covers healthy behavior; stages 1–3 where meaningful for
outer race, inner race, lubrication, rolling-element and cage faults; thermal
rise; startup suppression; electrical deviation; load spike; borderline
quality; and signal dropout. The generated latest records produce a deliberate
mix of healthy, warning and critical dashboard states while never using an
identity-invalid record.

## Backend and frontend consumption

- Asset, bearing and telemetry loaders continue to expose their existing
  contracts.
- `/api/pipeline/scenarios` discovers every generated scenario.
- `/api/dashboard/assets` projects all registered assets through the real Agent
  1–4 path used by the application.
- Work-order APIs load the generated demo work-order fixture.
- Websocket sensor baselines are built from the fleet mapping instead of a
  five-asset dictionary.
- Persona fleet prompts and the chat welcome summary use the asset rows loaded
  from the backend; fleet counts are no longer fixed at five.
- Persona greetings, tags, quick prompts and action cards are projected from
  current backend work orders. Role-specific relevance rules control which
  active orders appear, while all work-order facts come from API records.
- Terminal work orders (`Closed`, `Completed`, `Cancelled` or `Rejected`) are
  automatically excluded from active persona tasks.

## Certification

`tests/test_synthetic_fleet.py` checks scale, uniqueness, identity mapping,
history ordering, profile diversity, Agent 1 decisions, representative Agent
1–4 diagnosis/risk results, healthy/suppressed non-fabrication, all seven
personas, scenario and dashboard APIs, work orders, websocket baselines, and
the frontend's data-driven fleet-count contract.

Regenerate and certify with:

```powershell
python scripts\generate_synthetic_fleet.py
python -m pytest tests\test_synthetic_fleet.py -q
```

All values are explicitly synthetic demo data. Production historian, CMMS,
inventory, cost and safety sources remain separate adapter work.

Current certification result: `50 passed` for the focused fleet suite and
`905 passed` for the complete repository suite.
