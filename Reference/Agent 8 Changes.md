<!-- ************** Added by Prateek Mittal on 20th July 2026 ****************** -->
# Agent 8 Changes — Learning & Memory Agent

## Objective and roadmap alignment

Agent 8 closes the loop after work-order completion. It validates technician feedback, stores the confirmed outcome, creates a searchable learned-case document and exports a labeled training row, following roadmap Phase 10.

Confirmed technician fields remain authoritative. An optional LLM may rewrite narrative prose only when it preserves the confirmed fault and action.

## Implementation

- Replaced the placeholder `LearningMemoryAgent`.
- Added validated `config/learning_config.json`.
- Added completion-status, identity, required-field, root-cause, timestamp and physical-value validation.
- Added duplicate learned-case protection.
- Added `JSONLearnedCaseRepository` with persistence, lookup and lexical search.
- Added JSONL labeled-data export for future model development.
- Added explicit learned/duplicate/invalid/persistence-failed outcomes.
- Added execution and configuration provenance.
- Added optional fail-safe LLM narrative rewriting with label-preservation checks.
- Added searchable hit format compatible with an injected Knowledge Agent retriever.

## Expected input

```python
document = agent.process(
    execution: ExecutionResult,  # status success or partial
    feedback: FeedbackEvent,     # matching case and confirmed closure
)
```

Required feedback includes case/asset/bearing identity, confirmed fault, root cause, action, timezone-aware closure timestamp and physically valid post-repair readings.

## Expected output

`LearnedCaseDocument` contains narrative, confirmed labels, outcome, tags, measurements, status, persistence/index status and provenance.

| Status | Meaning |
|---|---|
| `learned` | Persisted, indexed and exported successfully |
| `duplicate` | Case was already learned |
| `invalid_input` | Execution or feedback cannot safely train memory |
| `persistence_failed` | Memory/index/export operation failed visibly |

## Test catalogue

`tests/test_learning_memory.py` contains 45 tests covering allowed/blocked execution outcomes, malformed contracts, identity and required fields, root cause, timestamps, physical values, duplicates, persistence/search, training export, serialization, provenance, LLM label preservation/fallback/timeout/no-call behavior and repository/config failures.

```text
Agent 8 suite: 45 passed
```

## Completion boundary

Agent 8 is complete for local/pilot closed-loop learning. Azure Blob Storage and Azure AI Search replace the repository adapter; governed model retraining remains a separate offline approval process.
## 2026-07-22 - Read-side learned-case history

Agent 8 now exposes `recent_cases(limit)` through its repository boundary.
Results are defensive copies ordered by `created_at`. Chat can therefore query
confirmed closed-case learning without treating a read as a new learning event.

Tests cover immediate retrieval after learning, newest-three ordering, fewer
than three available cases, invalid limits, malformed stored rows, and honest
empty-history behavior.

<!-- *********************** -->
