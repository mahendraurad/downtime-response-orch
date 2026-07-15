"""
Agent 6.6 — full test suite.
Run from project root: python -m tests.demo_scenarios

Covers:
  Part A — 4 guard / failure cases (agent must fail loudly, never silently)
  Part B — 7 telemetry scenarios (real-world fault handling)
"""
from datetime import datetime, timezone
from schemas.diagnosis import FaultDiagnosis
from schemas.risk import RiskAssessment
from schemas.knowledge import KnowledgeGuidance
from agents.prescriptive_optimization_agent import recommend_action

NOW = datetime(2026, 6, 5, 10, 0, 0, tzinfo=timezone.utc)


def run(label, expected, diagnosis, risk, guidance):
    """Run one test case and print results. Catches errors so others keep running."""
    print(f"\n{'='*62}")
    print(f"  {label}")
    print(f"  Expected : {expected}")
    print(f"  {'-'*58}")
    try:
        rec = recommend_action(diagnosis, risk, guidance)
        action = rec.recommended_action.name
        status = rec.recommendation_status
        is_fallback = status != "ok"
        tag = f"[{status.upper()}]" if is_fallback else "[OK - REAL RECOMMENDATION]"
        print(f"  {tag}")
        print(f"  status   : {status}")
        print(f"  action   : {action}")
        print(f"  urgency  : {rec.urgency}")
        print(f"  approval : {rec.approval_status}")
        parts = [p.part_number for p in rec.required_parts]
        print(f"  parts    : {parts if parts else '[]'}")
        print(f"  window   : {rec.window_chosen if rec.window_chosen else 'None'}")
        print(f"  person   : {rec.responsible_person} ({rec.responsible_person_id})")
        if rec.ranked_alternatives:
            print(f"  alts ({len(rec.ranked_alternatives)}):")
            for alt in rec.ranked_alternatives:
                print(f"      - {alt.description}  [{alt.estimated_duration_hours}h]")
        else:
            print(f"  alts     : none")
        print(f"  rationale: {rec.rationale}")
    except Exception as e:
        print(f"  [ERROR] {type(e).__name__}: {e}")


# ===========================================================================
# PART A — Guard / failure cases
# ===========================================================================

print("\n" + "#" * 62)
print("# PART A — Guard / failure cases")
print("#" * 62)

run(
    label="A1. Unknown asset",
    expected="blocked_unknown_asset — asset not in master data",
    diagnosis=FaultDiagnosis(
        case_id="G-001", asset_id="AST_FAKE", bearing_id="BRG_FAKE",
        fault_code="FT_001", fault_mode="outer_race_fault",
        affected_component="bearing_outer_race", severity="stage_3",
        confidence=0.90, diagnosed_at_utc=NOW,
    ),
    risk=RiskAssessment(
        case_id="G-001", asset_id="AST_FAKE", bearing_id="BRG_FAKE",
        failure_probability=0.85, risk_level="high",
        rul_min_days=5, rul_max_days=10, confidence=0.85,
        business_impact_flag=True, estimated_downtime_cost_per_hour=5000.0,
        assessed_at_utc=NOW,
    ),
    guidance=KnowledgeGuidance(case_id="G-001"),
)

run(
    label="A2. Low confidence / unreliable diagnosis",
    expected="unreliable_diagnosis — confidence 0.30 below threshold 0.5",
    diagnosis=FaultDiagnosis(
        case_id="G-002", asset_id="AST_MTR_001", bearing_id="BRG_001",
        fault_code="FT_001", fault_mode="outer_race_fault",
        affected_component="bearing_outer_race", severity="stage_2",
        confidence=0.30, diagnosed_at_utc=NOW,
    ),
    risk=RiskAssessment(
        case_id="G-002", asset_id="AST_MTR_001", bearing_id="BRG_001",
        failure_probability=0.40, risk_level="medium",
        rul_min_days=20, rul_max_days=30, confidence=0.30,
        business_impact_flag=False, estimated_downtime_cost_per_hour=12000.0,
        assessed_at_utc=NOW,
    ),
    guidance=KnowledgeGuidance(case_id="G-002"),
)

