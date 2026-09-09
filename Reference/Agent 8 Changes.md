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

## 2026-08-26 - Rejection learning and asset-specific chat retrieval

### Defect found

Recommendation rejections were being written to `data/rejection_feedback.json`,
but Agent 8 history queries read only validated closed outcomes from
`data/learned_cases.json`. The phrase `What do you learnt about M-104?` was not
recognized as a learning-history intent, and history retrieval was not filtered
by asset. As a result, the stored rejection could not be returned through chat.

### Corrected behavior

- Agent 8 exposes completed maintenance closures and rejected recommendation
  closures through separate, asset-filtered read paths.
- A rejection is a valid Agent 8 closure and learning outcome. It records the
  closed recommendation decision immediately; any later physical maintenance
  result remains a separate outcome that can also be learned.
- Display identifiers such as `M-104` are normalized to their canonical asset
  identifier before rejection feedback is stored.
- Learning questions route only to Agent 8. They do not load a telemetry
  scenario, call Agents 1-7, or get intercepted as conversational LLM output.
- Common `learned` and `learnt` question variants are recognized.
- Replaying the same rejection case is idempotent and cannot falsely trigger
  the consecutive-rejection rule.
- Two distinct consecutive rejections for the same asset and fault within the
  configured window trigger Reliability Engineering review.
- An explicit asset switch clears cached asset-bound analysis context.

### Configuration

`config/learning_config.json` controls:

```json
{
  "rejection_repository": {"json_path": "data/rejection_feedback.json"},
  "rejection_learning": {
    "include_in_history": true,
    "consecutive_window_days": 30,
    "reliability_review_threshold": 2
  }
}
```

### Chat example

After rejecting a recommendation for M-104, this question:

```text
What did you learn about M-104?
```

returns the matching Agent 8 rejection closure, reason code, operator comment,
persona and case reference. The response identifies it as a valid closed
decision and distinguishes any later physical maintenance result.

### Tests

The direct Agent 8 suite now contains 57 tests. Additional API/chat and Group A
tests cover rejection-to-chat round trips, exact asset isolation, intent
variants, LLM interception prevention, repeated-rejection escalation, approval
sequence breaks, 30-day boundaries, malformed legacy records and API contract
validation.

Current focused regression result:

```text
95 passed
```

Full repository regression result:

```text
1029 passed, 1 deprecation warning
```

## 2026-09-09 - Work-order rejection feedback

### Change

Rejection from the Work Orders view now follows the same Agent 8 learning
contract as rejection from the HITL card. Executor-created work orders retain
their recommendation case, fault mode and bearing identity. A first transition
to `Rejected` records a typed reason, operator context, persona and timestamp
through `LearningMemoryAgent.record_rejection()` and stores the result with the
work order. Repeating the same status update is idempotent.

The Work Orders UI now asks for one of the five configured reason codes and
optional free text (required for `Other`). The persisted Agent 8 outcome is
shown in the work-order detail, including when a Reliability Engineer review is
required after consecutive rejections.

If Agent 8 persistence is unavailable, the work-order rejection is still
retained and the response explicitly reports `persistence_failed`; a decision
is never silently lost.

### Tests

`tests/test_work_order_agent8_learning.py` covers successful recording,
idempotent repeated rejection, legacy work orders without recommendation
metadata and learning-store failure handling. Frontend contract assertions
cover the reason form and learning result display.
