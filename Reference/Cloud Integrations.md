# Cloud Integration Guide

The backend supports local deterministic development and opt-in production
adapters. Cloud integrations are selected by environment variables; secrets
must be stored in Azure Key Vault or the deployment platform, never committed.

## PostgreSQL

PostgreSQL has two independent responsibilities:

1. `HITL_REPOSITORY=postgres` stores pending human approvals and immutable
   decisions using `migrations/001_hitl_postgres.sql`.
2. `CHECKPOINT_BACKEND=postgres` stores LangGraph workflow checkpoints. Set
   `POSTGRES_CHECKPOINT_SETUP=true` only for the first controlled startup so
   LangGraph can create/migrate its tables; set it back to `false` afterwards.

Both use `POSTGRES_URL`. Azure Database for PostgreSQL should require TLS, use a
least-privilege application identity, and have backup/retention configured.

The application loads `.env` and then ignored `.env.local` for local use;
process/container settings remain authoritative. Both PostgreSQL responsibilities
use bounded pools configured by `POSTGRES_POOL_MIN_SIZE` and
`POSTGRES_POOL_MAX_SIZE`, and FastAPI closes both pools during shutdown.

Pipeline callers may provide a stable `thread_id`. Each physical checkpoint key
combines that logical thread with the unique run namespace, preventing one
conversation's runs from overwriting each other. Pending Agent 6 recommendations
carry both coordinates. An approved recommendation resumes at Agent 7 without
replaying upstream analysis, while repeated approval returns the stored result.

Run the safe live certification after configuration:

```powershell
python scripts\verify_postgres_langgraph.py
```

It verifies connectivity, checkpoint readback/resume/idempotency and atomic HITL
claim/decision readback, then removes its uniquely named verification rows. The
health endpoint reports only backend type and status; it never returns a URL or
credential.

Completion certification on 2026-08-05:

```text
Live Azure PostgreSQL/LangGraph checks: 11 passed
Focused persistence tests: 15 passed
Full repository regression: 833 passed
```

### LangChain boundary

LangGraph is the workflow runtime and uses LangChain's runnable/tracing
foundation transitively. The eight domain agents intentionally remain typed,
deterministic Python services; they are not wrapped in cosmetic chains. Azure
LLM calls remain behind the defensive client, and LangSmith/Langfuse observe
the API, routing, graph nodes, retrieval and LLM spans. This preserves existing
contracts while using LangGraph where durable state and conditional execution
are materially required.

## Azure AI Search RAG

Set `RAG_BACKEND=azure` and configure:

- `AZURE_SEARCH_ENDPOINT`
- `AZURE_SEARCH_KEY`
- `AZURE_SEARCH_INDEX_NAME`
- `AZURE_SEARCH_CHUNK_ID_FIELD`
- `AZURE_SEARCH_SOURCE_URI_FIELD`
- The content, citation/source, fault mode, asset type, and ISO stage field
  names shown in `.env.example`.

For an RBAC-only Search service, set `AZURE_SEARCH_AUTH=rbac` and omit the
Search key. `DefaultAzureCredential` uses the developer's Azure CLI identity
locally and the workload/managed identity after deployment. The identity needs
the `Search Index Data Reader` role to query documents.

Metadata filter fields are optional. For the minimal Azure index schema
`chunk_id`, `parent_id`, `chunk`, `title`, and `text_vector`, use `chunk` as
content, `title` as source, and `text_vector` as the vector field, with the
fault/asset/stage field settings set to `none`.

When `AZURE_SEARCH_VECTOR_FIELD` is empty, Agent 5 uses text search with
metadata filters. When it is populated, the adapter issues a hybrid text and
integrated-vectorization query. The Azure index must have a vectorizer attached
to that field.

Every accepted result must contain both source and content. Missing citations
are discarded, and Agent 5 returns `no_guidance` if no source-backed result
survives its relevance policy.

Conceptual and approved open-ended chat questions use the same retrieval
backend through the governed RAG service. The chat response exposes document
title, chunk ID, source URI/parent locator, normalized score, and inline
citation markers. Agent 8 learned cases are excluded from this general source
type and retain their separate case-reference contract.

Use `RAG_BACKEND=auto` only for development, where falling back to the local SOP
index is acceptable. Production should use the explicit `azure` value so
configuration problems are visible.

## LangSmith and Langfuse

LangSmith remains the primary agent/orchestrator trace. It now includes nested
retrieval and Azure LLM spans in addition to the API, routing, agents, executor,
and Reflexion spans.

Langfuse is an independent optional export. Configure
`LANGFUSE_TRACING=true`, public and secret keys, base URL, and environment.
When disabled or unavailable it is a no-op and cannot break the pipeline.

Telemetry can contain prompts, retrieved SOP passages, asset identifiers, and
agent outputs. Enable it only in approved projects with suitable retention and
access controls.

## Deployment validation

Run deterministic tests first:

```powershell
python -m pytest -q
```

Then validate each cloud integration in a non-production environment:

1. Apply the HITL migration and initialize LangGraph checkpoints once.
2. Submit a known SOP question and confirm cited Azure Search documents.
3. Trigger a full anomaly flow and confirm nested spans in both trace systems.
4. Trigger an HITL gate, restart the API, and resolve the same pending run.
5. Disable each cloud service in turn and confirm a controlled error or safe
   no-guidance response rather than fabricated output.
