<!-- ************** Added by Prateek Mittal on 20th July 2026 ****************** -->
<!-- Running change record for Agent 5: Knowledge Agent. -->

# Agent 5 Changes

This is the permanent implementation, rationale, contract, and test record for
Agent 5, the Knowledge Agent.

## 2026-07-20 - Baseline audit

### Agent responsibility

Agent 5 answers:

> Which approved or curated SOP sections, inspection steps, and safety notes are
> relevant to this diagnosed fault, and what source supports every instruction?

Baseline contract:

```text
FaultDiagnosis + TrustedBearingSignal
    -> KnowledgeAgent.process()
    -> KnowledgeGuidance
```

The completed contract additionally accepts the linked `RiskAssessment` so the
orchestrator can validate the Agent 4 handoff and include risk context in the
retrieval query.

### Guiding-document requirements

The roadmap requires:

- Synthetic SOP documents for each supported fault mode.
- Chunked indexed retrieval behind a replaceable vector-search boundary.
- Queries using fault mode, asset type, bearing type, and severity.
- Relevant SOP sections, inspection steps, and safety notes.
- At least 10 synthetic retrieval scenarios.
- Source document title in every result.
- No instruction without source support.
- LLM synthesis only later, after retrieval quality is acceptable.

### Existing implementation

The repository already contained:

- An in-memory TF-IDF/cosine retriever.
- Real TXT/PDF document loading plus a synthetic SOP fallback catalog.
- Metadata boosting for fault, asset type, and stage.
- Query construction and extraction of steps, safety notes, and LOTO IDs.
- Graceful empty output when retrieval fails.

These deterministic utilities are appropriate under Agent 5; separate
autonomous utility agents are unnecessary.

### Baseline test state

`tests/test_knowledge_agent.py` contained only a placeholder:

```text
1 skipped
```

No executable acceptance criterion was certified.

### Completion gaps

1. Instructions could be extracted from hits without a source document.
2. Guidance items had no per-item citations.
3. Retrieval failure and genuine no-match outcomes were indistinguishable.
4. Undetermined faults could retrieve unrelated generic repair content.
5. No defensive Agent 1/3/4 identity or eligibility checks existed.
6. Retrieval thresholds, boosts, and output limits were hardcoded.
7. No knowledge-config or effective-index provenance existed.
8. Retriever hits were not validated for malformed types, missing text, or
   mismatched fault metadata.
9. Output lacked explicit status, eligibility, query, hit count, and structured
   source details.
10. The orchestrator did not pass Agent 4 risk into Agent 5.
11. No unit, edge, integration, or complete-chain tests existed.

### Design decisions

- Local TF-IDF remains the deterministic development adapter; Azure AI Search
  can replace it through the same callable boundary.
- Every returned step and safety note must carry its source document.
- `undetermined` returns `no_guidance`, not a guessed SOP.
- Retrieval exceptions return `retrieval_failed`; empty/relevance-filtered
  results return `no_guidance`; invalid handoffs return `invalid_input`.
- LLM synthesis is not added, exactly following the roadmap sequencing.
- Agent 5 persistence remains part of the orchestrator's complete case record.

## 2026-07-20 - Agent 5 completion pass

### Configurable deterministic retrieval

Added `config/knowledge_config.json` for:

- Top-k retrieval count and minimum score.
- Fault, asset, and stage metadata boosts.
- Mandatory source policy and generic-document policy.
- Relevant-section, inspection-step, safety-note, and preview limits.
- Retrieval logging.

The local development adapter remains TF-IDF/cosine similarity. The callable
retriever boundary can be replaced by Azure AI Search without changing Agent 5
output or grounding logic.

### Grounding contract

Agent 5 now returns structured `GroundedGuidanceItem` objects:

```text
text
source_document
item_type = inspection_step | safety_note
retrieval_score
```

Every item in the compatibility `inspection_steps` and `safety_notes` lists has
a matching grounded item and source. Hits without a source or text are ignored.
Wrong-fault metadata, malformed hits, non-finite scores, and below-threshold
scores cannot produce instructions.

`SourceDocument` provides source title, score, fault mode, asset type, and stage.
This directly satisfies the roadmap requirements for source document titles and
no unsupported instructions.

### Guidance outcomes

| Status | Meaning | Guidance eligible |
|---|---|---|
| `grounded` | Relevant source-backed SOP content retrieved | Yes |
| `no_guidance` | No classified fault or no source passed policy | No; use HITL/manual retrieval |
| `retrieval_failed` | Retriever/index adapter raised an error | No; operational recovery required |
| `invalid_input` | Agent 1/3/4 handoff is inconsistent | No |

A valid Agent 3 `undetermined` result intentionally returns `no_guidance`
without querying. Generic repair content must not be presented as though it
matched an unknown fault.

### Defensive handoff

Agent 5 validates:

- Diagnosis and trusted-signal types.
- Agent 3 status and eligibility.
- Agent 1 downstream eligibility.
- Diagnosis/trusted asset and bearing identity.
- Optional Agent 4 `RiskAssessment` type, eligibility, case, asset, and bearing.

Agent 4 risk is optional for compatibility, but the orchestrator now supplies
it. Risk level is included in the retrieval query alongside fault mode, asset
type, bearing type, stage, and severity.

