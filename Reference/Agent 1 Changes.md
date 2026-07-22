<!-- ************** Added by Prateek Mittal on 16th July 2026 ****************** -->
<!-- Shareable Agent 1 change record, contracts, examples, and test catalogue. -->

# Agent 1 Changes

This is the running change record for Agent 1, the Data Foundation Agent.
Add future Agent 1 implementation and behavior changes here before moving to
the next agent.

## 2026-07-16 - Data-quality utility hardening

### Objective

Close the main gaps between the Data Foundation MVP and the developer roadmap
without introducing LLM-driven or independently deployed utility agents. The
new components are deterministic utilities owned and orchestrated by Agent 1.

### Utility structure

Agent 1 now coordinates these utility responsibilities:

1. Schema parsing through `BearingSignalFact`.
2. Field and unit normalization through `normalize_record`.
3. Timestamp syntax, timezone, future-time, and live freshness validation
   through `validate_timestamp`.
4. Asset, bearing, and channel mapping through the existing validators.
5. Full asset-to-bearing ownership validation through
   `validate_asset_bearing_relationship`.
6. Existing range, completeness, accuracy, and consistency validation.
7. Existing asset and bearing context enrichment.
8. Explicit downstream routing through `evaluate_routing`.

The added utilities live in `src/tools/data_foundation_utilities.py`. They are
pure or deterministic functions and are not separate autonomous agents.

### Timestamp and freshness behavior

- `timestamp_utc` must be valid ISO-8601.
- A timezone is required by default.
- Timestamps beyond the configured future tolerance are flagged.
- Maximum age is enforced only for configured live sources.
- Historical test/historian records are syntax-checked but do not fail merely
  because they are old.
- `record_age_seconds` is included in the output for audit.
- A clock function can be injected into `DataFoundationAgent` for deterministic
  tests.

Configuration was added under `freshness` in `config/dfa_config.json`:

```json
{
  "require_timezone": true,
  "max_future_seconds": 30,
  "max_age_seconds": 300,
  "live_sources": ["live", "stream", "event_hub"]
}
```

### Normalization behavior

Input normalization occurs on a copy of the incoming dictionary. Agent 1 does
not mutate the caller's data.

Configured aliases:

- `vibration_rms` -> `vib_rms_mm_s`
- `temperature_c` -> `temp_c`
- `speed_rpm` -> `rpm`

Configured unit conversions:

- `temperature_f` -> `temp_c`
- `speed_rps` -> `rpm`

If both an alias/source field and its canonical target exist, the canonical
field wins. The unused source field is reported as unknown instead of silently
overwriting canonical data.

Every applied conversion is returned in `normalization_actions`. Fields that
remain outside the canonical schema are returned in `unknown_fields`, allowing
schema drift to be observed without breaking ingestion.

### Context integrity behavior

The previous implementation verified that `bearing_id` and `channel_id`
resolved to the same bearing. Agent 1 now also verifies that the resolved
bearing belongs to the supplied `asset_id`.

The complete identity relationship is therefore:

```text
asset_id -> bearing_id -> channel_id
```

An ownership mismatch is `REJECTED` and routed to `stop`.

### Explicit downstream routing

Routing is configured in `config/dfa_config.json`:

```json
{
  "VALID": "monitoring",
  "FLAGGED": "data_review",
  "REJECTED": "stop"
}
```

Agent 1 output now includes:

- `downstream_eligible`
- `next_route`
- `routing_reason`

`TrustedBearingSignal.is_processable` now reflects
`downstream_eligible`. Consequently, a `FLAGGED` record no longer flows
directly to Monitoring. It must first be reviewed or remediated. The LangGraph
foundation routing also checks this field.

### Output contract additions

`TrustedBearingSignal` gained these additive fields:

```text
normalization_actions: list[str]
unknown_fields: list[str]
record_age_seconds: float | null
downstream_eligible: bool
next_route: str
routing_reason: str
```

`ValidationDetail` gained:

```text
freshness_valid: bool
```

All fields are included by `TrustedBearingSignal.to_dict()`.

### Tests added

The Agent 1 pytest suite increased from 30 to 47 tests. Added coverage includes:

- Valid timestamp audit information.
- Invalid ISO-8601 timestamp.
- Missing timezone.
- Stale live record.
- Fresh live record.
- Historical record age bypass.
- Future timestamp.
- Field alias normalization.
- Fahrenheit-to-Celsius conversion.
- RPS-to-RPM conversion.
- Canonical-field precedence.
- Input dictionary immutability.
- Unknown-field reporting.
- Asset/bearing ownership mismatch.
- Explicit routes for valid, flagged, and rejected records.
- Serialization of all new audit fields.

