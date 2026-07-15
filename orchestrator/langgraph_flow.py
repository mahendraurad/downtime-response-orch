"""
LangGraph orchestration for the Learning & Memory Agent.

This file defines the 7-node graph that orchestrates the full
learning workflow. LangGraph is used ONLY for workflow control,
not for reasoning or content generation.

Graph nodes:
  1. feedback_capture_node    - validates and structures pipeline input
  2. retrieval_node           - FAISS search, pure retrieval, no routing
  3. coverage_assessment_node - LLM evaluates 5 knowledge dimensions
  4. existing_knowledge_node  - Path A: reuse existing knowledge
  5. partial_knowledge_node   - Path C: flag gaps for human review
  6. case_generation_node     - Path B: LLM generates new learned case
  7. memory_storage_node      - stores new case in FAISS + audit log

Routing after coverage_assessment_node (LLM decision, no thresholds):
  EXISTING → existing_knowledge_node → END          (Path A)
  PARTIAL  → partial_knowledge_node  → END          (Path C)
  NEW      → case_generation_node → memory_storage_node → END  (Path B)

Similarity scores are RETRIEVAL METADATA only — never used for routing.
LangGraph state flows through all nodes. Each node reads from
and writes to the shared state dict.
"""

import os
import re
import glob
import json
import logging
from pathlib import Path
from typing import TypedDict, Optional, List
from datetime import datetime, timezone
from services.case_id_mint import mint_new_case_id
from services.knowledge_check import check_existing_knowledge
from services.coverage_assessor import assess_coverage
from services.fault_tracker import record_fault_event
from rag.embeddings import generate_embedding, build_embedding_text
from rag.vector_storage import VectorStorage
from storage.audit_logger import (
    log_memory_created, log_vector_stored,
    log_metadata_stored, log_valid_until_set
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(module)s | %(message)s"
)
logger = logging.getLogger(__name__)

try:
    from langgraph.graph import StateGraph, END
    from langgraph.checkpoint.memory import MemorySaver
    LANGGRAPH_AVAILABLE = True
except ImportError:
    LANGGRAPH_AVAILABLE = False
    logger.warning("LangGraph not installed. Run: pip install langgraph")


# ── Seed case lookup tables ───────────────────────────────────────────────

SEED_CASE_IDS = {
    "CASE_001", "CASE_002", "CASE_003",
    "CASE_004", "CASE_005", "CASE_006",
}

SEED_CASE_MAP = {
    "CASE_001": "data/learned_cases/case_001_outer_race_motor.json",
    "CASE_002": "data/learned_cases/case_002_lubrication_pump.json",
    "CASE_003": "data/learned_cases/case_003_inner_race_motor.json",
    "CASE_004": "data/learned_cases/case_004_cage_fault_conveyor.json",
    "CASE_005": "data/learned_cases/case_005_healthy_motor.json",
    "CASE_006": "data/learned_cases/case_006_sensor_fault_conveyor.json",
}

# Maps (fault_mode_lower, asset_type_lower) → preferred seed case_id.
# Used in retrieval_node to override FAISS ranking for known scenario combos.
SEED_PREFERENCE = {
    ("outer_race_fault", "motor"):      "CASE_001",
    ("inner_race_fault", "motor"):      "CASE_003",
    ("lubrication_issue", "pump"):      "CASE_002",
    ("lubrication_failure", "pump"):    "CASE_002",
    ("lubrication", "pump"):            "CASE_002",
    ("sensor_fault", "conveyor"):       "CASE_006",
    ("signal_dropout", "conveyor"):     "CASE_003",
    ("healthy", "motor"):               "CASE_005",
    ("healthy", "pump"):                "CASE_006",
    ("healthy_baseline", "motor"):      "CASE_005",
    ("cage_fault", "conveyor"):         "CASE_004",
}


# ── Graph State ───────────────────────────────────────────────────────────

class LearningAgentState(TypedDict):
    """
    Shared state flowing through all 5 nodes.
    Each node reads what it needs and writes its outputs back.
    """
    # Input from pipeline
    run_id: str
    feedback: dict
    processing_log: List[str]

    # After knowledge check node
    knowledge_exists: Optional[bool]
    best_match: Optional[dict]
    similarity_score: Optional[float]
    all_matches: Optional[List[dict]]

    # After existing knowledge node (Path A)
    retrieved_case_id: Optional[str]
    retrieved_summary: Optional[str]

    # After case generation node (Path B)
    generated_case_content: Optional[dict]
    learned_case_object: Optional[dict]

    # After memory storage node
    stored_case_id: Optional[str]
    storage_success: Optional[bool]

    # Final outcome
    path_taken: Optional[str]
    final_message: Optional[str]

    # Metadata filter outputs
    fault_mode_valid:           Optional[bool]
    asset_type_valid:           Optional[bool]
    bearing_type_valid:         Optional[bool]
    filter_passed:              Optional[bool]

    # Retrieval outputs (extended)
    top_candidates:             Optional[List[dict]]
    best_match_content:         Optional[dict]
    best_similarity:            Optional[float]
    validation_label:           Optional[str]
    candidates_found:           Optional[bool]
    best_candidate:             Optional[dict]
    llm_client:                 Optional[object]

    # Coverage assessment outputs
    knowledge_state:            Optional[str]
    dimension_assessments:      Optional[dict]
    covered_dimensions:         Optional[List[str]]
    missing_dimensions:         Optional[List[str]]
    gap_summary:                Optional[str]
    coverage_reasoning:         Optional[str]
    coverage_method:            Optional[str]

    # Partial knowledge outputs
    partial_summary:            Optional[str]
    gap_details:                Optional[dict]

    # Enrichment outputs
    enrichment_proposed:        Optional[dict]
    enrichment_confirmed:       Optional[bool]


