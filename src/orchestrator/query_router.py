"""Deterministic open-question intent and minimum-agent call planning."""
from dataclasses import dataclass

# ************** Added by Prateek Mittal on 20th July 2026 ******************
@dataclass(frozen=True)
class QueryPlan:
    intent: str
    pipeline_intent: str
    agents: tuple[str, ...]
    needs_signal: bool
    needs_approval: bool = False

_RULES = [
    ("concept", ("why vibration", "why temperature", "explain why vibration"),
     "concept", (), False),
    ("fleet", ("which bearing", "which asset", "highest risk", "fleet",
               "across the plant", "anomalous equipment"), "fleet", (), False),
    ("learning_history", ("last 3 failures", "last three failures", "recent failures",
                          "failure history", "what did we learn", "learnings from"),
     "history", (8,), False),
    ("execution", ("execute", "create work order", "reserve part", "approve work"), "full", (1,2,3,4,5,6,7), True),
    ("recommendation", ("recommend", "what should", "best action", "replace this",
                        "replace the bearing", "maintenance action", "maintenance plan"), "full", (1,2,3,4,5,6), False),
    ("guidance", ("sop", "procedure", "inspection steps", "safety", "loto"), "guidance", (1,2,3,4,5), False),
    ("risk", ("rul", "remaining useful life", "risk", "probability", "how long", "urgent"), "risk", (1,2,3,4), False),
    ("diagnosis", ("fault", "diagnose", "root cause", "what type"), "diagnosis", (1,2,3), False),
    ("anomaly", ("anomaly", "anomalous", "alert", "normal", "any issue"), "anomaly", (1,2), False),
    ("status", ("status", "reading", "vibration", "temperature", "sensor"), "status", (1,), False),
]

def plan_query(message: str, has_signal: bool = False) -> QueryPlan:
    text = str(message or "").strip().lower()
    if not text:
        return QueryPlan("invalid", "status", (), False)
    normalized = text.rstrip("?!. ")
    if normalized in {"what is rul", "what does rul mean", "define rul",
                      "what does remaining useful life mean",
                      "explain remaining useful life"}:
        return QueryPlan("concept", "concept", (), False)
    for intent, words, depth, agents, approval in _RULES:
        if any(word in text for word in words):
            return QueryPlan(intent, depth, tuple(f"agent_{n}" for n in agents),
                             intent not in {"learning_history", "concept", "fleet"}, approval)
    # Open-ended plant/reliability questions without telemetry are answered as
    # general guidance with an explicit request for asset/signal context.
    return QueryPlan("general", "full", ("reflexion",), False)
# ***********************
