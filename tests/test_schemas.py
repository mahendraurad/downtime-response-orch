"""
Tests for LearnedCase and CaseMetadata Pydantic schemas.
Covers: valid_until required, format validation, fault_mode validation,
        required fields, and CaseMetadata structure.
Run: pytest tests/test_schemas.py -v
"""

import pytest
from datetime import datetime, timezone
from schemas.learned_case import LearnedCase, CaseMetadata
from pydantic import ValidationError


VALID_CASE = {
    "case_id": "CASE_TEST_001",
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


class TestLearnedCaseSchema:

    def test_valid_case_passes(self):
        """A complete valid case should create without error."""
        case = LearnedCase(**VALID_CASE)
        assert case.case_id == "CASE_TEST_001"
        assert case.fault_mode == "outer_race_fault"

    def test_valid_until_is_required(self):
        """valid_until is required. Omitting it must raise ValidationError."""
        data = {k: v for k, v in VALID_CASE.items() if k != "valid_until"}
        with pytest.raises(ValidationError) as exc_info:
            LearnedCase(**data)
        assert "valid_until" in str(exc_info.value)

    def test_valid_until_wrong_format_rejected(self):
        """valid_until in wrong format (DD-MM-YYYY) must raise ValidationError."""
        data = {**VALID_CASE, "valid_until": "31-12-2028"}
        with pytest.raises(ValidationError) as exc_info:
            LearnedCase(**data)
        assert "YYYY-MM-DD" in str(exc_info.value)

    def test_valid_until_slash_format_rejected(self):
        """valid_until with slashes (2028/12/31) must raise ValidationError."""
        data = {**VALID_CASE, "valid_until": "2028/12/31"}
        with pytest.raises(ValidationError):
            LearnedCase(**data)

    def test_valid_until_invalid_date_rejected(self):
        """valid_until with impossible date (2028-13-01) must raise ValidationError."""
        data = {**VALID_CASE, "valid_until": "2028-13-01"}
        with pytest.raises(ValidationError):
            LearnedCase(**data)

    def test_invalid_fault_mode_rejected(self):
        """Unknown fault_mode must raise ValidationError."""
        data = {**VALID_CASE, "fault_mode": "unknown_fault_xyz"}
        with pytest.raises(ValidationError) as exc_info:
            LearnedCase(**data)
        assert "fault_mode" in str(exc_info.value)

    def test_all_supported_fault_modes_accepted(self):
        """Every supported fault_mode must pass validation."""
        supported = [
            "outer_race_fault", "inner_race_fault", "lubrication_issue",
            "imbalance", "misalignment", "cage_fault", "sensor_fault", "healthy"
        ]
        for mode in supported:
            data = {**VALID_CASE, "fault_mode": mode}
            case = LearnedCase(**data)
            assert case.fault_mode == mode

    def test_missing_required_field_rejected(self):
        """Omitting a required field (case_id) must raise ValidationError."""
        data = {k: v for k, v in VALID_CASE.items() if k != "case_id"}
        with pytest.raises(ValidationError):
            LearnedCase(**data)

    def test_created_by_defaults_to_seed_data(self):
        """created_by should default to seed_data if not provided."""
        data = {k: v for k, v in VALID_CASE.items() if k != "created_by"}
        case = LearnedCase(**data)
        assert case.created_by == "seed_data"


class TestCaseMetadataSchema:

    def test_valid_metadata_passes(self):
        """A complete valid CaseMetadata should create without error."""
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