run(
    label="A3. Catalog miss (inner_race_fault has no SOP)",
    expected="novel_llm_suggestion — no approved procedure; LLM proposes a tentative action, needs human validation",
    diagnosis=FaultDiagnosis(
        case_id="G-003", asset_id="AST_MTR_001", bearing_id="BRG_001",
        fault_code="FT_002", fault_mode="inner_race_fault",
        affected_component="bearing_inner_race", severity="stage_3",
        confidence=0.85, diagnosed_at_utc=NOW,
    ),
    risk=RiskAssessment(
        case_id="G-003", asset_id="AST_MTR_001", bearing_id="BRG_001",
        failure_probability=0.88, risk_level="high",
        rul_min_days=5, rul_max_days=8, confidence=0.85,
        business_impact_flag=True, estimated_downtime_cost_per_hour=12000.0,
        assessed_at_utc=NOW,
    ),
    guidance=KnowledgeGuidance(case_id="G-003"),
)

run(
    label="A4. Blocked on part (gearbox SKF22318-E out of stock, lead 14 > rul 5)",
    expected="blocked_no_part — SKF22318-E listed, urgency emergency",
    diagnosis=FaultDiagnosis(
        case_id="G-004", asset_id="AST_GBX_001", bearing_id="BRG_011",
        fault_code="FT_001", fault_mode="outer_race_fault",
        affected_component="bearing_outer_race", severity="stage_3",
        confidence=0.97, diagnosed_at_utc=NOW,
    ),
    risk=RiskAssessment(
        case_id="G-004", asset_id="AST_GBX_001", bearing_id="BRG_011",
        failure_probability=0.95, risk_level="critical",
        rul_min_days=5, rul_max_days=7, confidence=0.93,
        business_impact_flag=True, estimated_downtime_cost_per_hour=18000.0,
        assessed_at_utc=NOW,
    ),
    guidance=KnowledgeGuidance(case_id="G-004"),
)

# ===========================================================================
# PART B — 7 telemetry scenarios
# ===========================================================================

print("\n" + "#" * 62)
print("# PART B — 7 telemetry scenarios")
print("#" * 62)

run(
    label="B1. Healthy motor — routine preventive service (AST_MTR_001 / BRG_001)",
    expected="preventive_maintenance, urgency=monitor, approval=False",
    diagnosis=FaultDiagnosis(
        case_id="DEMO-001", asset_id="AST_MTR_001", bearing_id="BRG_001",
        fault_code="FT_003", fault_mode="lubrication_issue",
        affected_component="lubrication_system", severity="monitor",
        confidence=0.75, diagnosed_at_utc=NOW,
    ),
    risk=RiskAssessment(
        case_id="DEMO-001", asset_id="AST_MTR_001", bearing_id="BRG_001",
        failure_probability=0.05, risk_level="low",
        rul_min_days=45, rul_max_days=60, confidence=0.90,
        business_impact_flag=False, estimated_downtime_cost_per_hour=12000.0,
        assessed_at_utc=NOW,
    ),
    guidance=KnowledgeGuidance(case_id="DEMO-001"),
)

run(
    label="B2. Outer race fault — motor stage_3 (AST_MTR_001 / BRG_001)",
    expected="replace_bearing, urgent, SKF6310-ZZ in stock",
    diagnosis=FaultDiagnosis(
        case_id="DEMO-002", asset_id="AST_MTR_001", bearing_id="BRG_001",
        fault_code="FT_001", fault_mode="outer_race_fault",
        affected_component="bearing_outer_race", severity="stage_3",
        confidence=0.96, diagnosed_at_utc=NOW,
    ),
    risk=RiskAssessment(
        case_id="DEMO-002", asset_id="AST_MTR_001", bearing_id="BRG_001",
        failure_probability=0.92, risk_level="critical",
        rul_min_days=5, rul_max_days=10, confidence=0.91,
        business_impact_flag=True, estimated_downtime_cost_per_hour=12000.0,
        assessed_at_utc=NOW,
    ),
    guidance=KnowledgeGuidance(
        case_id="DEMO-002",
        source_documents=["SOP_001_outer_race_motor_stage3.pdf"],
    ),
)

run(
    label="B3. Inner race fault — motor stage_3 (no SOP)",
    expected="novel_llm_suggestion — no catalog entry; LLM proposes a tentative action, needs human validation",
    diagnosis=FaultDiagnosis(
        case_id="DEMO-003", asset_id="AST_MTR_001", bearing_id="BRG_001",
        fault_code="FT_002", fault_mode="inner_race_fault",
        affected_component="bearing_inner_race", severity="stage_3",
        confidence=0.82, diagnosed_at_utc=NOW,
    ),
    risk=RiskAssessment(
        case_id="DEMO-003", asset_id="AST_MTR_001", bearing_id="BRG_001",
        failure_probability=0.88, risk_level="high",
        rul_min_days=5, rul_max_days=8, confidence=0.85,
        business_impact_flag=True, estimated_downtime_cost_per_hour=12000.0,
        assessed_at_utc=NOW,
    ),
    guidance=KnowledgeGuidance(case_id="DEMO-003"),
)