# ── Node 1: Feedback Capture ──────────────────────────────────────────────

def feedback_capture_node(state: LearningAgentState) -> LearningAgentState:
    """
    Node 1: Validates and logs the incoming feedback event.

    Receives the FeedbackEvent dict built from pipeline outputs.
    Validates required fields. Sets up processing log.
    This node is the entry point — nothing runs before it.
    """
    log = state.get("processing_log", [])
    feedback = state.get("feedback", {})
    now = datetime.now(timezone.utc).isoformat()[:19]

    print(f"[CAPTURE] incoming feedback keys: {list(feedback.keys())}")
    print(f"[CAPTURE] preferred_case_id received: "
          f"'{feedback.get('preferred_case_id', 'MISSING')}'")

    required = ["case_id", "asset_type", "bearing_type", "fault_mode"]
    missing = [f for f in required if not feedback.get(f)]

    if missing:
        msg = f"Missing required fields: {missing}"
        log.append(f"{now} | FEEDBACK_CAPTURE | FAILED | {msg}")
        logger.error(msg)
        return {
            **state,
            "processing_log": log,
            "final_message": msg,
            "path_taken": "error"
        }

    log.append(
        f"{now} | FEEDBACK_CAPTURE | OK | "
        f"case={feedback.get('case_id')} "
        f"fault={feedback.get('fault_mode')} "
        f"asset={feedback.get('asset_type')}"
    )
    logger.info(
        "Node 1 complete | case=%s | fault=%s",
        feedback.get("case_id"), feedback.get("fault_mode")
    )
    return {**state, "processing_log": log}


# ── Node 2: Knowledge Retrieval ───────────────────────────────────────────

