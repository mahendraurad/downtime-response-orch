# Open-ended RAG Changes

## Objective

Complete the remaining RAG scope without changing Agent 5's completed
diagnostic SOP flow. Conceptual and approved open-ended maintenance questions
now use the same configurable local/Azure retrieval boundary instead of an
uncited glossary or direct general-knowledge LLM response.

## Decision and rationale

Agent 5 requires a validated diagnosis and risk handoff, so it must not be
called with a context-free definition question. A small governed RAG service
therefore owns open-ended retrieval while reusing `build_retrieval_backend()`.
This preserves the Agent 5 contract and prevents general knowledge from being
mistaken for an asset-specific result.

## Flow

```text
/api/chat
  -> intent and asset/evidence checks
  -> governed open-knowledge RAG
  -> configured local or Azure AI Search retriever
  -> relevance, source, injection, and learned-case filtering
  -> evidence-only extractive or optional LLM synthesis
  -> inline markers plus structured citations
  -> bounded Reflexion
```

Asset/fleet diagnosis, RUL, risk, cost, and action questions remain behind the
existing telemetry and fleet-evidence gates.

## Response contract

A grounded chat response includes:

```json
{
  "source_type": "approved_rag",
  "call_plan": ["knowledge_rag"],
  "citations": [{
    "citation_id": "1",
    "title": "DRO_Approved_Reliability_Glossary.txt",
    "chunk_id": "stable-chunk-id",
    "source_uri": "data/sops/DRO_Approved_Reliability_Glossary.txt",
    "retrieval_score": 0.82,
    "source_type": "approved_document"
  }],
  "rag": {
    "status": "grounded",
    "retrieval_hit_count": 1,
    "index_version": "stable-index-version",
    "llm_used": false
  }
}
```

Safe non-grounded outcomes are `rag_no_match`, `rag_error`, and `rag_blocked`.
They return no citations and explicitly state that no uncited answer was
generated.

## Grounding and safety controls

- Sources and passages are mandatory; malformed and non-finite-score hits are
  discarded.
- Returned citation markers are validated against retrieved passages.
- LLM output with missing or invented citation numbers is rejected in favour
  of deterministic extractive output.
- Prompt-injection patterns are blocked in user queries and discarded from
  retrieved passages.
- Agent 8 learned-case documents are excluded from the general-document source
  type and remain available only through Agent 8's case-reference flow.
- Search stop words are removed before retrieval to prevent unrelated matches
  driven by terms such as "what" and "is".
- Timeout, access-denied, empty-index, and adapter errors fail closed without
  leaking exception or credential details.

## Configuration

`config/knowledge_config.json` now contains the `open_ended` block controlling
top-k retrieval, minimum score, passage count, answer length, and optional LLM
synthesis. Azure citation metadata mappings are configured with:

```text
AZURE_SEARCH_CHUNK_ID_FIELD
AZURE_SEARCH_SOURCE_URI_FIELD
```

## Approved local knowledge

`data/sops/DRO_Approved_Reliability_Glossary.txt` provides version-controlled
definitions for RUL, condition monitoring, anomaly interpretation, vibration,
temperature, BPFO/BPFI, common bearing-failure causes, ISO 10816-3, and general
reliability improvement. It explicitly separates education from asset or fleet
decisions.

## Tests

`tests/test_governed_rag.py` covers grounded answers, citation metadata,
relevance, unrelated questions, deduplication, Agent 8 separation, prompt
injection, malicious retrieved text, invalid LLM citations, deterministic
fallback, empty index, timeout, access denial, Chat API trace output, LLM intent
classification, and the asset-evidence gate.

Related Agent 5, cloud-adapter, chat, and routing tests were updated for the new
contract.

Verification on 2026-08-04:

```text
Focused RAG/Agent 5/chat/cloud suites: 151 passed
Full repository regression: 812 passed, 1 non-functional dependency warning
```

Live validation against the deployed Azure AI Search index remains an
environment/deployment check; deterministic CI does not make paid cloud calls.
