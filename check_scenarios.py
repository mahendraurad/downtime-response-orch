# Standalone scenario runner — mirrors the app's pipeline path (no Streamlit).
# Run: python check_scenarios.py
# Patch rationale to template mode BEFORE importing the agent (avoids Azure call).
import tools.rationale_writer as _rw
_orig = _rw.write_rationale
_rw.write_rationale = lambda decision, use_llm=False: _orig(decision, use_llm=False)

from scenarios import SCENARIO_MAP
from agents.uncertainty_detector import apply_safe_defaults
from agents.prescriptive_optimization_agent import recommend_action

# The scenarios that exercise the parts path (the refactor's blast radius).
# Adjust labels if they differ — run once and it'll tell you valid labels.
WANTED = [
    "B4",   # clean pump lubrication  → lubrication_service
    "B2",   # outer race motor stage_3 → replace_bearing
    "B6",   # unknown asset → blocked_invalid_input
    "B7",   # gearbox out-of-stock → blocked_no_part
    "B3",   # inner_race_fault, no SOP → novel_llm_suggestion
]

def _match_label(frag):
    for label in SCENARIO_MAP:
        if label.startswith(frag) or frag in label:
            return label
    return None

def run(frag):
    label = _match_label(frag)
    if not label:
        print(f"\n### {frag}: NO MATCHING LABEL. Available labels:")
        for l in SCENARIO_MAP: print("   -", l)
        return
    scn = SCENARIO_MAP[label]
    diag, risk, guidance = scn["diagnosis"], scn["risk"], scn["guidance"]
    diag2, risk2, _defaulted = apply_safe_defaults(diag, risk)
    rec = recommend_action(diag2, risk2, guidance)

    print(f"\n=== {label} ===")
    print(f"  status      : {rec.recommendation_status}")
    print(f"  action      : {rec.recommended_action.name}")
    print(f"  urgency     : {rec.urgency}")
    print(f"  approval    : {rec.approval_status}")
    print(f"  approver    : {rec.responsible_approver}")
    print(f"  window      : {rec.window_chosen}")
    parts = [f"{p.part_number} (lead {p.lead_time_days}d)" for p in rec.required_parts]
    print(f"  parts       : {parts}")
    alts = [a.name for a in rec.ranked_alternatives]
    print(f"  alternatives: {alts}")

if __name__ == "__main__":
    for frag in WANTED:
        run(frag)
    print("\n(done)")
