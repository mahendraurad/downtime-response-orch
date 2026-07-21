# Downtime Response Orchestrator (DRO)

Multi-agent bearing predictive maintenance system.
Converts raw historian telemetry into explainable maintenance recommendations.

## Current status

Phases 2–4 complete — Data Foundation Agent (+ remediation/curated store),
Monitoring Agent, and Failure Intelligence Agent are built and tested
(88 tests passing, 3 stub phases skipped).
Phases 5–10 are scaffolded with typed stubs ready to implement.

## Project structure

```
dro/
├── data/                          # Synthetic baseline data (Tables 2–8)
│   ├── asset_master.json          # Table 3 — 6 assets
│   ├── bearing_master.json        # Table 2 — 12 bearings with baselines
│   ├── fault_taxonomy.json        # Table 4 — 6 fault rules
│   ├── telemetry_scenarios.json   # Table 8 — all 7 demo scenarios
│   └── sops/                      # Place SOP + case PDFs here (Phase 6)
├── src/
│   ├── schemas/                   # Pydantic-style dataclasses (agent contracts)
│   │   ├── bearing_signal.py      # ✅ BearingSignalFact, TrustedBearingSignal
│   │   ├── asset.py               # ✅ AssetMaster, BearingMaster
│   │   ├── anomaly.py             # ✅ Phase 3
│   │   ├── diagnosis.py           # ✅ Phase 4
│   │   ├── risk.py                # 🔲 Phase 5
│   │   ├── knowledge.py           # 🔲 Phase 6
│   │   ├── recommendation.py      # 🔲 Phase 7
│   │   ├── execution.py           # 🔲 Phase 9
│   │   └── feedback.py            # 🔲 Phase 10
│   ├── agents/
│   │   ├── data_foundation_agent.py      # ✅ COMPLETE — 30 tests passing
│   │   ├── monitoring_agent.py           # ✅ Phase 3
│   │   ├── failure_intelligence_agent.py # ✅ Phase 4
│   │   ├── predictive_risk_agent.py      # 🔲 Phase 5
│   │   ├── knowledge_agent.py            # 🔲 Phase 6
│   │   ├── prescriptive_optimization_agent.py # 🔲 Phase 7
│   │   ├── executor_agent.py             # 🔲 Phase 9
│   │   └── learning_memory_agent.py      # 🔲 Phase 10
│   ├── tools/
│   │   ├── data_loader.py         # ✅ loads JSON → typed dicts (swap for DB later)
│   │   ├── validators.py          # ✅ all field-level validation rules
│   │   ├── enrichment.py          # ✅ asset + bearing context joining
│   │   ├── baseline_features.py   # ✅ Phase 3 — z-score, rolling trend
│   │   ├── fault_matcher.py       # ✅ Phase 4 — BPFO/BPFI threshold matching
│   │   ├── rul_calculator.py      # 🔲 Phase 5 — RUL band logic
│   │   ├── retriever.py           # 🔲 Phase 6 — FAISS/Azure AI Search wrapper
│   │   ├── cmms_mock_service.py   # 🔲 Phase 9 — mock work order creation
│   │   └── inventory_mock_service.py # 🔲 Phase 9 — mock part reservation
│   └── orchestrator/
│       ├── state.py               # 🔲 Phase 8 — LangGraph TypedDict state
│       ├── routing.py             # 🔲 Phase 8 — conditional edge functions
│       └── graph.py               # 🔲 Phase 8 — StateGraph wiring
├── tests/
│   ├── test_data_foundation.py    # ✅ 30 tests, all passing
│   ├── test_monitoring.py         # ✅ Phase 3
│   ├── test_failure_intelligence.py # ✅ Phase 4
│   ├── test_predictive_risk.py    # 🔲 Phase 5
│   ├── test_knowledge_agent.py    # 🔲 Phase 6
│   └── test_end_to_end_graph.py   # 🔲 Phase 8
├── models/                        # ML model artifacts (Phase 8+, git-ignored)
├── .env.example                   # Copy to .env and fill in credentials
├── .gitignore
├── pyproject.toml                 # pytest config — testpaths and pythonpath
└── requirements.txt               # Phased install guide
```

## Quickstart

```bash
# 1. Clone / open in VS Code
cd dro

# 2. Run the Data Foundation Agent tests (no install needed — stdlib only)
python -m unittest tests.test_data_foundation -v

# 3. Copy .env template
cp .env.example .env
```

## Running tests

```bash
# All tests (stubs for future phases will be skipped automatically)
python -m unittest discover tests/ -v

# Data Foundation only
python -m unittest tests.test_data_foundation -v
```

## Swapping JSON for a real database

Only `src/tools/data_loader.py` changes. Every other file stays the same.
See the three `load_*()` functions — replace `_load_json()` calls with DB queries.
The agent receives already-built dicts and never touches the loader directly.

## Build order

| Phase | Agent | Status |
|-------|-------|--------|
| 2 | Data Foundation Agent | ✅ Complete |
| 3 | Monitoring Agent | ✅ Complete |
| 4 | Failure Intelligence Agent | ✅ Complete |
| 5 | Predictive Risk Agent | 🔲 Next |
| 6 | Knowledge Agent (RAG) | 🔲 |
| 7 | Prescriptive Optimization Agent | 🔲 |
| 8 | LangGraph Orchestrator | 🔲 |
| 9 | Executor Agent | 🔲 |
| 10 | Learning & Memory Agent | 🔲 |
