"""
tests/test_prescriptive_agent.py  —  Phase 7

Unit tests for the Prescriptive Optimization Agent and its supporting tools.
Covers all 13 branch scenarios adapted to DRO's schema fields:
  - FaultDiagnosis uses severity="low/medium/high/critical" + iso_stage=0-3
    (not the branch's severity="stage_1/2/3/monitor")
  - FaultDiagnosis.evidence is Dict[str, Any] (not list[str])
  - No affected_component field on FaultDiagnosis
  - RiskAssessment uses rul_min_days/rul_max_days as int with defaults 0

Guard scenarios (all four blocking paths):
  A1 / B6  blocked_unknown_asset
  A2 / B5  unreliable_diagnosis
  A3 / B3  novel_llm_suggestion  (catalog miss — inner_race_fault has no SOP)
  A4 / B7  blocked_no_part       (gearbox, SKF22318-E out of stock)
  A5       blocked_invalid_input (validator catches bad upstream data)

Normal scenarios:
  B1  lubrication_issue, motor, iso_stage=0 (monitor)
  B2  outer_race_fault, motor, iso_stage=3 (critical)
  B4  lubrication_issue, pump, iso_stage=2 (high)

Partial-data scenarios:
  A6  low confidence, wide RUL — pipeline runs but flags uncertainty
  A7  None confidence + None RUL — safe defaults applied, pipeline runs
"""
import unittest

from src.schemas.diagnosis import FaultDiagnosis
from src.schemas.knowledge import KnowledgeGuidance
from src.schemas.risk import RiskAssessment
from src.agents.prescriptive_optimization_agent import recommend_action
from src.tools.inventory_checker import check_part_for_action
from src.tools.decision_logger import log_recommendation, get_history
from src.tools.poa_input_validator import validate_inputs
from src.tools.uncertainty_detector import apply_safe_defaults, detect_uncertainty


# ── Shared test fixtures ─────────────────────────────────────────────────────

def _diag(case_id, asset_id, bearing_id, fault_mode, iso_stage, severity,
          confidence=0.90, fault_code="FT_001", evidence=None):
    return FaultDiagnosis(
        case_id=case_id,
        asset_id=asset_id,
        bearing_id=bearing_id,
        fault_code=fault_code,
        fault_mode=fault_mode,
        iso_stage=iso_stage,
        severity=severity,
        confidence=confidence,
        evidence=evidence or {},
    )


def _risk(case_id, asset_id, bearing_id, risk_level, rul_min, rul_max,
          failure_probability=0.85, business_impact_flag=False):
    return RiskAssessment(
        case_id=case_id,
        asset_id=asset_id,
        bearing_id=bearing_id,
        failure_probability=failure_probability,
        risk_level=risk_level,
        rul_min_days=rul_min,
        rul_max_days=rul_max,
        business_impact_flag=business_impact_flag,
    )


def _guidance(case_id, source_documents=None, inspection_steps=None, safety_notes=None):
    return KnowledgeGuidance(
        case_id=case_id,
        source_documents=source_documents or [],
        inspection_steps=inspection_steps or [],
        safety_notes=safety_notes or [],
    )


# ── Guard tests ───────────────────────────────────────────────────────────────

class TestGuardUnknownAsset(unittest.TestCase):
    """A1 / B6 — asset not in master data → blocked_unknown_asset"""

    def _run(self, case_id, asset_id, bearing_id):
        d = _diag(case_id, asset_id, bearing_id, "outer_race_fault", 3, "critical")
        r = _risk(case_id, asset_id, bearing_id, "high", 5, 10, business_impact_flag=True)
        g = _guidance(case_id)
        return recommend_action(d, r, g)

    def test_a1_fake_asset(self):
        rec = self._run("G-001", "AST_FAKE", "BRG_FAKE")
        self.assertEqual(rec.recommendation_status, "blocked_unknown_asset")
        self.assertEqual(rec.approval_status, "escalated")

    def test_b6_unknown_asset(self):
        rec = self._run("DEMO-006", "AST_UNKNOWN_001", "BRG_UNKNOWN")
        self.assertEqual(rec.recommendation_status, "blocked_unknown_asset")
        self.assertEqual(rec.approval_status, "escalated")