### Test results

Agent 1 suite:

```text
47 passed
```

Full repository regression suite:

```text
142 passed, 3 failed, 2 skipped
```

The three failures existed before this Agent 1 work and are unrelated:

1. Two Failure Intelligence tests expect `outer_race_fault` for the gearbox
   scenario while the active taxonomy returns `gearbox_fault`.
2. One Predictive Risk test expects a 7-day gearbox RUL while the active
   taxonomy/configuration returns 10 days.

No new full-suite regression was introduced by the Agent 1 changes.

### Deferred at the end of the first pass

These items were identified for the completion pass documented immediately
below:

- Trusted-signal repository interface and PostgreSQL persistence.
- Duplicate telemetry/idempotency checks.
- Out-of-order event detection across records.
- Configuration and master-data version identifiers in every result.
- Source-specific normalization profiles for real historian/OPC-UA/Event Hub
  payloads.
- A separate Data Remediation Agent was considered but was not required. The
  existing deterministic remediation utility is the completed MVP design.

## 2026-07-16 - Deferred scope completed

### Durable trusted-signal repository

Added `SQLiteTrustedSignalRepository` in
`src/tools/trusted_signal_repository.py`. It provides the development/pilot
persistence boundary required by Agent 1:

- `exists(telemetry_id)`
- `latest_event_epoch(bearing_id)`
- `save(trusted_signal, event_epoch)`
- `get(telemetry_id)`
- `count()`

The database path is configurable under `repository.sqlite_path`. The default
is `data/trusted_signals.db`, which is ignored by Git. The repository stores the
complete serialized Agent 1 decision, including rejected records, so ingestion
and data-quality failures remain auditable.

`process()` remains a stateless validation operation. `process_and_store()` is
the durable ingestion operation. This separation keeps unit checks repeatable
while making production-style ingestion explicit.

Factories:

```python
DataFoundationAgent.from_data_files(repository=my_repository)
DataFoundationAgent.with_sqlite_repository("data/trusted_signals.db")
```

The repository is behind a narrow interface, so a PostgreSQL implementation
can replace SQLite without changing Agent 1 validation logic or its output
contract.

### Idempotency

When durable ingestion is used, `telemetry_id` is the idempotency key.

- First occurrence: stored normally.
- Repeated occurrence: not stored again.
- `duplicate_detected=true`.
- `persistence_status="duplicate_not_stored"`.
- Route is `stop` because the original event has already been processed.

### Cross-record event ordering

The repository tracks the latest timestamp for each `bearing_id`.

- In-order events remain eligible for Monitoring.
- A unique event older than the latest persisted event is still stored for
  audit, but is marked `out_of_order=true`, changed to `FLAGGED`, and routed to
  `data_review`.
- Equal timestamps with different telemetry IDs are allowed. Duplicate identity
  is controlled by `telemetry_id`, not timestamp alone.

### Configuration and master-data provenance

Every Agent 1 output now includes:

- `schema_version` - current contract version (`1.2`).
- `config_version` - stable SHA-256-derived identifier for effective DFA config.
- `master_data_version` - stable SHA-256-derived identifier for loaded asset and
  bearing masters.
- `source_profile` - selected source normalization profile or `default`.

These identifiers allow an output to be traced to the rules and reference data
that produced it.

### Source-specific normalization profiles

`dfa_config.json` now supports source-specific aliases and conversions in
addition to global normalization.

Included profiles:

- `opcua`: maps `MotorSpeed`, `BearingTemp`, and `VibrationRMS`.
- `event_hub`: maps `assetId`, `bearingId`, `channelId`, and `eventTime`.

Source-specific mappings override global mappings only for that source. The
canonical field still wins if both source and canonical fields are supplied.

### Persistence failure behavior

Repository failures never disappear silently and never permit downstream
analysis:

```text
persistence_status = failed
downstream_eligible = false
next_route = data_review
routing_reason = persistence error detail
```

### Additional tests

Agent 1 coverage increased from 47 to 60 tests. New tests cover:

- Provenance values are populated and stable.
- Master-data changes produce a different master version.
- OPC-UA normalization profile.
- Event Hub normalization profile.
- Missing repository error for durable ingestion.
- Successful SQLite persistence and retrieval.
- Duplicate telemetry rejection and single-row persistence.
- Out-of-order event storage and review routing.
- In-order event eligibility.
- Rejected-record audit persistence.
- Repository failure handling.
- Durable batch ingestion.

Current Agent 1 result:

```text
60 passed
```

Full repository regression after the completion pass:

```text
155 passed, 3 failed, 2 skipped
```

The same three pre-existing gearbox taxonomy/RUL expectation failures remain;
there are no Agent 1 regressions.

## Expected Input

Agent 1 accepts one Python dictionary/JSON object per telemetry event.

### Required canonical fields

```json
{
  "telemetry_id": "TEL-1001",
  "timestamp_utc": "2026-07-16T10:00:00Z",
  "asset_id": "AST_MTR_002",
  "bearing_id": "BRG_003",
  "channel_id": "CH_003"
}
```

### Recommended complete input

```json
{
  "telemetry_id": "TEL-1001",
  "timestamp_utc": "2026-07-16T10:00:00Z",
  "asset_id": "AST_MTR_002",
  "bearing_id": "BRG_003",
  "channel_id": "CH_003",
  "rpm": 1768.0,
  "load_pct": 70.0,
  "machine_state": "running",
  "startup_shutdown_flag": false,
  "vib_rms_mm_s": 1.9,
  "vib_peak_g": 1.0,
  "kurtosis": 2.1,
  "temp_c": 53.0,
  "bpfo_energy": 0.6,
  "bpfi_energy": 0.5,
  "bsf_energy": 0.2,
  "ftf_energy": 0.1,
  "envelope_peak_hz": 115.0,
  "motor_current_a": 37.0,
  "voltage_v": 415.0,
  "current_deviation": 0.2,
  "signal_quality_score": 1.0,
  "data_source": "historian"
}
```

### Source-profile input example

An OPC-UA connector may submit source names instead of canonical signal names:

```json
{
  "telemetry_id": "TEL-OPC-1",
  "timestamp_utc": "2026-07-16T10:00:00Z",
  "asset_id": "AST_MTR_002",
  "bearing_id": "BRG_003",
  "channel_id": "CH_003",
  "MotorSpeed": 1768.0,
  "BearingTemp": 53.0,
  "VibrationRMS": 1.9,
  "kurtosis": 2.1,
  "bpfo_energy": 0.6,
  "bpfi_energy": 0.5,
  "data_source": "opcua"
}
```

Agent 1 converts these fields to `rpm`, `temp_c`, and `vib_rms_mm_s` and
records every conversion in `normalization_actions`.

## Expected Output

Agent 1 always returns a `TrustedBearingSignal`, including for malformed or
rejected input. The output contains:

```json
{
  "raw": {},
  "asset_ctx": {},
  "bearing_ctx": {},
  "validation": {
    "asset_mapping": true,
    "bearing_mapping": true,
    "fields_complete": true,
    "ranges_valid": true,
    "accuracy_ok": true,
    "consistency_valid": true,
    "freshness_valid": true,
    "reasons": []
  },
  "data_quality_score": 1.0,
  "validation_status": "VALID",
  "quality_report": {},
  "normalization_actions": [],
  "unknown_fields": [],
  "record_age_seconds": 3.2,
  "downstream_eligible": true,
  "next_route": "monitoring",
  "routing_reason": "record passed the Data Foundation quality gate",
  "schema_version": "1.2",
  "config_version": "16-character-version",
  "master_data_version": "16-character-version",
  "source_profile": "default",
  "persistence_status": "stored",
  "duplicate_detected": false,
  "out_of_order": false,
  "processed_at": "2026-07-16T10:00:03.200000+00:00"
}
```

When `process()` is used instead of `process_and_store()`, expected
`persistence_status` is `not_requested`.

### Example outcome: healthy record

```json
{
  "validation_status": "VALID",
  "data_quality_score": 1.0,
  "downstream_eligible": true,
  "next_route": "monitoring",
  "persistence_status": "stored"
}
```

### Example outcome: incomplete or stale record

```json
{
  "validation_status": "FLAGGED",
  "downstream_eligible": false,
  "next_route": "data_review",
  "validation": {
    "fields_complete": false,
    "freshness_valid": false,
    "reasons": ["Missing critical fields: temp_c", "live record is stale"]
  }
}
```

### Example outcome: unknown asset or mapping mismatch

```json
{
  "validation_status": "REJECTED",
  "data_quality_score": 0.0,
  "downstream_eligible": false,
  "next_route": "stop",
  "persistence_status": "stored"
}
```

Rejected records are stored when durable ingestion is used so data-quality
problems remain available for audit.

### Example outcome: duplicate event

```json
{
  "duplicate_detected": true,
  "persistence_status": "duplicate_not_stored",
  "downstream_eligible": false,
  "next_route": "stop",
  "routing_reason": "duplicate telemetry_id already persisted"
}
```

### Example outcome: out-of-order event