### Knowledge corpus corrections

- Added a Stage-3 inner-race motor SOP because the real pipeline scenario had no
  relevant motor document.
- Corrected the BSF/gearbox SOP and case study from gear-tooth overhaul to the
  roadmap-aligned rolling-element bearing inspection/replacement.
- Extended LOTO extraction to recognize the corpus's `EL-*` and `IL-*` permit
  identifiers as well as `LOTO_*` identifiers.

All four diagnosed pipeline fault families now retrieve relevant guidance:

```text
outer_race_fault
inner_race_fault
lubrication_issue
rolling_element_fault
```

Cage-fault guidance remains available and is tested at the retriever boundary;
the current telemetry scenarios do not yet contain a full cage-fault stream.

### Retrieval/index provenance

Every output includes:

```text
schema_version = 1.1
knowledge_config_version
knowledge_index_version
source_diagnosis_schema_version
source_fi_config_version
source_taxonomy_version
source_risk_schema_version
source_risk_config_version
source_monitoring_config_version
source_data_config_version
source_master_data_version
linked_risk_case_id
```

The effective real+synthetic chunk collection receives a stable SHA-256-derived
index identifier. Changing document text or metadata changes that version.

### LLM decision

No LLM synthesis was added. The roadmap explicitly schedules synthesis only
after retrieval quality is acceptable. Agent 5 currently returns exact
source-backed sections and extracted steps without generating new instructions.

### Persistence decision

Agent 5 does not create another database. Knowledge guidance belongs to the
orchestrator's complete case record with anomaly, diagnosis, and risk.

## Expected Input

Preferred orchestrated call:

```python
guidance = knowledge_agent.process(
    fault_diagnosis,
    trusted_bearing_signal,
    risk_assessment,
)
```

Compatibility call without risk remains supported:

```python
guidance = knowledge_agent.process(fault_diagnosis, trusted_bearing_signal)
```

## Expected Output

### Grounded guidance

```json
{
  "case_id": "ANOM-BRG_001-2026-05-20T12:00:00Z",
  "fault_mode": "outer_race_fault",
  "asset_type": "motor",
  "bearing_type": "SKF6310",
  "guidance_status": "grounded",
  "guidance_eligible": true,
  "source_documents": ["SOP_001_outer_race_motor_stage3.pdf"],
  "inspection_steps": ["Obtain LOTO EL-104-A permit..."],
  "safety_notes": ["Mandatory PPE..."],
  "grounded_items": [
    {
      "text": "Obtain LOTO EL-104-A permit...",
      "source_document": "SOP_001_outer_race_motor_stage3.pdf",
      "item_type": "inspection_step",
      "retrieval_score": 0.91
    }
  ],
  "source_details": [
    {
      "title": "SOP_001_outer_race_motor_stage3.pdf",
      "retrieval_score": 0.91,
      "fault_mode": "outer_race_fault",
      "asset_type": "motor",
      "iso_stage": 3
    }
  ],
  "knowledge_config_version": "16-character-version",
  "knowledge_index_version": "16-character-version"
}
```

### No matching guidance

```json
{
  "fault_mode": "undetermined",
  "guidance_status": "no_guidance",
  "guidance_eligible": false,
  "source_documents": [],
  "inspection_steps": [],
  "status_reason": "no classified fault mode is available for SOP retrieval"
}
```

### Retrieval failure

```json
{
  "guidance_status": "retrieval_failed",
  "guidance_eligible": false,
  "status_reason": "retriever failed: index unavailable"
}
```

## Complete test catalogue summary

| Suite | Cases | Coverage |
|---|---:|---|
| `test_knowledge_agent.py` | 51 | Four faults, grounding, sources, 10 retrieval scenarios, malformed adapters, config, extraction, provenance |
| `test_agent4_agent5_integration.py` | 9 | Assessed/monitor/invalid risks, risk query, provenance, immutability |
| `test_agent1_agent2_agent3_agent4_agent5_integration.py` | 41 | Complete fault, terminal, malformed, normalization, persistence/order/cooldown, handoff tampering, retrieval failure, provenance, JSON, batch, citation paths |
| **Agent 5 total** | **79** | |

```text
Agent 5 certification: 101 passed
All Agent 1-5 integration suites: 139 passed
Full repository: 485 passed, 1 skipped, 0 failed
```

Run Agent 5 certification:

```powershell
python -m pytest tests/test_knowledge_agent.py tests/test_agent4_agent5_integration.py tests/test_agent1_agent2_agent3_agent4_agent5_integration.py -q
```

## Completion boundary

Agent 5 is complete for the deterministic development/pilot scope:

- Replaceable local/cloud retrieval boundary.
- Configurable query/ranking/output policy.
- Relevant guidance for every currently diagnosed pipeline fault.
- Ten certified synthetic retrieval scenarios.
- Mandatory document titles and per-instruction citations.
- Explicit no-guidance, retrieval-failure, and invalid-input outcomes.
- Agent 1-4 handoff validation and full provenance.
- Corrected fault-aligned synthetic corpus.
- No premature generative synthesis.
- Comprehensive unit, edge, boundary, and complete-chain tests.

Real Azure Blob/AI Search deployment requires an adapter and approved indexed
documents; it does not require changing Agent 5's grounding/output contract.

<!-- *********************** -->