class TestGuardUnreliableDiagnosis(unittest.TestCase):
    """A2 / B5 — confidence below threshold → unreliable_diagnosis"""

    def test_a2_low_confidence(self):
        d = _diag("G-002", "AST_MTR_001", "BRG_001", "outer_race_fault", 2, "high",
                  confidence=0.30)
        r = _risk("G-002", "AST_MTR_001", "BRG_001", "medium", 20, 30,
                  failure_probability=0.40)
        g = _guidance("G-002")
        rec = recommend_action(d, r, g)
        self.assertEqual(rec.recommendation_status, "unreliable_diagnosis")
        self.assertEqual(rec.approval_status, "escalated")

    def test_b5_signal_dropout(self):
        d = _diag("DEMO-005", "AST_CON_001", "BRG_009", "outer_race_fault", 0, "low",
                  confidence=0.15)
        r = _risk("DEMO-005", "AST_CON_001", "BRG_009", "low", 30, 60,
                  failure_probability=0.20)
        g = _guidance("DEMO-005")
        rec = recommend_action(d, r, g)
        self.assertEqual(rec.recommendation_status, "unreliable_diagnosis")

    def test_boundary_just_below_threshold(self):
        # Confidence = 0.49 — just below MIN_RELIABLE_CONFIDENCE (0.5)
        d = _diag("G-BOUND", "AST_MTR_001", "BRG_001", "outer_race_fault", 2, "high",
                  confidence=0.49)
        r = _risk("G-BOUND", "AST_MTR_001", "BRG_001", "medium", 15, 25)
        g = _guidance("G-BOUND")
        rec = recommend_action(d, r, g)
        self.assertEqual(rec.recommendation_status, "unreliable_diagnosis")

    def test_boundary_exactly_at_threshold(self):
        # Confidence = 0.5 — exactly at threshold → should NOT be blocked
        d = _diag("G-EXACT", "AST_MTR_001", "BRG_001", "outer_race_fault", 2, "high",
                  confidence=0.50)
        r = _risk("G-EXACT", "AST_MTR_001", "BRG_001", "medium", 15, 25)
        g = _guidance("G-EXACT")
        rec = recommend_action(d, r, g)
        self.assertNotEqual(rec.recommendation_status, "unreliable_diagnosis")


class TestGuardCatalogMiss(unittest.TestCase):
    """A3 / B3 — no SOP in catalog for this fault → novel_llm_suggestion"""

    def _run(self, case_id):
        # inner_race_fault has no entry in action_catalog.json
        d = _diag(case_id, "AST_MTR_001", "BRG_001", "inner_race_fault", 3, "critical",
                  confidence=0.85, fault_code="FT_002")
        r = _risk(case_id, "AST_MTR_001", "BRG_001", "high", 5, 8,
                  failure_probability=0.88, business_impact_flag=True)
        g = _guidance(case_id)
        return recommend_action(d, r, g)

    def test_a3_catalog_miss(self):
        rec = self._run("G-003")
        self.assertEqual(rec.recommendation_status, "novel_llm_suggestion")
        self.assertTrue(rec.is_llm_suggested)
        self.assertEqual(rec.approval_status, "escalated")

    def test_b3_catalog_miss_demo(self):
        rec = self._run("DEMO-003")
        self.assertEqual(rec.recommendation_status, "novel_llm_suggestion")
        self.assertTrue(rec.is_llm_suggested)
        # Recommended action should be a safe default (inspect_and_monitor on LLM failure)
        self.assertIsNotNone(rec.recommended_action.name)