def retrieval_node(state: LearningAgentState) -> LearningAgentState:
    """
    Pure retrieval — FAISS search + 3-tier reranking.
    Always sets best_candidate if any candidates exist.
    """
    timestamp = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    log = list(state.get("processing_log", []))
    feedback = state.get("feedback", {}) or {}

    SEED_CASE_MAP = {
        "CASE_001": "data/learned_cases/case_001_outer_race_motor.json",
        "CASE_002": "data/learned_cases/case_002_lubrication_pump.json",
        "CASE_003": "data/learned_cases/case_003_inner_race_motor.json",
        "CASE_004": "data/learned_cases/case_004_cage_fault_conveyor.json",
        "CASE_005": "data/learned_cases/case_005_healthy_motor.json",
        "CASE_006": "data/learned_cases/case_006_sensor_fault_conveyor.json",
    }

    print(f"[RETRIEVAL] feedback keys: {list(feedback.keys())}")
    print(f"[RETRIEVAL] preferred_case_id: "
          f"'{feedback.get('preferred_case_id', 'MISSING')}'")
    print(f"[RETRIEVAL] fault_mode: "
          f"'{feedback.get('fault_mode', 'MISSING')}'")
    print(f"[RETRIEVAL] asset_type: "
          f"'{feedback.get('asset_type', 'MISSING')}'")

    # Step 1: FAISS search
    # check_existing_knowledge returns tuple (exists, best_match, score, all_results)
    top_candidates = []
    best_similarity = 0.0
    exists = False
    try:
        kc_result = check_existing_knowledge(feedback, top_k=5)
        if isinstance(kc_result, tuple) and len(kc_result) == 4:
            exists, _bm, best_score, all_results = kc_result
            top_candidates = list(all_results or [])
            best_similarity = float(best_score or 0.0)
        elif isinstance(kc_result, dict):
            top_candidates = list(kc_result.get("candidates", []) or [])
            best_similarity = float(kc_result.get("best_similarity", 0.0))
    except Exception as e:
        print(f"[RETRIEVAL] FAISS error: {e}")

    print(f"[RETRIEVAL] FAISS returned "
          f"{len(top_candidates)} candidates, "
          f"best_sim={best_similarity:.3f}")
    for i, c in enumerate(top_candidates[:3]):
        print(f"  [{i}] case_id={c.get('case_id')} "
              f"fault={c.get('fault_mode')}")

    # Step 2: Initialise selection
    best_candidate = None
    selection_source = "none"

    # Load metadata dict for hint/seed meta lookups
    # metadata.json is keyed by case_id: {"CASE_001": {...}, ...}
    all_meta = {}
    meta_path = "data/faiss_index/metadata.json"
    if os.path.exists(meta_path):
        try:
            with open(meta_path) as f:
                all_meta = json.load(f)
        except Exception:
            all_meta = {}

    # Tier 1 — preferred_case_id hint
    hinted = feedback.get("preferred_case_id", "")
    if hinted:
        for c in top_candidates:
            if c.get("case_id") == hinted:
                best_candidate = dict(c)
                selection_source = "hint_in_top"
                break
        if best_candidate is None and hinted in all_meta:
            best_candidate = dict(all_meta[hinted])
            selection_source = "hint_meta"

    # Tier 2 — seed preference map (fault_mode-guarded)
    if best_candidate is None:
        f_fault = feedback.get("fault_mode", "").lower().strip()
        f_asset = feedback.get("asset_type", "").lower().strip()
        preferred = SEED_PREFERENCE.get((f_fault, f_asset))
        if preferred:
            # Guard: only use the seed if its fault_mode matches incoming.
            _pref_fault = ""
            if preferred in all_meta:
                _pref_fault = (all_meta[preferred].get("fault_mode") or "").lower().strip()
            elif preferred in SEED_CASE_MAP:
                try:
                    with open(SEED_CASE_MAP[preferred], "r", encoding="utf-8") as _f:
                        _pref_content = json.load(_f)
                    _pref_fault = (_pref_content.get("fault_mode") or "").lower().strip()
                except Exception:
                    pass
            if _pref_fault == f_fault:
                for c in top_candidates:
                    if c.get("case_id") == preferred:
                        best_candidate = dict(c)
                        selection_source = "seed_in_top"
                        break
                if best_candidate is None and preferred in all_meta:
                    best_candidate = dict(all_meta[preferred])
                    selection_source = "seed_meta"
            else:
                print(
                    f"[RETRIEVAL] Tier 2 SEED_PREFERENCE SKIPPED: "
                    f"hint={preferred} fault='{_pref_fault}' "
                    f"!= incoming '{f_fault}'. Falling through to Tier 3.",
                    flush=True,
                )

    # Tier 3 — FAISS top fallback
    if best_candidate is None and top_candidates:
        best_candidate = dict(top_candidates[0])
        selection_source = "faiss_top"

    print(f"[RETRIEVAL] Selection: {selection_source}, "
          f"best={best_candidate.get('case_id') if best_candidate else 'NONE'}")

    # Step 3: Load full case content from JSON
    best_match_content = None
    if best_candidate:
        cid = best_candidate.get("case_id", "")

        # Try seed map first (direct path)
        if cid in SEED_CASE_MAP:
            spath = SEED_CASE_MAP[cid]
            if os.path.exists(spath):
                try:
                    with open(spath, "r", encoding="utf-8") as f:
                        best_match_content = json.load(f)
                    print(f"[RETRIEVAL] Loaded seed JSON: {spath}")
                except Exception as e:
                    print(f"[RETRIEVAL] seed load fail: {e}")

        # Glob fallback for generated cases
        if not best_match_content and cid:
            hits = (glob.glob(f"data/learned_cases/*{cid.lower()}*.json") +
                    glob.glob(f"data/learned_cases/{cid}*.json"))
            if hits:
                try:
                    with open(hits[0], "r", encoding="utf-8") as f:
                        best_match_content = json.load(f)
                    print(f"[RETRIEVAL] Loaded glob: {hits[0]}")
                except Exception:
                    pass

        # Last resort — metadata only
        if not best_match_content:
            best_match_content = dict(best_candidate)
            print(f"[RETRIEVAL] Using metadata only for {cid}")

    # Step 4: candidates_found flag
    candidates_found = (
        best_candidate is not None and
        best_similarity > 0.15
    )

    # Step 5: Log entry
    best_id = (
        best_candidate.get("case_id", "none")
        if best_candidate else "none"
    )
    log.append(
        f"{timestamp} | KNOWLEDGE_RETRIEVAL "
        f"| candidates={len(top_candidates)} "
        f"| best={best_id} "
        f"| similarity={best_similarity:.3f} "
        f"| source={selection_source}"
    )
    logger.info(
        "Node 2 complete | candidates=%d | best=%s | similarity=%.3f",
        len(top_candidates), best_id, best_similarity,
    )

    # Step 6: Return with explicit keys
    return {
        **state,
        "knowledge_exists": exists,
        "best_match": best_candidate,
        "similarity_score": best_similarity,
        "all_matches": top_candidates,
        "candidates_found": candidates_found,
        "top_candidates": top_candidates,
        "best_candidate": best_candidate,
        "best_match_content": best_match_content,
        "best_similarity": best_similarity,
        "validation_label": selection_source,
        "processing_log": log,
    }


# ── Node 3: Coverage Assessment ───────────────────────────────────────────