run(
    label="B4. Lubrication issue — pump stage_2 (AST_PMP_001 / BRG_005)",
    expected="lubrication_service, ok, MOBIL-DTE-25 in stock, NO escalation",
    diagnosis=FaultDiagnosis(
        case_id="DEMO-004", asset_id="AST_PMP_001", bearing_id="BRG_005",
        fault_code="FT_003", fault_mode="lubrication_issue",
        affected_component="lubrication_system", severity="stage_2",
        confidence=0.88, diagnosed_at_utc=NOW,
    ),
    risk=RiskAssessment(
        case_id="DEMO-004", asset_id="AST_PMP_001", bearing_id="BRG_005",
        failure_probability=0.60, risk_level="medium",
        rul_min_days=14, rul_max_days=20, confidence=0.85,
        business_impact_flag=False, estimated_downtime_cost_per_hour=10000.0,
        assessed_at_utc=NOW,
    ),
    guidance=KnowledgeGuidance(
        case_id="DEMO-004",
        source_documents=["SOP_003_lubrication_pump_stage2.pdf"],
    ),
)

run(
    label="B5. Signal dropout — conveyor (bad sensor data, low confidence)",
    expected="unreliable_diagnosis — confidence 0.15 below threshold",
    diagnosis=FaultDiagnosis(
        case_id="DEMO-005", asset_id="AST_CON_001", bearing_id="BRG_009",
        fault_code="FT_001", fault_mode="outer_race_fault",
        affected_component="bearing_outer_race", severity="monitor",
        confidence=0.15, diagnosed_at_utc=NOW,
    ),
    risk=RiskAssessment(
        case_id="DEMO-005", asset_id="AST_CON_001", bearing_id="BRG_009",
        failure_probability=0.20, risk_level="low",
        rul_min_days=30, rul_max_days=60, confidence=0.20,
        business_impact_flag=False, estimated_downtime_cost_per_hour=15000.0,
        assessed_at_utc=NOW,
    ),
    guidance=KnowledgeGuidance(case_id="DEMO-005"),
)

run(
    label="B6. Unknown asset (not in master data)",
    expected="blocked_unknown_asset — graceful, no crash",
    diagnosis=FaultDiagnosis(
        case_id="DEMO-006", asset_id="AST_UNKNOWN_001", bearing_id="BRG_UNKNOWN",
        fault_code="FT_001", fault_mode="outer_race_fault",
        affected_component="bearing_outer_race", severity="stage_3",
        confidence=0.80, diagnosed_at_utc=NOW,
    ),
    risk=RiskAssessment(
        case_id="DEMO-006", asset_id="AST_UNKNOWN_001", bearing_id="BRG_UNKNOWN",
        failure_probability=0.85, risk_level="high",
        rul_min_days=5, rul_max_days=10, confidence=0.80,
        business_impact_flag=False, estimated_downtime_cost_per_hour=0.0,
        assessed_at_utc=NOW,
    ),
    guidance=KnowledgeGuidance(case_id="DEMO-006"),
)

run(
    label="B7. Gearbox fault — stage_3, part out of stock (AST_GBX_001 / BRG_011)",
    expected="blocked_no_part — SKF22318-E listed, lead 14 > rul 5, urgency emergency",
    diagnosis=FaultDiagnosis(
        case_id="DEMO-007", asset_id="AST_GBX_001", bearing_id="BRG_011",
        fault_code="FT_001", fault_mode="outer_race_fault",
        affected_component="bearing_outer_race", severity="stage_3",
        confidence=0.97, diagnosed_at_utc=NOW,
    ),
    risk=RiskAssessment(
        case_id="DEMO-007", asset_id="AST_GBX_001", bearing_id="BRG_011",
        failure_probability=0.95, risk_level="critical",
        rul_min_days=5, rul_max_days=7, confidence=0.93,
        business_impact_flag=True, estimated_downtime_cost_per_hour=18000.0,
        assessed_at_utc=NOW,
    ),
    guidance=KnowledgeGuidance(
        case_id="DEMO-007",
        source_documents=["SOP_005_outer_race_gearbox_stage3.pdf"],
    ),
)

print(f"\n{'#'*62}")
print("# All 11 tests complete.")
print("#" * 62)