class TestGuardBlockedNoPart(unittest.TestCase):
    """A4 / B7 — required part out of stock, lead time > RUL → blocked_no_part"""

    def _run(self, case_id):
        # BRG_011 (AST_GBX_001) → SKF22318-E is out of stock (qty_available=0)
        # lead_time=14 days > rul_min=5 days → blocked
        d = _diag(case_id, "AST_GBX_001", "BRG_011", "outer_race_fault", 3, "critical",
                  confidence=0.97)
        r = _risk(case_id, "AST_GBX_001", "BRG_011", "critical", 5, 7,
                  failure_probability=0.95, business_impact_flag=True)
        g = _guidance(case_id)
        return recommend_action(d, r, g)

    def test_a4_blocked_no_part(self):
        rec = self._run("G-004")
        self.assertEqual(rec.recommendation_status, "blocked_no_part")
        self.assertEqual(rec.approval_status, "escalated")

    def test_b7_gearbox_blocked(self):
        rec = self._run("DEMO-007")
        self.assertEqual(rec.recommendation_status, "blocked_no_part")
        # Part should be identified in required_parts (SKF22318-E)
        self.assertTrue(len(rec.required_parts) > 0)
        self.assertEqual(rec.required_parts[0].part_number, "SKF22318-E")


class TestGuardInvalidInput(unittest.TestCase):
    """A5 — validator catches out-of-range / mismatched upstream data"""

    def test_a5_invalid_upstream_data(self):
        # confidence=1.45 (>1), rul_min_days=-3, asset_id mismatch, bearing mismatch
        d = FaultDiagnosis.model_construct(
            case_id="DEMO-VAL-001",
            asset_id="AST_MTR_001",
            bearing_id="BRG_011",        # BRG_011 belongs to AST_GBX_001, not MTR_001
            fault_code="FT_001",
            fault_mode="outer_race_fault",
            iso_stage=3,
            severity="critical",
            confidence=1.45,             # out of range
        )
        r = RiskAssessment.model_construct(
            case_id="DEMO-VAL-001",
            asset_id="AST_PMP_001",      # mismatch — diagnosis says AST_MTR_001
            bearing_id="BRG_011",
            failure_probability=0.88,
            risk_level="critical",
            rul_min_days=-3,             # negative
            rul_max_days=7,
            business_impact_flag=True,
        )
        g = _guidance("DEMO-VAL-001")
        rec = recommend_action(d, r, g)
        self.assertEqual(rec.recommendation_status, "blocked_invalid_input")
        self.assertEqual(rec.approval_status, "escalated")

    def test_validator_passes_for_valid_input(self):
        d = _diag("V-OK", "AST_MTR_001", "BRG_001", "outer_race_fault", 3, "critical")
        r = _risk("V-OK", "AST_MTR_001", "BRG_001", "high", 5, 10)
        g = _guidance("V-OK")
        result = validate_inputs(d, r, g)
        self.assertTrue(result.valid)
        self.assertEqual(result.errors, [])


# ── Normal path tests ─────────────────────────────────────────────────────────

