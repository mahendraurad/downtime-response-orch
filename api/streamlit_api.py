"""Streamlit adapter layer.

Bridge between the Streamlit UI (streamlit_app_v3.py) and the
LangGraph orchestration pipeline (orchestrator/langgraph_flow.py).

Exports:
  - run_pipeline_demo: Invoke the L&M pipeline for a scenario
  - create_learned_case: Persist a user-captured case via PATH_B
  - get_case_summary: Load a case document for UI display

Historical note: the api/ folder previously also contained
routes.py for a FastAPI backend. The FastAPI interface has been
archived (see archive/fastapi_removed_20260713_121310/). The api/
folder now contains only Streamlit adapter code.
"""

import asyncio
import concurrent.futures
import json
import logging
import time as _time
import traceback
from datetime import datetime, timezone
from typing import Tuple

from orchestrator.langgraph_flow import run_learning_workflow
from rag.vector_storage import VectorStorage

logger = logging.getLogger(__name__)


def create_learned_case(
    case_id: str,
    asset_type: str,
    bearing_type: str,
    fault_mode: str,
    root_cause: str,
    action_taken: str,
    result: str,
    lessons_learned: str,
    valid_until: str,
    created_by: str = "streamlit_user"
) -> Tuple[bool, str]:
    """
    Creates a new learned case from manual input.
    This is the function Streamlit will call when a user submits the form.
    
    Args:
        All case fields as individual strings.
        valid_until: date string in YYYY-MM-DD format (from Streamlit date picker)
        created_by: who is creating this - defaults to streamlit_user
    
    Returns:
        Tuple of (success: bool, message: str)
        Streamlit displays the message to the user.
    """
    try:
        from schemas.learned_case import LearnedCase, CaseMetadata
        from rag.embeddings import generate_embedding, build_embedding_text
        from rag.vector_storage import VectorStorage
        from storage.audit_logger import (
            log_memory_created,
            log_vector_stored,
            log_metadata_stored,
            log_valid_until_set,
        )

        # Step 1: Validate with Pydantic schema
        # This catches wrong valid_until format before anything is stored
        case = LearnedCase(
            case_id=case_id,
            asset_type=asset_type,
            bearing_type=bearing_type,
            fault_mode=fault_mode,
            root_cause=root_cause,
            action_taken=action_taken,
            result=result,
            lessons_learned=lessons_learned,
            valid_until=valid_until,
            created_by=created_by
        )
        
        # Step 2: Log memory created
        log_memory_created(case.case_id, case.fault_mode, case.asset_type)
        
        # Step 3: Generate embedding
        embedding_text = build_embedding_text(case.model_dump())
        embedding = generate_embedding(embedding_text)
        
        # Step 4: Store in FAISS
        store = VectorStorage()
        store.load_index()
        
        if store.case_exists(case.case_id):
            return False, f"Case {case_id} already exists in the knowledge base."
        
        now = datetime.now(timezone.utc).isoformat()
        metadata = CaseMetadata(
            case_id=case.case_id,
            created_at=now,
            updated_at=now,
            valid_until=case.valid_until,
            created_by=case.created_by,
            source="streamlit_user",
            embedding_dim=len(embedding),
            fault_mode=case.fault_mode,
            asset_type=case.asset_type,
            bearing_type=case.bearing_type
        ).model_dump()
        
        success = store.add(embedding, metadata, case.case_id)
        
        if not success:
            return False, "Failed to store vector. Check logs/memory.log."
        
        store.save_index()
        
        # Step 5: Write audit logs
        log_vector_stored(case.case_id, len(embedding), store.get_vector_count())
        log_metadata_stored(case.case_id, case.valid_until, case.created_by)
        log_valid_until_set(case.case_id, case.valid_until, case.created_by)
        
        # Step 6: Optionally save to data/learned_cases/ as JSON backup
        output_path = f"data/learned_cases/{case_id.lower()}_manual.json"
        with open(output_path, "w") as f:
            json.dump(case.model_dump(), f, indent=2)
        
        return True, f"Case {case_id} stored successfully."
        
    except Exception as e:
        logger.error("create_learned_case failed: %s", str(e))
        return False, f"Validation error: {str(e)}"


