"""
scenarios.py
Single source of truth for the 11 demo scenarios.

Imported by app.py (UI) and tools/langgraph_tools.py (recommend_action_tool).
Each scenario holds pre-built Pydantic objects so there is no object
construction at call time in either consumer.
"""
from datetime import datetime, timezone

from schemas.diagnosis import FaultDiagnosis
from schemas.risk import RiskAssessment
from schemas.knowledge import KnowledgeGuidance

NOW = datetime(2026, 6, 5, 10, 0, 0, tzinfo=timezone.utc)

SCENARIOS = [
    {
        "label": "A1 — Unknown asset (blocked)",
        "diagnosis": FaultDiagnosis(
            case_id="G-001", asset_id="AST_FAKE", bearing_id="BRG_FAKE",
            fault_code="FT_001", fault_mode="outer_race_fault",
            affected_component="bearing_outer_race", severity="stage_3",
            confidence=0.90, diagnosed_at_utc=NOW,
        ),
        "risk": RiskAssessment(
            case_id="G-001", asset_id="AST_FAKE", bearing_id="BRG_FAKE",
            failure_probability=0.85, risk_level="high",
            rul_min_days=5, rul_max_days=10, confidence=0.85,
            business_impact_flag=True, estimated_downtime_cost_per_hour=5000.0,
            assessed_at_utc=NOW,
        ),
        "guidance": KnowledgeGuidance(case_id="G-001"),
    },
    {
        "label": "A2 — Unreliable diagnosis (low confidence)",
        "diagnosis": FaultDiagnosis(
            case_id="G-002", asset_id="AST_MTR_001", bearing_id="BRG_001",
            fault_code="FT_001", fault_mode="outer_race_fault",
            affected_component="bearing_outer_race", severity="stage_2",
            confidence=0.30, diagnosed_at_utc=NOW,
        ),
        "risk": RiskAssessment(
            case_id="G-002", asset_id="AST_MTR_001", bearing_id="BRG_001",
            failure_probability=0.40, risk_level="medium",
            rul_min_days=20, rul_max_days=30, confidence=0.30,
            business_impact_flag=False, estimated_downtime_cost_per_hour=12000.0,
            assessed_at_utc=NOW,
        ),
        "guidance": KnowledgeGuidance(case_id="G-002"),
    },
    {
        "label": "A3 — Catalog miss (inner_race_fault, no SOP)",
        "diagnosis": FaultDiagnosis(
            case_id="G-003", asset_id="AST_MTR_001", bearing_id="BRG_001",
            fault_code="FT_002", fault_mode="inner_race_fault",
            affected_component="bearing_inner_race", severity="stage_3",
            confidence=0.85, diagnosed_at_utc=NOW,
        ),
        "risk": RiskAssessment(
            case_id="G-003", asset_id="AST_MTR_001", bearing_id="BRG_001",
            failure_probability=0.88, risk_level="high",
            rul_min_days=5, rul_max_days=8, confidence=0.85,
            business_impact_flag=True, estimated_downtime_cost_per_hour=12000.0,
            assessed_at_utc=NOW,
        ),
        "guidance": KnowledgeGuidance(case_id="G-003"),
    },
    {
        "label": "A4 — Blocked on part (gearbox, SKF22318-E out of stock)",
        "diagnosis": FaultDiagnosis(
            case_id="G-004", asset_id="AST_GBX_001", bearing_id="BRG_011",
            fault_code="FT_001", fault_mode="outer_race_fault",
            affected_component="bearing_outer_race", severity="stage_3",
            confidence=0.97, diagnosed_at_utc=NOW,
        ),
        "risk": RiskAssessment(
            case_id="G-004", asset_id="AST_GBX_001", bearing_id="BRG_011",
            failure_probability=0.95, risk_level="critical",
            rul_min_days=5, rul_max_days=7, confidence=0.93,
            business_impact_flag=True, estimated_downtime_cost_per_hour=18000.0,
            assessed_at_utc=NOW,
        ),
        "guidance": KnowledgeGuidance(case_id="G-004"),
    },
    {
        "label": "B1 — Healthy motor (routine preventive service)",
        "diagnosis": FaultDiagnosis(
            case_id="DEMO-001", asset_id="AST_MTR_001", bearing_id="BRG_001",
            fault_code="FT_003", fault_mode="lubrication_issue",
            affected_component="lubrication_system", severity="monitor",
            confidence=0.75, diagnosed_at_utc=NOW,
        ),
        "risk": RiskAssessment(
            case_id="DEMO-001", asset_id="AST_MTR_001", bearing_id="BRG_001",
            failure_probability=0.05, risk_level="low",
            rul_min_days=45, rul_max_days=60, confidence=0.90,
            business_impact_flag=False, estimated_downtime_cost_per_hour=12000.0,
            assessed_at_utc=NOW,
        ),
        "guidance": KnowledgeGuidance(case_id="DEMO-001"),
    },
    {
        "label": "B2 — Outer race fault, motor stage_3",
        "diagnosis": FaultDiagnosis(
            case_id="DEMO-002", asset_id="AST_MTR_001", bearing_id="BRG_001",
            fault_code="FT_001", fault_mode="outer_race_fault",
            affected_component="bearing_outer_race", severity="stage_3",
            confidence=0.96, diagnosed_at_utc=NOW,
        ),
        "risk": RiskAssessment(
            case_id="DEMO-002", asset_id="AST_MTR_001", bearing_id="BRG_001",
            failure_probability=0.92, risk_level="critical",
            rul_min_days=5, rul_max_days=10, confidence=0.91,
            business_impact_flag=True, estimated_downtime_cost_per_hour=12000.0,
            assessed_at_utc=NOW,
        ),
        "guidance": KnowledgeGuidance(
            case_id="DEMO-002",
            source_documents=["SOP_001_outer_race_motor_stage3.pdf"],
        ),
    },
    {
        "label": "B3 — Inner race fault, motor stage_3 (catalog miss)",
        "diagnosis": FaultDiagnosis(
            case_id="DEMO-003", asset_id="AST_MTR_001", bearing_id="BRG_001",
            fault_code="FT_002", fault_mode="inner_race_fault",
            affected_component="bearing_inner_race", severity="stage_3",
            confidence=0.82, diagnosed_at_utc=NOW,
        ),
        "risk": RiskAssessment(
            case_id="DEMO-003", asset_id="AST_MTR_001", bearing_id="BRG_001",
            failure_probability=0.88, risk_level="high",
            rul_min_days=5, rul_max_days=8, confidence=0.85,
            business_impact_flag=True, estimated_downtime_cost_per_hour=12000.0,
            assessed_at_utc=NOW,
        ),
        "guidance": KnowledgeGuidance(case_id="DEMO-003"),
    },
    {
        "label": "B4 — Lubrication issue, pump stage_2",
        "diagnosis": FaultDiagnosis(
            case_id="DEMO-004", asset_id="AST_PMP_001", bearing_id="BRG_005",
            fault_code="FT_003", fault_mode="lubrication_issue",
            affected_component="lubrication_system", severity="stage_2",
            confidence=0.88, diagnosed_at_utc=NOW,
        ),
        "risk": RiskAssessment(
            case_id="DEMO-004", asset_id="AST_PMP_001", bearing_id="BRG_005",
            failure_probability=0.60, risk_level="medium",
            rul_min_days=14, rul_max_days=20, confidence=0.85,
            business_impact_flag=False, estimated_downtime_cost_per_hour=10000.0,
            assessed_at_utc=NOW,
        ),
        "guidance": KnowledgeGuidance(
            case_id="DEMO-004",
            source_documents=["SOP_003_Lubrication_Service.pdf"],
            relevant_sections=["3.2 Re-greasing procedure", "4.1 Lubricant specification"],
            inspection_steps=[
                "Isolate the pump and confirm zero energy state",
                "Remove old grease from the bearing housing",
                "Apply the specified lubricant to the correct fill level",
            ],
            safety_notes=[
                "Lock out/tag out before servicing",
                "Wear appropriate PPE",
            ],
        ),
    },
    {
        "label": "B5 — Signal dropout, conveyor (unreliable diagnosis)",
        "diagnosis": FaultDiagnosis(
            case_id="DEMO-005", asset_id="AST_CON_001", bearing_id="BRG_009",
            fault_code="FT_001", fault_mode="outer_race_fault",
            affected_component="bearing_outer_race", severity="monitor",
            confidence=0.15, diagnosed_at_utc=NOW,
        ),
        "risk": RiskAssessment(
            case_id="DEMO-005", asset_id="AST_CON_001", bearing_id="BRG_009",
            failure_probability=0.20, risk_level="low",
            rul_min_days=30, rul_max_days=60, confidence=0.20,
            business_impact_flag=False, estimated_downtime_cost_per_hour=15000.0,
            assessed_at_utc=NOW,
        ),
        "guidance": KnowledgeGuidance(case_id="DEMO-005"),
    },
    {
        "label": "B6 — Unknown asset (not in master data)",
        "diagnosis": FaultDiagnosis(
            case_id="DEMO-006", asset_id="AST_UNKNOWN_001", bearing_id="BRG_UNKNOWN",
            fault_code="FT_001", fault_mode="outer_race_fault",
            affected_component="bearing_outer_race", severity="stage_3",
            confidence=0.80, diagnosed_at_utc=NOW,
        ),
        "risk": RiskAssessment(
            case_id="DEMO-006", asset_id="AST_UNKNOWN_001", bearing_id="BRG_UNKNOWN",
            failure_probability=0.85, risk_level="high",
            rul_min_days=5, rul_max_days=10, confidence=0.80,
            business_impact_flag=False, estimated_downtime_cost_per_hour=0.0,
            assessed_at_utc=NOW,
        ),
        "guidance": KnowledgeGuidance(case_id="DEMO-006"),
    },
    {
        "label": "B7 — Gearbox fault, stage_3 (part out of stock)",
        "diagnosis": FaultDiagnosis(
            case_id="DEMO-007", asset_id="AST_GBX_001", bearing_id="BRG_011",
            fault_code="FT_001", fault_mode="outer_race_fault",
            affected_component="bearing_outer_race", severity="stage_3",
            confidence=0.97, diagnosed_at_utc=NOW,
        ),
        "risk": RiskAssessment(
            case_id="DEMO-007", asset_id="AST_GBX_001", bearing_id="BRG_011",
            failure_probability=0.95, risk_level="critical",
            rul_min_days=5, rul_max_days=7, confidence=0.93,
            business_impact_flag=True, estimated_downtime_cost_per_hour=18000.0,
            assessed_at_utc=NOW,
        ),
        "guidance": KnowledgeGuidance(
            case_id="DEMO-007",
            source_documents=["SOP_005_outer_race_gearbox_stage3.pdf"],
        ),
    },
    {
        "label": "A5 — Invalid upstream data (validation blocked)",
        # model_construct() bypasses Pydantic's own field validators so these
        # intentionally bad values (confidence > 1.0, negative rul_min_days) survive
        # import. agents/input_validator.py catches all three errors before the
        # pipeline runs.
        "description": (
            "Upstream agents sent inconsistent data — confidence out of range, "
            "asset/bearing mismatch, negative RUL. The validator catches all three "
            "errors before the pipeline runs and flags them for the orchestrator."
        ),
        "diagnosis": FaultDiagnosis.model_construct(
            case_id="DEMO-VAL-001",
            asset_id="AST_MTR_001",       # correct
            bearing_id="BRG_011",         # WRONG — BRG_011 belongs to gearbox, not AST_MTR_001
            fault_code="FT_001",
            fault_mode="outer_race_fault",
            affected_component="bearing_outer_race",
            severity="stage_3",
            confidence=1.45,              # WRONG — confidence > 1.0
            diagnosed_at_utc=NOW,
        ),
        "risk": RiskAssessment.model_construct(
            case_id="DEMO-VAL-001",
            asset_id="AST_PMP_001",       # WRONG — mismatch with diagnosis asset_id AST_MTR_001
            bearing_id="BRG_011",
            failure_probability=0.88,
            risk_level="critical",
            rul_min_days=-3,              # WRONG — negative RUL
            rul_max_days=7,
            confidence=0.85,
            business_impact_flag=True,
            estimated_downtime_cost_per_hour=12000.0,
            assessed_at_utc=NOW,
        ),
        "guidance": KnowledgeGuidance(case_id="DEMO-VAL-001"),
    },
    {
        "label": "A6 — Partial data (low confidence, wide RUL, no evidence)",
        "description": (
            "Upstream agents provided incomplete data — low diagnostic "
            "confidence, wide RUL range, and no supporting signal evidence. "
            "Pipeline runs on available data but flags the uncertainty."
        ),
        "diagnosis": FaultDiagnosis(
            case_id="DEMO-PART-001",
            asset_id="AST_PMP_001",
            bearing_id="BRG_005",
            fault_code="FT_003",
            fault_mode="lubrication_issue",
            affected_component="lubrication_system",
            severity="stage_2",
            confidence=0.62,
            evidence=[],
            diagnosed_at_utc=NOW,
        ),
        "risk": RiskAssessment(
            case_id="DEMO-PART-001",
            asset_id="AST_PMP_001",
            bearing_id="BRG_005",
            failure_probability=0.55,
            risk_level="high",
            rul_min_days=5,
            rul_max_days=45,
            confidence=0.62,
            business_impact_flag=False,
            estimated_downtime_cost_per_hour=10000.0,
            assessed_at_utc=NOW,
        ),
        "guidance": KnowledgeGuidance(case_id="DEMO-PART-001"),
    },
    {
        "label": "A7 — Missing RUL and confidence (partial upstream)",
        "description": (
            "Upstream risk agent failed to compute RUL and confidence. "
            "Agent 6.6 applies safe defaults and runs the pipeline, "
            "flagging every assumption made."
        ),
        "diagnosis": FaultDiagnosis.model_construct(
            case_id="DEMO-MISS-001",
            asset_id="AST_PMP_001",
            bearing_id="BRG_005",
            fault_code="FT_003",
            fault_mode="lubrication_issue",
            affected_component="lubrication_system",
            severity="stage_2",
            confidence=None,
            evidence=[],
            diagnosed_at_utc=NOW,
        ),
        "risk": RiskAssessment.model_construct(
            case_id="DEMO-MISS-001",
            asset_id="AST_PMP_001",
            bearing_id="BRG_005",
            failure_probability=None,
            risk_level=None,
            rul_min_days=None,
            rul_max_days=None,
            confidence=None,
            business_impact_flag=False,
            estimated_downtime_cost_per_hour=10000.0,
            assessed_at_utc=NOW,
        ),
        "guidance": KnowledgeGuidance(case_id="DEMO-MISS-001"),
    },
]

SCENARIO_LABELS = [s["label"] for s in SCENARIOS]
SCENARIO_MAP    = {s["label"]: s for s in SCENARIOS}