class TestNormalPath(unittest.TestCase):
    """B1, B2, B4 — normal pipeline, recommendation_status='ok'"""

    def test_b1_healthy_motor_monitor(self):
        # iso_stage=0 → "monitor" label → urgency should be "monitor"
        d = _diag("DEMO-001", "AST_MTR_001", "BRG_001", "lubrication_issue", 0, "low",
                  confidence=0.75, fault_code="FT_003")
        r = _risk("DEMO-001", "AST_MTR_001", "BRG_001", "low", 45, 60,
                  failure_probability=0.05)
        g = _guidance("DEMO-001")
        rec = recommend_action(d, r, g)
        self.assertEqual(rec.recommendation_status, "ok")
        self.assertFalse(rec.is_llm_suggested)
        self.assertEqual(rec.urgency, "monitor")

    def test_b2_outer_race_motor_stage3(self):
        d = _diag("DEMO-002", "AST_MTR_001", "BRG_001", "outer_race_fault", 3, "critical",
                  confidence=0.96)
        r = _risk("DEMO-002", "AST_MTR_001", "BRG_001", "critical", 5, 10,
                  failure_probability=0.92, business_impact_flag=True)
        g = _guidance("DEMO-002",
                      source_documents=["SOP_001_outer_race_motor_stage3.pdf"])
        rec = recommend_action(d, r, g)
        self.assertEqual(rec.recommendation_status, "ok")
        self.assertEqual(rec.recommended_action.name, "replace_bearing")
        self.assertFalse(rec.is_llm_suggested)
        self.assertIn(rec.urgency, ("urgent", "emergency", "planned"))
        # Parts should be identified (SKF6310-ZZ for BRG_001)
        self.assertTrue(len(rec.required_parts) > 0)

    def test_b4_lubrication_pump_stage2(self):
        d = _diag("DEMO-004", "AST_PMP_001", "BRG_005", "lubrication_issue", 2, "high",
                  confidence=0.88, fault_code="FT_003")
        r = _risk("DEMO-004", "AST_PMP_001", "BRG_005", "medium", 14, 20,
                  failure_probability=0.60)
        g = _guidance("DEMO-004",
                      source_documents=["SOP_003_Lubrication_Service.pdf"],
                      inspection_steps=["Isolate the pump", "Apply lubricant"],
                      safety_notes=["Lock out/tag out before servicing"])
        rec = recommend_action(d, r, g)
        self.assertEqual(rec.recommendation_status, "ok")
        self.assertEqual(rec.recommended_action.name, "lubrication_service")
        self.assertFalse(rec.is_llm_suggested)
        # Inspection steps should appear in rationale
        self.assertIn("Isolate the pump", rec.rationale)

    def test_recommendation_has_required_fields(self):
        d = _diag("DEMO-002B", "AST_MTR_001", "BRG_001", "outer_race_fault", 3, "critical",
                  confidence=0.90)
        r = _risk("DEMO-002B", "AST_MTR_001", "BRG_001", "high", 7, 14)
        g = _guidance("DEMO-002B")
        rec = recommend_action(d, r, g)
        self.assertEqual(rec.case_id, "DEMO-002B")
        self.assertEqual(rec.asset_id, "AST_MTR_001")
        self.assertIsNotNone(rec.recommended_action)
        self.assertIsNotNone(rec.rationale)
        self.assertGreater(len(rec.rationale), 20)
        self.assertIsNotNone(rec.responsible_approver)
        self.assertIsNotNone(rec.generated_at_utc)


# ── Partial-data / uncertainty tests ─────────────────────────────────────────

class TestPartialData(unittest.TestCase):
    """A6 / A7 — uncertainty detection and safe defaults"""

    def test_a6_low_confidence_wide_rul(self):
        # Low confidence (0.62) and wide RUL range (5–45) — pipeline should still run
        d = _diag("DEMO-PART-001", "AST_PMP_001", "BRG_005", "lubrication_issue", 2, "high",
                  confidence=0.62, fault_code="FT_003", evidence={})
        r = _risk("DEMO-PART-001", "AST_PMP_001", "BRG_005", "high", 5, 45,
                  failure_probability=0.55)
        g = _guidance("DEMO-PART-001")
        rec = recommend_action(d, r, g)
        # Should produce a recommendation (not blocked) despite low confidence > 0.5
        self.assertEqual(rec.recommendation_status, "ok")

    def test_a6_uncertainty_detection(self):
        d = _diag("U-001", "AST_PMP_001", "BRG_005", "lubrication_issue", 2, "high",
                  confidence=0.62, evidence={})
        r = _risk("U-001", "AST_PMP_001", "BRG_005", "high", 5, 45)
        result = detect_uncertainty(d, r)
        self.assertTrue(result.is_partial)
        # Should flag both low confidence and wide RUL
        flags_text = " ".join(result.flags)
        self.assertIn("confidence", flags_text.lower())
        self.assertIn("uncertainty", flags_text.lower())

    def test_a7_none_confidence_and_rul(self):
        # Missing confidence and RUL — safe defaults should be applied
        d = FaultDiagnosis.model_construct(
            case_id="DEMO-MISS-001",
            asset_id="AST_PMP_001",
            bearing_id="BRG_005",
            fault_code="FT_003",
            fault_mode="lubrication_issue",
            iso_stage=2,
            severity="high",
            confidence=None,
            evidence={},
        )
        r = RiskAssessment.model_construct(
            case_id="DEMO-MISS-001",
            asset_id="AST_PMP_001",
            bearing_id="BRG_005",
            failure_probability=None,
            risk_level=None,
            rul_min_days=None,
            rul_max_days=None,
            business_impact_flag=False,
        )
        diag_out, risk_out, defaulted = apply_safe_defaults(d, r)
        self.assertEqual(diag_out.confidence, 0.60)
        self.assertEqual(risk_out.rul_min_days, 14)
        self.assertEqual(risk_out.rul_max_days, 21)
        self.assertEqual(risk_out.risk_level, "medium")
        self.assertTrue(len(defaulted) > 0)