def run_pipeline_demo(feedback: dict,
                      run_id: str,
                      llm_client=None,
                      langfuse_handler=None) -> dict:
    """
    Runs the real LangGraph learning workflow for a feedback event.

    This is the function the Streamlit Pipeline Demo tab calls. It wraps
    the async LangGraph orchestrator so the UI can stay synchronous.

    Args:
        feedback: FeedbackEvent dict built from the selected scenario.
                  Must contain: case_id, fault_mode, asset_type,
                  bearing_type (required by feedback_capture_node).
        run_id:   Short unique identifier for this run.

    Returns:
        The final LangGraph state dict. Key fields for UI display:
          knowledge_state, path_taken, processing_log,
          dimension_assessments, covered_dimensions, missing_dimensions,
          gap_summary, coverage_reasoning, best_similarity,
          retrieved_case_id, retrieved_summary,
          generated_case_content, stored_case_id, final_message
    """
    print("[PIPELINE DEBUG] run_pipeline_demo called")
    print(
        f"[PIPELINE DEBUG] feedback keys: "
        f"{list(feedback.keys()) if isinstance(feedback, dict) else type(feedback)}")
    print(f"[PIPELINE] preferred_case_id: "
          f"'{feedback.get('preferred_case_id', 'MISSING') if isinstance(feedback, dict) else 'N/A'}'")
    print("[PIPELINE DEBUG] about to call run_learning_workflow")

    _pipeline_t0 = _time.perf_counter()
    result = {}
    try:
        try:
            asyncio.get_running_loop()
            loop_is_running = True
        except RuntimeError:
            loop_is_running = False

        if loop_is_running:
            # Called from inside a running event loop (e.g. Streamlit).
            # Dispatch to a worker thread with its own fresh event loop.
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(
                    asyncio.run,
                    run_learning_workflow(feedback, run_id, llm_client=llm_client, langfuse_handler=langfuse_handler),
                )
                result = future.result(timeout=120)
        else:
            # No running loop — asyncio.run() creates one directly.
            result = asyncio.run(
                run_learning_workflow(feedback, run_id, llm_client=llm_client, langfuse_handler=langfuse_handler)
            )
    except Exception as e:
        print(f"[PIPELINE DEBUG] FAILED: {e}")
        traceback.print_exc()
        logger.error("run_pipeline_demo failed: %s", str(e))
        result = {}

    _pipeline_ms = int((_time.perf_counter() - _pipeline_t0) * 1000)
    if isinstance(result, dict) and result:
        result["pipeline_duration_ms"] = _pipeline_ms

    print(f"[PIPELINE DEBUG] result type: {type(result)}")
    print(
        f"[PIPELINE DEBUG] result keys: "
        f"{list(result.keys()) if isinstance(result, dict) else result}")

    # Normalise: ensure required UI keys are always present
    if isinstance(result, dict):
        if "processing_log" not in result or not result["processing_log"]:
            result["processing_log"] = [
                "Pipeline completed — no log entries recorded"]
        if "knowledge_state" not in result or result["knowledge_state"] is None:
            result["knowledge_state"] = "NEW"
        if "path_taken" not in result or result["path_taken"] is None:
            result["path_taken"] = "B"

    return result


def get_case_summary() -> dict:
    """
    Returns a summary of all cases currently in the knowledge base.
    Streamlit will call this to display a dashboard of loaded cases.
    
    Returns:
        {
            "total_cases": int,
            "cases": [list of case metadata dicts],
            "index_path": str,
            "log_path": str
        }
    """
    store = VectorStorage()
    store.load_index()
    
    return {
        "total_cases": store.get_vector_count(),
        "cases": list(store.metadata.values()),
        "index_path": "data/faiss_index/index.faiss",
        "log_path": "logs/memory.log"
    }
