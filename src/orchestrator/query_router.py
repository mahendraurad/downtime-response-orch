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
    # ── Conceptual / educational ──────────────────────────────────────────────
    ("concept", ("why vibration", "why temperature", "explain why vibration",
                 "vibration and temperature matter", "explain bpfo", "explain bpfi",
                 "bpfo in plain english", "bpfi different from bpfo",
                 "difference between bpfi", "what is anomaly", "what is bpfo", "what is bpfi",
                 "what is condition monitoring", "define anomaly"),
     "concept", (), False),

    # ── Fleet-wide questions ──────────────────────────────────────────────────
    ("fleet", ("which bearing", "which asset", "highest risk", "fleet",
               "across the plant", "anomalous equipment"), "fleet", (), False),

    # ── Learning history ──────────────────────────────────────────────────────
    ("learning_history", ("last 3 failures", "last three failures", "recent failures",
                          "failure history", "what did we learn", "learnings from",
                          "learned bearing cases", "learned cases"),
     "history", (8,), False),

    # ── Full pipeline — explicit analysis or work-order requests ─────────────
    ("execution", ("execute", "create work order", "create a work order",
                   "reserve part", "approve work"),
     "full", (1,2,3,4,5,6,7), True),

    # "Analyse M-104", "run full analysis", "full check" → all 8 agents + HITL
    ("analysis", ("analyse", "analyze", "run analysis", "full analysis",
                  "run orchestrated", "run the analysis", "full check",
                  "complete analysis", "technical analysis", "decision brief",
                  "run diagnostics", "run pipeline"),
     "full", (1,2,3,4,5,6,7), False),

    ("recommendation", ("recommend", "what should", "best action", "replace this",
                        "replace the bearing", "maintenance action", "maintenance plan",
                        "what to do", "next steps", "fix this", "action plan"),
     "full", (1,2,3,4,5,6), False),

    ("guidance", ("sop", "procedure", "inspection steps", "safety", "loto",
                  "how to fix", "how to repair", "maintenance steps"),
     "guidance", (1,2,3,4,5), False),

    # ── Risk / impact / timeline questions → agents 1-4 only ─────────────────
    # Covers: "production impact", "financial exposure", "can it run until",
    # "failure this week", "downtime cost", "safely operate", etc.
    ("risk", ("rul", "remaining useful life", "risk", "probability",
              "how long", "urgent", "impact", "production impact",
              "financial", "cost", "exposure", "consequence",
              "fail", "failure", "downtime", "shutdown",
              "when will", "safely run", "safely operate",
              "can it run", "can.*run", "this week", "this month",
              "how many days", "how much time", "time left",
              "critical", "run until", "wait until"),
     "risk", (1,2,3,4), False),

    # ── Fault identification → agents 1-3 ────────────────────────────────────
    ("diagnosis", ("fault", "diagnose", "root cause", "what type", "what kind",
                   "identify", "which fault", "bearing fault", "defect",
                   "symptom", "issue", "problem"),
     "diagnosis", (1,2,3), False),

    # ── Anomaly / alert check → agents 1-2 ───────────────────────────────────
    ("anomaly", ("anomaly", "anomalous", "alert", "normal", "any issue",
                 "triggered", "threshold", "any alert", "is there a problem"),
     "anomaly", (1,2), False),

    # ── Current readings → agent 1 only ──────────────────────────────────────
    ("status", ("status", "reading", "vibration", "temperature", "sensor",
                "current value", "what is the", "live data", "how is", "signal"),
     "status", (1,), False),
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
    # Fallback: unknown phrasing with a signal → risk depth (agents 1-4).
    # Avoids running the full 8-agent pipeline for unclassified conversational queries.
    return QueryPlan("general", "risk", ("reflexion",), False)
# ***********************
