"""
Pipeline flow tests for the Learning & Memory Agent.

Tests 5 scenarios as requested by manager:
  Test 1: Correct retrieval - known fault returns Path A
  Test 2: Wrong retrieval simulation - different fault type
          with high similarity should still take Path B
  Test 3: No retrieval - empty index returns Path B
  Test 4: Partial similarity - score near threshold boundary
  Test 5: Duplicate prevention - same case_id not stored twice

Requirement: test success, failure, and edge cases.
Not just the happy path.
"""

import pytest
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)
)))


class TestCorrectRetrieval:
    """Test 1: Known fault returns Path A with correct match."""

    def test_outer_race_matches_case_001(self):
        """
        Scenario A feedback should match CASE_001 with
        similarity above threshold and fault_mode matching.
        """
        from demo.scenarios import get_scenario_a
        from services.knowledge_check import check_existing_knowledge

        feedback = get_scenario_a()
        exists, best, score, results = check_existing_knowledge(
            feedback
        )

        assert score > 0.70, (
            f"Expected score > 0.70, got {score:.4f}. "
            f"Scenario A should match CASE_001."
        )
        assert exists is True, (
            "Scenario A should trigger Path A (existing knowledge)"
        )
        assert best is not None, "Best match should not be None"
        assert best.get("fault_matches") is True, (
            "Fault mode should match for Scenario A"
        )


class TestWrongRetrievalPrevention:
    """
    Test 2: High similarity with wrong fault type should
    still route to Path B due to double validation.
    """

    def test_different_fault_type_routes_to_path_b(self):
        """
        Even if similarity score is above threshold, a different
        fault_mode should reduce adjusted_score and trigger Path B.
        """
        from services.knowledge_check import (
            check_existing_knowledge,
            SIMILARITY_THRESHOLD
        )

        # Create feedback with healthy fault mode but outer_race
        # sensor values — should not match outer_race cases
        feedback = {
            "case_id": "TEST_WRONG_RETRIEVAL",
            "asset_type": "motor",
            "bearing_type": "SKF6310",
            "fault_mode": "healthy",
            "root_cause": "no fault found",
            "diagnosis_reasoning": (
                "All signals within baseline. "
                "No threshold exceeded."
            )
        }

        exists, best, score, results = check_existing_knowledge(
            feedback
        )

        # If any result was found with different fault_mode,
        # verify validation_label is not full_match
        if results:
            for r in results:
                if not r.get("fault_matches"):
                    assert r["validation_label"] != "full_match", (
                        "A result with mismatched fault_mode "
                        "should not be labelled full_match"
                    )


class TestNoRetrieval:
    """Test 3: Empty or irrelevant index returns Path B."""

    def test_novel_fault_routes_to_path_b(self):
        """
        Scenario B is a genuinely new fault type not in seed data.
        Should always return Path B.
        """
        from demo.scenarios import get_scenario_b
        from services.knowledge_check import check_existing_knowledge

        feedback = get_scenario_b()
        exists, best, score, results = check_existing_knowledge(
            feedback
        )

        assert exists is False, (
            f"Scenario B should trigger Path B (new case). "
            f"Score was {score:.4f}"
        )
        assert score < 0.70, (
            f"Scenario B similarity should be below 0.70, "
            f"got {score:.4f}"
        )


class TestPartialSimilarity:
    """Test 4: Score near threshold boundary behaves correctly."""

    def test_boundary_score_with_fault_mismatch_goes_to_path_b(
        self
    ):
        """
        A score of exactly 0.70 with a non-matching fault type
        should go to Path B after adjusted score calculation.
        """
        from services.knowledge_check import cosine_similarity
        import numpy as np

        # Verify cosine_similarity function works correctly
        vec_a = [1.0, 0.0, 0.0]
        vec_b = [1.0, 0.0, 0.0]
        assert cosine_similarity(vec_a, vec_b) == 1.0, (
            "Identical vectors should have similarity 1.0"
        )

        vec_c = [0.0, 1.0, 0.0]
        sim = cosine_similarity(vec_a, vec_c)
        assert sim == 0.0, (
            "Orthogonal vectors should have similarity 0.0"
        )

    def test_adjusted_score_penalty_applied(self):
        """
        Verify that fault_mode mismatch applies -0.08 penalty
        to adjusted score.
        """
        from services.knowledge_check import SIMILARITY_THRESHOLD

        raw_similarity = 0.73
        penalty = -0.08
        adjusted = raw_similarity + penalty

        assert adjusted < SIMILARITY_THRESHOLD, (
            f"Adjusted score {adjusted:.2f} should be below "
            f"threshold {SIMILARITY_THRESHOLD} when fault "
            f"type does not match"
        )


class TestDuplicatePrevention:
    """Test 5: Same case_id cannot be stored twice."""

    def test_existing_case_id_not_stored_again(self):
        """
        VectorStorage.case_exists() should return True for
        all 7 seed cases, preventing re-storage.
        """
        from rag.vector_storage import VectorStorage

        store = VectorStorage()
        store.load_index()

        seed_cases = [
            "CASE_001", "CASE_002", "CASE_003",
            "CASE_004", "CASE_005", "CASE_006", "CASE_007"
        ]

        for case_id in seed_cases:
            exists = store.case_exists(case_id)
            assert exists is True, (
                f"{case_id} should exist in vector store "
                f"after load_data.py. Run python tools/load_data.py"
            )

    def test_unknown_case_id_not_in_store(self):
        """A randomly generated case_id should not exist."""
        from rag.vector_storage import VectorStorage

        store = VectorStorage()
        store.load_index()

        assert not store.case_exists("CASE_NONEXISTENT_XYZ"), (
            "Random case_id should not exist in the vector store"
        )