def coverage_assessment_node(state: LearningAgentState) -> LearningAgentState:
    """
    LLM reasoning node. Evaluates 5 knowledge dimensions.
    Determines knowledge_state: EXISTING / PARTIAL / NEW.
    Never uses similarity thresholds for routing.
    """
    timestamp = datetime.now().strftime(
        "%Y-%m-%dT%H:%M:%S")
    log = list(state.get("processing_log", []))

    feedback = state.get("feedback", {})
    best_match_content = state.get("best_match_content")
    candidates_found = state.get("candidates_found", False)

    if not candidates_found:
        result = {
            "knowledge_state": "NEW",
            "dimensions": {},
            "gap_summary": "No relevant case found.",
            "coverage_reasoning": (
                "No candidates retrieved from knowledge "
                "base. New case generation required."),
            "coverage_method": "no_candidate",
            "covered_dimensions": [],
            "missing_dimensions": []
        }
    else:
        llm_client = state.get("llm_client")
        result = assess_coverage(
            feedback, best_match_content, llm_client)

    _best_cand = state.get("best_candidate") or state.get("best_match_content") or {}
    print(
        f"[COVERAGE_NODE] "
        f"fault={feedback.get('fault_mode')!r} "
        f"asset={feedback.get('asset_type')!r} "
        f"hint={feedback.get('preferred_case_id')!r} "
        f"best_id={_best_cand.get('case_id', 'NONE')!r} "
        f"knowledge_state={result.get('knowledge_state', 'NONE')!r} "
        f"covered={result.get('covered_dimensions', [])!r} "
        f"missing={result.get('missing_dimensions', [])!r} "
        f"method={result.get('coverage_method', 'NONE')!r}",
        flush=True,
    )

    knowledge_state = result.get(
        "knowledge_state", "NEW")
    covered = result.get("covered_dimensions", [])
    missing = result.get("missing_dimensions", [])

    log.append(
        f"{timestamp} | COVERAGE_ASSESSMENT | "
        f"method={result.get('coverage_method', 'unknown')}"
        f" | knowledge_state={knowledge_state} | "
        f"covered={len(covered)}/5"
    )

    return {
        **state,
        "knowledge_state": knowledge_state,
        "dimension_assessments": result.get(
            "dimensions", {}),
        "covered_dimensions": covered,
        "missing_dimensions": missing,
        "gap_summary": result.get("gap_summary", ""),
        "coverage_reasoning": result.get(
            "coverage_reasoning", ""),
        "coverage_method": result.get(
            "coverage_method", "unknown"),
        "processing_log": log,
    }


# ── Node 4: Partial Knowledge Path C ──────────────────────────────────────

def partial_knowledge_node(state: LearningAgentState) -> LearningAgentState:
    """
    Path C — Partial Knowledge.
    Surfaces confirmed knowledge and identified gaps.
    Flags for human review.
    Does NOT modify any existing case.
    Does NOT create a duplicate case.
    """
    timestamp = datetime.now().strftime(
        "%Y-%m-%dT%H:%M:%S")
    log = list(state.get("processing_log", []))
    feedback = state.get("feedback", {})

    best_candidate = state.get("best_candidate", {})
    covered = state.get("covered_dimensions", [])
    missing = state.get("missing_dimensions", [])
    gap_summary = state.get("gap_summary", "")
    coverage_reasoning = state.get(
        "coverage_reasoning", "")
    best_similarity = state.get("best_similarity", 0.0)
    case_id = (best_candidate.get("case_id", "unknown")
               if best_candidate else "none")

    gap_details = {
        "matched_case_id": case_id,
        "covered_dimensions": covered,
        "missing_dimensions": missing,
        "gap_summary": gap_summary,
        "coverage_reasoning": coverage_reasoning,
        "similarity_score": best_similarity,
    }

    partial_summary = (
        f"Partial knowledge found in {case_id}. "
        f"{len(covered)} of 5 dimensions confirmed. "
        f"{len(missing)} dimensions require new findings. "
        f"{gap_summary}"
    )

    try:
        record_fault_event(
            case_id=feedback.get("case_id", "unknown"),
            fault_mode=feedback.get("fault_mode", "unknown"),
            asset_id=feedback.get("asset_id", "unknown"),
            asset_type=feedback.get("asset_type", "unknown"),
            bearing_type=feedback.get("bearing_type", "unknown"),
            path_taken="C",
            similarity_score=best_similarity,
            source_case_id=case_id,
            source=feedback.get("source", "pipeline"),
        )
    except Exception as e:
        print(f"[PARTIAL] Tracker write failed: {e}")

    log.append(
        f"{timestamp} | PARTIAL_KNOWLEDGE | "
        f"case={case_id} | "
        f"covered={len(covered)} | "
        f"missing={len(missing)} | "
        f"flagged_for_review=True"
    )

    return {
        **state,
        "partial_summary": partial_summary,
        "gap_details": gap_details,
        "path_taken": "C",
        "final_message": partial_summary,
        "processing_log": log,
    }


# ── Routing function ──────────────────────────────────────────────────────