# ── Inventory checker tests ───────────────────────────────────────────────────

class TestInventoryChecker(unittest.TestCase):
    """Direct tests for check_part_for_action."""

    def test_in_stock_motor_bearing(self):
        # BRG_001 → SKF6310-ZZ qty_available=2
        result = check_part_for_action("BRG_001", "replace_bearing")
        self.assertEqual(result["status"], "in_stock")
        self.assertEqual(result["part_model"], "SKF6310-ZZ")
        self.assertGreater(result["quantity_available"], 0)

    def test_out_of_stock_gearbox_bearing(self):
        # BRG_011 → SKF22318-E qty_available=0
        result = check_part_for_action("BRG_011", "replace_bearing")
        self.assertEqual(result["status"], "out_of_stock")
        self.assertEqual(result["part_model"], "SKF22318-E")
        self.assertEqual(result["quantity_available"], 0)
        self.assertEqual(result["lead_time_days"], 14)

    def test_unknown_bearing(self):
        result = check_part_for_action("BRG_999", "replace_bearing")
        self.assertEqual(result["status"], "no_matching_part")

    def test_pump_lubricant_in_stock(self):
        # BRG_005 (pump) → MOBIL-DTE-25 lubricant qty_available=6
        result = check_part_for_action("BRG_005", "lubricate")
        self.assertEqual(result["status"], "in_stock")
        self.assertGreater(result["quantity_available"], 0)


# ── Decision logger tests ─────────────────────────────────────────────────────

class TestDecisionLogger(unittest.TestCase):
    """log_recommendation and get_history."""

    def _make_rec_and_context(self, case_id, asset_id, bearing_id,
                               fault_mode, iso_stage, severity, risk_level,
                               rul_min, rul_max):
        d = _diag(case_id, asset_id, bearing_id, fault_mode, iso_stage, severity)
        r = _risk(case_id, asset_id, bearing_id, risk_level, rul_min, rul_max)
        g = _guidance(case_id)
        rec = recommend_action(d, r, g)
        return rec, d, r

    def test_log_and_retrieve(self):
        rec, d, r = self._make_rec_and_context(
            "LOG-TEST-001", "AST_MTR_001", "BRG_001",
            "outer_race_fault", 3, "critical", "high", 7, 14,
        )
        log_id = log_recommendation(rec, d, r)
        self.assertTrue(log_id.startswith("LOG-"), f"Expected LOG- prefix, got: {log_id}")

    def test_log_id_unique_per_call(self):
        rec1, d1, r1 = self._make_rec_and_context(
            "LOG-TEST-002a", "AST_MTR_001", "BRG_001",
            "outer_race_fault", 3, "critical", "high", 7, 14,
        )
        rec2, d2, r2 = self._make_rec_and_context(
            "LOG-TEST-002b", "AST_PMP_001", "BRG_005",
            "lubrication_issue", 2, "high", "medium", 14, 20,
        )
        id1 = log_recommendation(rec1, d1, r1)
        id2 = log_recommendation(rec2, d2, r2)
        self.assertNotEqual(id1, id2)

    def test_get_history_filter_by_asset(self):
        # Log a motor rec so there's definitely one to retrieve
        rec, d, r = self._make_rec_and_context(
            "LOG-TEST-003", "AST_MTR_001", "BRG_001",
            "outer_race_fault", 3, "critical", "high", 7, 14,
        )
        log_recommendation(rec, d, r)
        history = get_history(asset_id="AST_MTR_001", limit=50)
        self.assertGreater(len(history), 0)
        for entry in history:
            self.assertEqual(entry["asset_id"], "AST_MTR_001")

    def test_get_history_sorted_newest_first(self):
        history = get_history(limit=20)
        if len(history) >= 2:
            self.assertGreaterEqual(
                history[0]["logged_at_utc"], history[-1]["logged_at_utc"]
            )


