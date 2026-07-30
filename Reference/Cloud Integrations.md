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

## Azure AI Search RAG

Set `RAG_BACKEND=azure` and configure:

- `AZURE_SEARCH_ENDPOINT`
- `AZURE_SEARCH_KEY`
- `AZURE_SEARCH_INDEX_NAME`
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