```json
{
  "validation_status": "FLAGGED",
  "out_of_order": true,
  "persistence_status": "stored",
  "downstream_eligible": false,
  "next_route": "data_review"
}
```

## Agent 1 completion boundary

Agent 1 is complete for the current deterministic development/pilot scope:

- Canonical schema and source-profile normalization.
- Timestamp and live freshness validation.
- Full asset/bearing/channel mapping and enrichment.
- Range, completeness, quality, and physical-consistency validation.
- Quality scoring and actionable explanations.
- Explicit downstream routing.
- Durable local persistence.
- Duplicate and cross-record ordering checks.
- Configuration and master-data provenance.
- Batch and single-record operation.
- Comprehensive pytest coverage.

Real PostgreSQL, historian, OPC-UA, and Event Hub deployments require only
connector/repository adapters; they do not require changes to Agent 1's decision
logic or output contract.

## Complete Test Case Catalogue

The authoritative executable suite is `tests/test_data_foundation.py`. It
contains 60 tests. The catalogue below separates the original 30-test baseline
from the 17 utility-hardening tests and the 13 completion tests.

### Original baseline tests (1-30)

| # | Test case | Expected behavior |
|---:|---|---|
| 1 | `test_valid_signal_returns_valid_status` | A complete, mapped signal is `VALID`, enriched, and scores above the quality threshold. |
| 2 | `test_valid_signal_has_quality_score` | Every output carries a numeric quality score between 0 and 1. |
| 3 | `test_processed_at_is_populated` | Every result carries an Agent 1 processing timestamp. |
| 4 | `test_to_dict_is_serialisable` | `TrustedBearingSignal.to_dict()` can be serialized as JSON. |
| 5 | `test_unknown_asset_id_is_rejected` | An unregistered asset is `REJECTED`, receives no context, and scores zero. |
| 6 | `test_unknown_asset_rejection_reason_is_informative` | The rejection reason identifies the unknown asset ID. |
| 7 | `test_unknown_asset_is_not_processable` | A rejected unknown-asset record cannot continue downstream. |
| 8 | `test_healthy_signal_passes_all_checks` | Healthy telemetry passes mapping, completeness, and range checks. |
| 9 | `test_healthy_signal_asset_context_correct` | Asset type, production line, criticality, and bottleneck attributes are joined correctly. |
| 10 | `test_healthy_signal_bearing_context_has_baselines` | The bearing context includes the baseline statistics required by Monitoring. |
| 11 | `test_outer_race_signal_is_valid` | Clean outer-race fault telemetry remains processable; Agent 1 does not diagnose it. |
| 12 | `test_outer_race_asset_is_bottleneck` | Bottleneck and ISO-zone metadata are preserved for the outer-race asset. |
| 13 | `test_inner_race_signal_is_valid` | Clean inner-race telemetry passes the data-quality gate. |
| 14 | `test_inner_race_bearing_context_correct` | The expected bearing position and bearing mapping are joined. |
| 15 | `test_lubrication_signal_is_valid` | Clean lubrication-issue telemetry passes Agent 1. |
| 16 | `test_lubrication_asset_is_pump` | The correct pump asset context is attached. |
| 17 | `test_bottleneck_asset_flags_correctly` | High-risk bottleneck context is represented accurately. |
| 18 | `test_gearbox_fault_signal_is_valid` | Clean gearbox telemetry passes the data-quality layer. |
| 19 | `test_startup_flag_is_passed_through` | Startup/shutdown state remains processable and is noted for Monitoring suppression. |
| 20 | `test_dropout_record_is_flagged` | A dropout with null signals and poor source quality is `FLAGGED` and routed for review. |
| 21 | `test_dropout_reasons_mention_missing_fields` | The quality explanation names missing fields. |
| 22 | `test_negative_rpm_is_flagged` | Physically impossible negative RPM is flagged with an RPM-specific reason. |
| 23 | `test_load_over_110_is_flagged` | Load beyond the configured maximum is flagged. |
| 24 | `test_temperature_above_limit_is_flagged` | Temperature above the bearing/sensor limit is flagged. |
| 25 | `test_missing_required_fields_is_rejected` | Missing identity and timestamp contract fields cause graceful rejection. |
| 26 | `test_empty_dict_is_rejected` | An empty payload returns a rejected result rather than raising. |
| 27 | `test_non_dict_input_handled` | Non-dictionary input is handled safely and rejected. |
| 28 | `test_batch_processes_all_scenarios` | Batch processing returns exactly one result per input record. |
| 29 | `test_batch_unknown_asset_correctly_rejected` | Mixed batches isolate rejected rows while allowing good rows to complete. |
| 30 | `test_from_data_files_loads_correctly` | The standard factory loads configuration and master data into a working agent. |