# ── Input validator tests ─────────────────────────────────────────────────────

class TestInputValidator(unittest.TestCase):

    def test_valid_input_passes(self):
        d = _diag("VAL-OK", "AST_MTR_001", "BRG_001", "outer_race_fault", 3, "critical")
        r = _risk("VAL-OK", "AST_MTR_001", "BRG_001", "high", 5, 10)
        g = _guidance("VAL-OK")
        result = validate_inputs(d, r, g)
        self.assertTrue(result.valid)

    def test_confidence_out_of_range(self):
        d = FaultDiagnosis.model_construct(
            case_id="VAL-ERR-1", asset_id="AST_MTR_001", bearing_id="BRG_001",
            fault_code="FT_001", fault_mode="outer_race_fault",
            iso_stage=3, severity="critical", confidence=1.45,
        )
        r = _risk("VAL-ERR-1", "AST_MTR_001", "BRG_001", "high", 5, 10)
        g = _guidance("VAL-ERR-1")
        result = validate_inputs(d, r, g)
        self.assertFalse(result.valid)
        self.assertTrue(any("confidence" in e for e in result.errors))

    def test_negative_rul(self):
        d = _diag("VAL-ERR-2", "AST_MTR_001", "BRG_001", "outer_race_fault", 3, "critical")
        r = RiskAssessment.model_construct(
            case_id="VAL-ERR-2", asset_id="AST_MTR_001", bearing_id="BRG_001",
            failure_probability=0.80, risk_level="critical",
            rul_min_days=-5, rul_max_days=10, business_impact_flag=False,
        )
        g = _guidance("VAL-ERR-2")
        result = validate_inputs(d, r, g)
        self.assertFalse(result.valid)
        self.assertTrue(any("negative" in e for e in result.errors))

    def test_asset_id_mismatch(self):
        d = _diag("VAL-ERR-3", "AST_MTR_001", "BRG_001", "outer_race_fault", 3, "critical")
        r = _risk("VAL-ERR-3", "AST_PMP_001", "BRG_001", "high", 5, 10)
        g = _guidance("VAL-ERR-3")
        result = validate_inputs(d, r, g)
        self.assertFalse(result.valid)
        self.assertTrue(any("mismatch" in e for e in result.errors))

    def test_invalid_fault_mode(self):
        d = FaultDiagnosis.model_construct(
            case_id="VAL-ERR-4", asset_id="AST_MTR_001", bearing_id="BRG_001",
            fault_code="FT_001", fault_mode="exploded",
            iso_stage=3, severity="critical", confidence=0.90,
        )
        r = _risk("VAL-ERR-4", "AST_MTR_001", "BRG_001", "high", 5, 10)
        g = _guidance("VAL-ERR-4")
        result = validate_inputs(d, r, g)
        self.assertFalse(result.valid)
        self.assertTrue(any("fault_mode" in e for e in result.errors))


if __name__ == "__main__":
    unittest.main()