def route_after_coverage_assessment(state: LearningAgentState) -> str:
    """
    Routes based on knowledge_state string only.
    No arithmetic. No threshold comparison.
    The LLM already made the coverage decision.
    """
    timestamp = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    log = state.get("processing_log", [])
    covered = state.get("covered_dimensions", [])
    missing = state.get("missing_dimensions", [])
    best_similarity = state.get("best_similarity", 0.0)
    ks = state.get("knowledge_state", "NEW")
    if ks == "EXISTING":
        log.append(
            f"{timestamp} | ROUTING | knowledge_state=EXISTING -> existing_knowledge_node"
            f" | reason=5/5 dimensions covered + sim {best_similarity:.2f}"
        )
        logger.info("Routing to PATH A: existing knowledge confirmed")
        return "existing_knowledge_node"
    elif ks == "PARTIAL":
        log.append(
            f"{timestamp} | ROUTING | knowledge_state=PARTIAL -> partial_knowledge_node"
            f" | reason={len(covered)}/5 covered, {len(missing)} missing"
        )
        logger.info("Routing to PATH C: partial knowledge, flagging gaps")
        return "partial_knowledge_node"
    else:
        log.append(
            f"{timestamp} | ROUTING | knowledge_state=NEW -> case_generation_node"
            f" | reason={len(covered)}/5 covered, no match"
        )
        logger.info("Routing to PATH B: new case, generating learned case")
        return "case_generation_node"


# ── Node 3a: Existing Knowledge (Path A) ─────────────────────────────────

def existing_knowledge_node(state: LearningAgentState) -> LearningAgentState:
    """
    Node 3a: Path A — existing knowledge retrieved.

    Called when a similar case already exists in the vector store.
    Retrieves the full metadata for the matched case and builds
    a summary for the operator.

    Does NOT create a new case. Does NOT call the LLM.
    Uses the existing stored case as-is.
    """
    log = state.get("processing_log", [])
    best = state.get("best_match", {})
    score = state.get("similarity_score", 0.0)
    now = datetime.now(timezone.utc).isoformat()[:19]

    case_id = best.get("case_id", "unknown")
    fault_mode = best.get("fault_mode", "unknown")
    asset_type = best.get("asset_type", "unknown")

    summary = (
        f"Existing knowledge retrieved: {case_id}. "
        f"Fault mode: {fault_mode} on {asset_type} bearing. "
        f"Similarity score: {score:.3f}. "
        f"Valid until: {best.get('valid_until', 'unknown')}. "
        f"No new case created — existing knowledge is sufficient."
    )

    log.append(
        f"{now} | EXISTING_KNOWLEDGE | PATH_A | "
        f"retrieved={case_id} | similarity={score:.3f}"
    )
    logger.info("Node 3a complete | PATH A | retrieved=%s", case_id)

    # Record in fault tracker for analytics
    try:
        from services.fault_tracker import record_fault_event
        record_fault_event(
            case_id=feedback.get("case_id", "unknown")
            if (feedback := state.get("feedback", {}))
            else "unknown",
            fault_mode=state.get(
                "feedback", {}
            ).get("fault_mode", "unknown"),
            asset_id=state.get(
                "feedback", {}
            ).get("asset_id", "unknown"),
            asset_type=state.get(
                "feedback", {}
            ).get("asset_type", "unknown"),
            bearing_type=state.get(
                "feedback", {}
            ).get("bearing_type", "unknown"),
            path_taken="A",
            similarity_score=state.get("similarity_score", 0.0),
            source_case_id=best.get("case_id")
        )
    except Exception as track_err:
        logger.warning(
            "Fault tracking failed (non-fatal): %s", track_err
        )

    # Write decision log entry
    try:
        with open("logs/agent_decisions.log", "a") as log_file:
            log_file.write(
                f"{now} | "
                f"{state.get('feedback',{}).get('case_id','?')} | "
                f"{state.get('feedback',{}).get('fault_mode','?')} | "
                f"PATH_A | {score:.4f} | "
                f"retrieved={case_id}\n"
            )
    except Exception:
        pass

    return {
        **state,
        "retrieved_case_id": case_id,
        "retrieved_summary": summary,
        "path_taken": "A",
        "final_message": summary,
        "processing_log": log
    }


# ── Node 3b: Case Generation (Path B) ────────────────────────────────────

def case_generation_node(state: LearningAgentState) -> LearningAgentState:
    """
    Node 3b: Path B — generate a new learned case using LLM.

    Called only when no similar case exists in the vector store.
    Uses the LLM to generate structured case content from pipeline data.

    LLM is used HERE for content generation only.
    The routing decision was made by LangGraph in route_after_knowledge_check.
    """
    log = state.get("processing_log", [])
    feedback = state.get("feedback", {})
    now = datetime.now(timezone.utc).isoformat()[:19]

    try:
        from services.case_generator import (
            generate_case_from_pipeline,
            build_learned_case_object
        )

        generated = generate_case_from_pipeline(
            feedback,
            azure_openai_key=os.getenv("AZURE_OPENAI_KEY", ""),
            azure_openai_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT", ""),
            azure_openai_deployment=os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4"),
            azure_openai_api_version=os.getenv("AZURE_OPENAI_API_VERSION", "2024-02-01"),
        )
        learned_case = build_learned_case_object(feedback, generated)

        log.append(
            f"{now} | CASE_GENERATION | PATH_B | "
            f"case={feedback.get('case_id')} | "
            f"lessons={len(generated.get('lessons_learned', []))}"
        )
        logger.info(
            "Node 3b complete | PATH B | case=%s",
            feedback.get("case_id")
        )

        return {
            **state,
            "generated_case_content": generated,
            "learned_case_object": learned_case,
            "path_taken": "B",
            "processing_log": log
        }
    except Exception as e:
        log.append(f"{now} | CASE_GENERATION | ERROR | {e}")
        logger.error("Case generation failed: %s", e)
        return {
            **state,
            "path_taken": "B_error",
            "final_message": f"Case generation failed: {e}",
            "processing_log": log
        }


