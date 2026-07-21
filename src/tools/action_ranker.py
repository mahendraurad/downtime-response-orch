"""
tools/action_ranker.py  —  Phase 7

Ranks candidate maintenance actions by combined urgency and feasibility score.
"""


def rank_actions(candidates: list[dict]) -> list[dict]:
    """
    Compute final_score for each candidate and return the list sorted highest-first.

    Each candidate dict must have:
      urgency_score     - float 0-1: how badly the situation demands this action
      feasibility_score - float 0-1: whether the action can actually be done

    final_score = urgency_score * feasibility_score

    Multiplication rather than addition ensures an impossible action (feasibility
    near 0) scores near 0 no matter how urgent it is. The agent must never
    recommend something it cannot do.
    """
    if not candidates:
        return []

    for candidate in candidates:
        urgency = candidate.get("urgency_score", 0)
        feasibility = candidate.get("feasibility_score", 0)
        candidate["final_score"] = urgency * feasibility

    return sorted(candidates, key=lambda c: c["final_score"], reverse=True)
