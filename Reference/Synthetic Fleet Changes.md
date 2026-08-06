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
- 55 named telemetry scenarios: 40 standard cases and 15 supplemental complex
  certification/replay cases.
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

The complex cases are stored under `complex_scenarios` in
`config/synthetic_fleet_config.json`. Each entry declares its source asset,
bearing position, base profile, field overrides, human description and expected
outcome. They are visible through the scenario API but deliberately excluded
from `load_fleet_scenario_map()`, so they cannot silently replace the current
dashboard state of an asset.

| Scenario | Difficult behavior certified |
|---|---|
| `complex_dual_race_signature` | BPFO and BPFI both match; outer race wins by configured priority and inner race remains a differential. |
| `complex_cage_rolling_tie` | Equal dominant FTF/BSF evidence resolves deterministically while retaining the alternative. |
| `complex_outer_stage3_boundary` | A band value exactly on the stage-3 threshold belongs to stage 3. |
| `complex_outer_kurtosis_boundary` | Kurtosis exactly on the strict gate does not fabricate a fault diagnosis. |
| `complex_nde_rolling_fault` | The non-drive-end bearing and channel are enriched and diagnosed correctly. |
| `complex_running_low_speed_conflict` | A running/49-RPM contradiction is stopped by Agent 1. |
| `complex_non_running_fault_signature` | Fault-like values in maintenance state are suppressed by Agent 2. |
| `complex_complete_low_quality` | Complete telemetry below the historian-quality floor goes to data review. |
| `complex_low_criticality_outer_stage2` | Asset business context changes severity, not the technical fault/stage. |
| `complex_high_load_outer_fault` | A true fault remains diagnosable and records a high-load regime. |
| `complex_low_load_outer_fault` | A true fault remains diagnosable and records a low-load regime. |
| `complex_asset_channel_mismatch` | A real channel belonging to another asset is rejected at the identity gate. |
| `complex_temperature_at_limit` | The exact configured sensor maximum is range-valid. |
| `complex_temperature_over_limit` | A value just over the maximum is held for review. |
| `complex_partial_critical_dropout` | One missing critical fault band triggers remediation instead of inference. |

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

`tests/test_complex_synthetic_scenarios.py` adds 58 checks covering the full
configuration-to-generated-data contract, Agent 1 quality/identity outcomes,
Agent 2 suppression and regimes, Agent 3 priorities/differentials/boundaries,
Agent 4 business-context effects, all seven persona formatters, scenario and
pipeline APIs, HITL exposure, mixed multi-asset isolation, finite values and
input immutability.

Regenerate and certify with:

```powershell
python scripts\generate_synthetic_fleet.py
python -m pytest tests\test_synthetic_fleet.py tests\test_complex_synthetic_scenarios.py -q
```

All values are explicitly synthetic demo data. Production historian, CMMS,
inventory, cost and safety sources remain separate adapter work.

Current certification result: `108 passed` across the two focused fleet suites,
`211 passed` across the wider fleet/routing/persona/HITL regression, and
`963 passed` for the complete repository suite.