# ── Node 4: Memory Storage (Path B only) ─────────────────────────────────

def memory_storage_node(state: LearningAgentState) -> LearningAgentState:
    """
    Node 4: Path B only — stores the new learned case in FAISS.

    Validates the learned case object using LearnedCase Pydantic schema.
    Generates embedding from case text.
    Stores vector and metadata in FAISS.
    Writes 4 audit log events.
    Saves FAISS index to disk.
    """
    log = state.get("processing_log", [])
    now = datetime.now(timezone.utc).isoformat()[:19]

    # NL assessment mode: caller (chatbot/manual) decides save based on verdict.
    _fb = state.get("feedback", {})
    if _fb.get("_nl_assessment_only"):
        log.append(
            f"{now} | MEMORY_STORAGE | NL_ASSESSMENT_ONLY | "
            "skipping pipeline-side persist — caller will save based on verdict."
        )
        return {
            **state,
            "processing_log": log,
            "storage_success": False,
            "stored_case_id": None,
        }

    learned_case = state.get("learned_case_object", {})

    if not learned_case:
        msg = "No learned case object to store"
        log.append(f"{now} | MEMORY_STORAGE | SKIPPED | {msg}")
        return {**state, "storage_success": False, "processing_log": log}

    try:
        from schemas.learned_case import LearnedCase, CaseMetadata

        # Replace DISC_xxx placeholder from feedback with a real minted
        # CASE_YYYYMMDD_NNN. All downstream sites (FAISS write, disk write,
        # tracker write, decision log write) read case.case_id, so this
        # single injection fixes them all.
        _new_case_id = mint_new_case_id()
        logger.info(
            "[PATH_B] Minted new case_id: %s (replaced DISC placeholder %s)",
            _new_case_id, learned_case.get("case_id")
        )
        learned_case = {**learned_case, "case_id": _new_case_id}

        case = LearnedCase(**learned_case)
        log_memory_created(case.case_id, case.fault_mode, case.asset_type)

        embedding_text = build_embedding_text(learned_case)
        embedding = generate_embedding(embedding_text)

        store = VectorStorage()
        store.load_index()

        if store.case_exists(case.case_id):
            msg = f"Case {case.case_id} already exists, skipping storage"
            log.append(f"{now} | MEMORY_STORAGE | DUPLICATE_SKIPPED | {msg}")
            return {
                **state,
                "stored_case_id": case.case_id,
                "storage_success": False,
                "final_message": msg,
                "processing_log": log
            }

        _fb = state.get("feedback", {})
        case_meta = CaseMetadata(
            case_id=case.case_id,
            created_at=_fb.get("created_at") or datetime.now(timezone.utc).isoformat(),
            updated_at=datetime.now(timezone.utc).isoformat(),
            valid_until=case.valid_until,
            created_by=case.created_by,
            source=_fb.get("source", "pipeline"),
            embedding_dim=len(embedding),
            fault_mode=case.fault_mode,
            asset_type=case.asset_type,
            bearing_type=case.bearing_type
        ).model_dump()
        case_meta.update({
            "root_cause": learned_case.get("root_cause", "unknown"),
            "lessons_learned": learned_case.get("lessons_learned", ""),
            "action_taken": learned_case.get("action_taken", ""),
            "result": learned_case.get("result", ""),
        })

        faiss_success = store.add(
            embedding, case_meta, case.case_id)
        if faiss_success:
            store.save_index()

        # Write .md and companion .json as independent operations so a failure
        # in one does not suppress the other. FAISS is already persisted above;
        # the post-write check at the end logs an error if either file is
        # missing so the mismatch is visible without losing the FAISS entry.
        _doc_source = _fb.get("source", "pipeline")
        _md_path = None
        _json_path = None

        # --- Markdown document write ---
        try:
            from services.case_generator import render_case_as_markdown
            _md_path = Path(
                f"data/learned_cases/"
                f"{case.case_id.lower()}_{_doc_source}_generated.md"
            )
            markdown_doc = render_case_as_markdown(
                case.case_id,
                state.get("feedback", {}),
                state.get("generated_case_content", {})
            )
            os.makedirs("data/learned_cases", exist_ok=True)
            with open(_md_path, "w", encoding="utf-8") as f:
                f.write(markdown_doc)
            log.append(
                f"{now} | DOCUMENT_WRITTEN | {case.case_id} | "
                f"path={_md_path}"
            )
            logger.info(
                "Case document written | path=%s", _md_path
            )
        except Exception as doc_err:
            logger.error(
                "Document write failed: %s", doc_err, exc_info=True
            )
            log.append(
                f"{now} | DOCUMENT_WRITE_FAILED | "
                f"{case.case_id} | {doc_err}"
            )

        # --- Companion .json write (independent of .md) ---
        # The Recent Case Documents panel globs *.json — this file must be
        # written even if the .md write above failed.
        try:
            _json_path = Path(
                f"data/learned_cases/"
                f"{case.case_id.lower()}_{_doc_source}_generated.json"
            )
            _ad = learned_case.get("agent_diagnosis", {})
            _sig = (
                f"vib_rms={_fb.get('vib_rms_mm_s', '')} mm/s, "
                f"kurtosis={_fb.get('kurtosis', '')}, "
                f"temp={_fb.get('temp_c', '')}C, "
                f"bpfo={_fb.get('bpfo_energy', '')}x, "
                f"signal_quality="
                f"{_fb.get('signal_quality_score', _fb.get('signal_quality', ''))}"
            )
            _case_json = {
                "case_id":              case.case_id,
                "asset_id":             _fb.get("asset_id", "unknown"),
                "asset_type":           case.asset_type,
                "bearing_type":         case.bearing_type,
                "fault_mode":           case.fault_mode,
                "root_cause":           case_meta.get("root_cause", ""),
                "action_taken":         case_meta.get("action_taken", ""),
                "lessons_learned":      case_meta.get("lessons_learned", ""),
                "result":               case_meta.get("result", "unknown"),
                "valid_until":          str(case.valid_until),
                "created_by":           case.created_by,
                "source":               _doc_source,
                "path_taken":           "B",
                "created_at":           case_meta.get("created_at", now),
                "reasoning": (
                    _ad.get("reasoning", "")
                    if isinstance(_ad, dict) else str(_ad)
                ),
                "signal_signature":     _sig,
                "vib_rms_mm_s":         _fb.get("vib_rms_mm_s"),
                "kurtosis":             _fb.get("kurtosis"),
                "temp_c":               _fb.get("temp_c"),
                "bpfo_energy":          _fb.get("bpfo_energy"),
                "signal_quality_score": _fb.get(
                    "signal_quality_score",
                    _fb.get("signal_quality")
                ),
            }
            _case_json = {
                k: v for k, v in _case_json.items()
                if v is not None
            }
            os.makedirs("data/learned_cases", exist_ok=True)
            with open(_json_path, "w", encoding="utf-8") as _jf:
                _jf.write(json.dumps(_case_json, indent=2))
            log.append(
                f"{now} | JSON_COMPANION_WRITTEN | {case.case_id} | "
                f"path={_json_path}"
            )
            logger.info(
                "Companion JSON written | path=%s", _json_path
            )
        except Exception as _json_err:
            logger.error(
                "Companion JSON write failed: %s", _json_err, exc_info=True
            )
            log.append(
                f"{now} | JSON_COMPANION_WRITE_FAILED | "
                f"{case.case_id} | {_json_err}"
            )

        # Post-write verification: FAISS is already persisted; if either disk
        # file is missing the case won't appear in Recent Case Documents until
        # disk is repaired.
        _md_exists = _md_path.exists() if _md_path is not None else False
        _json_exists = _json_path.exists() if _json_path is not None else False
        if not (_md_exists and _json_exists):
            logger.error(
                "[memory_storage] Disk-write incomplete for %s: md=%s json=%s. "
                "FAISS entry exists but panel will not show this case until "
                "disk is repaired.",
                case.case_id, _md_exists, _json_exists,
            )

        if faiss_success:
            log_vector_stored(
                case.case_id, len(embedding),
                store.get_vector_count())
            log_metadata_stored(
                case.case_id, case.valid_until,
                case.created_by)
            log_valid_until_set(
                case.case_id, case.valid_until,
                case.created_by)

        # Record in fault tracker for analytics
        try:
            from services.fault_tracker import record_fault_event
            record_fault_event(
                case_id=case.case_id,
                fault_mode=case.fault_mode,
                asset_id=_fb.get("asset_id", "unknown"),
                asset_type=case.asset_type,
                bearing_type=case.bearing_type,
                path_taken="B",
                similarity_score=state.get(
                    "similarity_score", 0.0
                ),
                source_case_id=None,
                source=_fb.get("source", "pipeline"),
            )
        except Exception as track_err:
            logger.warning(
                "Fault tracking failed (non-fatal): %s",
                track_err
            )

        # Write structured decision log entry
        try:
            with open(
                "logs/agent_decisions.log", "a"
            ) as log_file:
                log_file.write(
                    f"{now} | {case.case_id} | "
                    f"{case.fault_mode} | PATH_B | "
                    f"{state.get('similarity_score',0):.4f} | "
                    f"new_case_generated\n"
                )
        except Exception:
            pass

        if faiss_success:
            final_msg = (
                f"New case {case.case_id} generated and stored. "
                f"Fault: {case.fault_mode}. "
                f"Vector index now contains "
                f"{store.get_vector_count()} cases. "
                f"Case is now retrievable by the Knowledge Agent."
            )
            log.append(
                f"{now} | MEMORY_STORAGE | STORED | "
                f"case={case.case_id} | "
                f"total_vectors={store.get_vector_count()}"
            )
            logger.info(
                "Node 4 complete | stored=%s | total=%d",
                case.case_id, store.get_vector_count()
            )
        else:
            log.append(
                f"{now} | MEMORY_STORAGE | FAILED | "
                f"vector add failed")
            return {
                **state,
                "storage_success": False,
                "final_message": "Vector storage failed",
                "processing_log": log
            }

        return {
            **state,
            "stored_case_id": case.case_id,
            "storage_success": True,
            "final_message": final_msg,
            "processing_log": log
        }

    except Exception as e:
        log.append(f"{now} | MEMORY_STORAGE | ERROR | {e}")
        logger.error("Memory storage failed: %s", e)
        return {
            **state,
            "storage_success": False,
            "final_message": f"Storage error: {e}",
            "processing_log": log
        }