### Utility-hardening tests (31-47)

| # | Test case | Expected behavior |
|---:|---|---|
| 31 | `test_valid_timestamp_is_normalized_for_audit` | A valid timestamp passes freshness validation and produces record-age audit data. |
| 32 | `test_invalid_timestamp_is_flagged` | Invalid ISO-8601 syntax is flagged and routed to `data_review`. |
| 33 | `test_timezone_is_required` | A timestamp without timezone information is flagged. |
| 34 | `test_stale_live_record_routes_to_review` | A live event beyond the configured age limit is flagged and withheld from Monitoring. |
| 35 | `test_fresh_live_record_routes_to_monitoring` | A current live event stays `VALID`, records its age, and routes to Monitoring. |
| 36 | `test_historical_record_does_not_fail_age_limit` | Historical/historian data is syntax-checked without failing the live-age rule. |
| 37 | `test_future_record_is_flagged` | A timestamp beyond the future tolerance is flagged. |
| 38 | `test_field_alias_is_normalized_and_audited` | A configured field alias is converted to the canonical name and recorded in the audit list. |
| 39 | `test_temperature_fahrenheit_is_converted` | Fahrenheit input is converted correctly to Celsius and audited. |
| 40 | `test_speed_rps_is_converted` | Revolutions per second are converted correctly to RPM. |
| 41 | `test_canonical_field_wins_over_alias` | Canonical data is never overwritten when both canonical and alias fields are supplied. |
| 42 | `test_input_dictionary_is_not_mutated` | Normalization does not modify the caller's original dictionary. |
| 43 | `test_unknown_fields_are_reported` | Unrecognized source fields are surfaced as schema-drift audit information. |
| 44 | `test_bearing_must_belong_to_asset` | A bearing mapped to a different asset is rejected. |
| 45 | `test_valid_record_has_explicit_monitoring_route` | A valid record explicitly reports eligibility and the `monitoring` route. |
| 46 | `test_rejected_record_has_stop_route` | A rejected record explicitly reports the `stop` route. |
| 47 | `test_new_audit_fields_are_serialized` | All normalization, freshness, and routing fields appear in serialized output. |

### Completion tests (48-60)

| # | Test case | Expected behavior |
|---:|---|---|
| 48 | `test_provenance_versions_are_populated` | Schema, configuration, and master-data version identifiers are populated. |
| 49 | `test_provenance_versions_are_stable` | Identical configuration and masters produce stable version identifiers across agent instances. |
| 50 | `test_master_data_change_changes_version` | A master-data change produces a new master-data version identifier. |
| 51 | `test_opcua_source_profile_normalizes_fields` | OPC-UA field names normalize to the canonical telemetry contract. |
| 52 | `test_event_hub_source_profile_normalizes_identity` | Event Hub identity and timestamp aliases normalize correctly. |
| 53 | `test_process_and_store_requires_repository` | Durable ingestion fails clearly when no repository was configured. |
| 54 | `test_process_and_store_persists_and_can_retrieve` | A processed result is stored and can be retrieved intact from SQLite. |
| 55 | `test_duplicate_telemetry_id_is_not_stored_twice` | A repeated telemetry ID is detected, stopped, and not inserted twice. |
| 56 | `test_out_of_order_event_is_stored_but_routed_to_review` | A late unique event is retained for audit but flagged and routed to review. |
| 57 | `test_in_order_events_remain_eligible` | Sequential unique events remain eligible for Monitoring. |
| 58 | `test_rejected_record_is_persisted_for_audit` | Rejected ingestion decisions are persisted for traceability. |
| 59 | `test_persistence_failure_routes_to_review` | A repository error is visible, blocks downstream processing, and routes to review. |
| 60 | `test_process_and_store_batch_persists_all_unique_records` | Durable batch ingestion stores every unique input and returns one result per input. |

### Coverage summary

| Capability | Tests |
|---|---:|
| Core contract, serialization, and factory | 6 |
| Mapping and context enrichment | 12 |
| Quality, malformed input, and batch behavior | 12 |
| Timestamp and freshness | 7 |
| Normalization and schema drift | 6 |
| Explicit routing and audit serialization | 4 |
| Provenance and source profiles | 5 |
| Persistence, idempotency, and event ordering | 8 |
| **Total** | **60** |

Run only the Agent 1 catalogue with:

```powershell
python -m pytest tests/test_data_foundation.py -v
```

Expected result:

```text
60 passed
```

<!-- *********************** -->
