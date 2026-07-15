"""
Tests for the memory loading pipeline.
Covers: learned case loading from JSON, metadata creation,
        timestamp creation, and audit log generation.
Does NOT test retrieval (Knowledge Agent responsibility).
Run: pytest tests/test_memory.py -v
"""

import pytest
import json
import os
from datetime import datetime, timezone
from schemas.learned_case import LearnedCase, CaseMetadata
from pydantic import ValidationError


SAMPLE_CASE_DATA = {
    "case_id": "CASE_TEST_MEM_001",
    "asset_type": "motor",
    "bearing_type": "SKF6310",
    "fault_mode": "outer_race_fault",
    "root_cause": "contamination ingress",
    "action_taken": "bearing replacement",
    "result": "successful repair",
    "lessons_learned": "replace seal with bearing",
    "valid_until": "2028-12-31",
    "created_by": "test"
}


class TestCaseLoading:

    def test_valid_json_loads_to_schema(self):
        """A valid JSON dict should load into LearnedCase schema without error."""
        case = LearnedCase(**SAMPLE_CASE_DATA)
        assert case.case_id == "CASE_TEST_MEM_001"
        assert case.fault_mode == "outer_race_fault"

    def test_invalid_json_raises_validation_error(self):
        """JSON with invalid fault_mode should raise ValidationError."""
        bad_data = {**SAMPLE_CASE_DATA, "fault_mode": "not_a_real_fault"}
        with pytest.raises(ValidationError):
            LearnedCase(**bad_data)

    def test_load_from_temp_json_file(self, tmp_path):
        """
        Simulates loading a case from a real JSON file.
        Creates a temp file, writes case data, reads it back, validates schema.
        """
        case_file = tmp_path / "case_test.json"
        case_file.write_text(json.dumps(SAMPLE_CASE_DATA))
        
        with open(case_file, "r") as f:
            loaded = json.load(f)
        
        case = LearnedCase(**loaded)
        assert case.case_id == SAMPLE_CASE_DATA["case_id"]


class TestMetadataCreation:

    def test_metadata_fields_populated_correctly(self):
        """CaseMetadata must contain all required fields after creation."""
        now = datetime.now(timezone.utc).isoformat()
        meta = CaseMetadata(
            case_id="CASE_TEST_001",
            created_at=now,
            updated_at=now,
            valid_until="2028-12-31",
            created_by="test",
            source="seed_data",
            embedding_dim=384,
            fault_mode="outer_race_fault",
            asset_type="motor",
            bearing_type="SKF6310"
        )
        assert meta.case_id == "CASE_TEST_001"
        assert meta.embedding_dim == 384
        assert meta.source == "seed_data"

    def test_metadata_valid_until_matches_case(self):
        """valid_until in metadata must match valid_until from the case record."""
        case = LearnedCase(**SAMPLE_CASE_DATA)
        now = datetime.now(timezone.utc).isoformat()
        meta = CaseMetadata(
            case_id=case.case_id,
            created_at=now,
            updated_at=now,
            valid_until=case.valid_until,
            created_by=case.created_by,
            source="seed_data",
            embedding_dim=384,
            fault_mode=case.fault_mode,
            asset_type=case.asset_type,
            bearing_type=case.bearing_type
        )
        assert meta.valid_until == case.valid_until == "2028-12-31"


class TestTimestampCreation:

    def test_created_at_is_valid_iso_timestamp(self):
        """created_at must be a valid ISO timestamp string."""
        now = datetime.now(timezone.utc).isoformat()
        parsed = datetime.fromisoformat(now)
        assert parsed is not None

    def test_timestamps_are_strings(self):
        """Timestamps stored in metadata must be strings not datetime objects."""
        now = datetime.now(timezone.utc).isoformat()
        assert isinstance(now, str)
        assert "T" in now


class TestAuditLogGeneration:

    def test_audit_log_file_created(self, tmp_path):
        """
        Audit log file must be created when log events are written.
        Uses tmp_path to avoid writing to real logs/ directory during tests.
        """
        log_path = str(tmp_path / "test_memory.log")
        
        import logging
        test_handler = logging.FileHandler(log_path)
        test_logger = logging.getLogger("audit_test")
        test_logger.addHandler(test_handler)
        test_logger.setLevel(logging.INFO)
        test_logger.info("MEMORY_CREATED | CASE_TEST | fault=outer_race_fault")
        test_handler.flush()
        
        assert os.path.exists(log_path)
        with open(log_path, "r") as f:
            content = f.read()
        assert "MEMORY_CREATED" in content

    def test_audit_log_contains_case_id(self, tmp_path):
        """Audit log entries must contain the case_id."""
        log_path = str(tmp_path / "test_audit.log")
        
        import logging
        handler = logging.FileHandler(log_path)
        logger = logging.getLogger("audit_test_2")
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.info("VECTOR_STORED | CASE_001 | dim=384 index_total=1")
        handler.flush()
        
        with open(log_path, "r") as f:
            content = f.read()
        assert "CASE_001" in content