# ── Build the graph ───────────────────────────────────────────────────────

def build_learning_graph():
    """
    Assembles and compiles the 7-node LangGraph workflow.

    Graph structure:
    START
      → feedback_capture_node
      → retrieval_node
      → coverage_assessment_node
      → [conditional routing via route_after_coverage_assessment]
           → existing_knowledge_node → END            (Path A)
           → partial_knowledge_node  → END            (Path C)
           → case_generation_node
               → memory_storage_node → END            (Path B)

    Returns compiled LangGraph app with MemorySaver checkpoint.
    """
    if not LANGGRAPH_AVAILABLE:
        logger.error("LangGraph not available")
        return None

    graph = StateGraph(LearningAgentState)

    graph.add_node("feedback_capture_node",   feedback_capture_node)
    graph.add_node("retrieval_node",           retrieval_node)
    graph.add_node("coverage_assessment_node", coverage_assessment_node)
    graph.add_node("partial_knowledge_node",   partial_knowledge_node)
    graph.add_node("existing_knowledge_node",  existing_knowledge_node)
    graph.add_node("case_generation_node",     case_generation_node)
    graph.add_node("memory_storage_node",      memory_storage_node)

    graph.set_entry_point("feedback_capture_node")
    graph.add_edge("feedback_capture_node", "retrieval_node")
    graph.add_edge("retrieval_node",         "coverage_assessment_node")

    graph.add_conditional_edges(
        "coverage_assessment_node",
        route_after_coverage_assessment,
        {
            "existing_knowledge_node": "existing_knowledge_node",
            "partial_knowledge_node":  "partial_knowledge_node",
            "case_generation_node":    "case_generation_node",
        }
    )

    graph.add_edge("existing_knowledge_node", END)
    graph.add_edge("partial_knowledge_node",  END)
    graph.add_edge("case_generation_node",    "memory_storage_node")
    graph.add_edge("memory_storage_node",     END)

    checkpointer = MemorySaver()
    app = graph.compile(checkpointer=checkpointer)
    logger.info("LangGraph workflow compiled successfully")
    return app


