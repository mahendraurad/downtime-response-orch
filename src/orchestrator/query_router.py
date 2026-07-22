"""Deterministic open-question intent and minimum-agent call planning."""
from dataclasses import dataclass

# ************** Added by Prateek Mittal on 20th July 2026 ******************
@dataclass(frozen=True)
class QueryPlan:
    intent: str
    pipeline_intent: str
    agents: tuple
    needs_signal: bool
    needs_approval: bool = False
    scenario: str = ""   # populated when a scenario entity is detected in the message

_RULES = [
    ("execution",      ("execute", "create work order", "reserve part", "approve work"), "full",      (1,2,3,4,5,6,7), True),
    ("recommendation", ("recommend", "what should", "best action", "maintenance plan"),  "full",      (1,2,3,4,5,6),   False),
    ("guidance",       ("sop", "procedure", "inspection steps", "safety", "loto"),       "full",      (1,2,3,4,5),     False),
    ("risk",           ("rul", "remaining useful life", "risk", "probability", "how long", "urgent"), "risk", (1,2,3,4), False),
    ("diagnosis",      ("fault", "diagnose", "root cause", "what type"),                 "diagnosis", (1,2,3),         False),
    ("anomaly",        ("anomaly", "alert", "normal", "any issue"),                      "anomaly",   (1,2),           False),
    ("status",         ("status", "reading", "vibration", "temperature", "sensor"),      "status",    (1,),            False),
]

# Maps natural-language trigger phrases to scenario names in telemetry_scenarios.json.
# This is the authoritative source — it is also referenced by the frontend as a
# fallback, but the backend checks it first so the frontend never needs to.
# Keys are matched case-insensitively as substrings of the incoming message.
_SCENARIO_TRIGGERS = {
    "low confidence":   "fi_hitl_test",
    "no sop":           "knowledge_hitl_test",
    "inner race":       "inner_race_fault",
    "unknown asset":    "unknown_asset",
    "cage fault":       "cage_fault",
    "e-501":            "electrical_anomaly",
    "thermal event":    "thermal_event",
    "borderline stage": "borderline_fault",
    "l-701":            "load_spike",
    "m-104":            "outer_race_fault",
    "p-207":            "lubrication_issue",
    "c-301":            "signal_dropout",
    "m-089":            "healthy",
    "g-112":            "gearbox_fault",
    "ast_mtr_001":      "outer_race_fault",
    "ast_pmp_001":      "lubrication_issue",
    "ast_mtr_002":      "healthy",
    "ast_gbx_001":      "gearbox_fault",
    "ast_pmp_002":      "lubrication_issue",
    "ast_con_001":      "signal_dropout",
    "conveyor motor a": "outer_race_fault",
    "cooling pump a":   "lubrication_issue",
    "conveyor motor b": "healthy",
    "gearbox unit":     "gearbox_fault",
}


def plan_query(message: str, has_signal: bool = False) -> QueryPlan:
    text = str(message or "").strip().lower()
    if not text:
        return QueryPlan("invalid", "status", (), False)

    # Entity detection: find a scenario trigger before checking intent keywords.
    detected_scenario = ""
    for trigger, scenario_name in _SCENARIO_TRIGGERS.items():
        if trigger in text:
            detected_scenario = scenario_name
            break

    # Intent detection: first matching rule wins.
    for intent, words, depth, agents, approval in _RULES:
        if any(word in text for word in words):
            return QueryPlan(intent, depth, tuple(f"agent_{n}" for n in agents),
                             True, approval, detected_scenario)

    # No intent keyword matched but a scenario entity was found — default to
    # full pipeline so the backend can still run it end-to-end.
    if detected_scenario:
        return QueryPlan("status", "full",
                         tuple(f"agent_{n}" for n in (1, 2, 3, 4, 5, 6)),
                         True, False, detected_scenario)

    # Open-ended plant/reliability questions — answered as general guidance.
    return QueryPlan("general", "full", ("reflexion",), False)
# ***********************