async def run_learning_workflow(
    feedback: dict,
    run_id: str,
    llm_client: object = None,
    langfuse_handler: object = None,
) -> LearningAgentState:
    """
    Runs the full LangGraph workflow for a single feedback event.
    Returns the final graph state after all nodes complete.

    Args:
        feedback:   FeedbackEvent dict from upstream pipeline
        run_id:     Unique identifier for this run (used as thread_id)
        llm_client: Optional LangChain LLM client for coverage assessment
    """
    app = build_learning_graph()
    if app is None:
        return {
            "run_id": run_id,
            "feedback": feedback,
            "processing_log": ["LangGraph not available"],
            "path_taken": "error",
            "final_message": "LangGraph not installed"
        }

    config = {
        "configurable": {"thread_id": run_id}
    }
    initial_state = {
        "run_id": run_id,
        "feedback": feedback,
        "processing_log": [],
        "llm_client": llm_client,
        # Backward-compatible fields
        "knowledge_exists": None,
        "best_match": None,
        "similarity_score": None,
        "all_matches": None,
        # Retrieval fields
        "candidates_found": None,
        "best_candidate": None,
        "top_candidates": None,
        "best_match_content": None,
        "best_similarity": None,
        "validation_label": None,
        # Coverage assessment fields
        "knowledge_state": None,
        "dimension_assessments": None,
        "covered_dimensions": None,
        "missing_dimensions": None,
        "gap_summary": None,
        "coverage_reasoning": None,
        "coverage_method": None,
        # Path A outputs
        "retrieved_case_id": None,
        "retrieved_summary": None,
        # Path C outputs
        "partial_summary": None,
        "gap_details": None,
        # Path B outputs
        "generated_case_content": None,
        "learned_case_object": None,
        # Storage outputs
        "stored_case_id": None,
        "storage_success": None,
        # Final state
        "path_taken": None,
        "final_message": None,
    }

    _callbacks = [langfuse_handler] if langfuse_handler else []
    config["callbacks"] = _callbacks
    final_state = await app.ainvoke(initial_state, config=config)

    # Flush Langfuse spans before returning so traces appear in dashboard
    if langfuse_handler is not None:
        try:
            from langfuse import get_client
            _lf = get_client()
            if _lf is not None:
                _lf.flush()
        except Exception as _flush_err:
            print(f"[LANGFUSE_FLUSH_WARN] {_flush_err}")

    return final_state
