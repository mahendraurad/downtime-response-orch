"""Bounded response critic/refiner; never changes authoritative agent decisions."""
from copy import deepcopy
from typing import Any, Dict, List

from langsmith import traceable
from pydantic import BaseModel, Field


# ************** Added by Prateek Mittal on 20th July 2026 ******************
class ReflectionResult(BaseModel):
    response: Dict[str, Any] = Field(default_factory=dict)
    status: str = "accepted"  # accepted | refined | blocked
    issues: List[str] = Field(default_factory=list)
    source_agents: List[str] = Field(default_factory=list)
    llm_used: bool = False
    iterations: int = 0
    max_iterations: int = 3
    termination_reason: str = "accepted"


class ReflexionAgent:
    def __init__(self, llm_client=None, max_characters=4000, max_iterations=3):
        if int(max_iterations) < 1:
            raise ValueError("max_iterations must be at least 1")
        self._llm = llm_client
        self._max = max_characters
        self._max_iterations = int(max_iterations)

    @traceable(name="Reflexion Agent", run_type="chain", tags=["dro", "reflexion"])
    def process(self, draft: dict, state: dict = None, call_plan=None) -> ReflectionResult:
        if not isinstance(draft, dict):
            return ReflectionResult(
                status="blocked",
                issues=["response draft must be a dictionary"],
                max_iterations=self._max_iterations,
                termination_reason="blocked",
            )

        state = state or {}
        out = deepcopy(draft)
        key = "response" if "response" in out else "headline"
        all_issues = []
        iterations = 0
        termination_reason = "limit_reached"

        for iteration in range(1, self._max_iterations + 1):
            iterations = iteration
            pass_issues = []
            before = str(out.get(key) or "").strip()
            text = before

            if not text:
                pass_issues.append("empty response")
            if len(text) > self._max:
                text = text[:self._max].rstrip() + "..."
                pass_issues.append("response length capped")

            execution = state.get("execution_result")
            completion_claims = (
                "work order created",
                "parts reserved",
                "execution completed",
            )
            if any(claim in text.lower() for claim in completion_claims):
                if execution is None or execution.status not in {"success", "partial"}:
                    text = (
                        "Execution has not occurred. Human approval and a successful "
                        "Executor result are required."
                    )
                    pass_issues.append("unsupported execution claim removed")

            out[key] = text
            all_issues.extend(issue for issue in pass_issues if issue not in all_issues)

            if not pass_issues:
                termination_reason = "accepted" if iteration == 1 else "converged"
                break
            if text == before:
                termination_reason = "converged"
                break

        sources = []
        for node in state.get("pipeline_log", []):
            if node.get("status") != "error":
                sources.append(node.get("node", ""))
        out["sources"] = [source for source in dict.fromkeys(sources) if source]
        status = "refined" if all_issues else "accepted"
        out["reflection"] = {
            "status": status,
            "issues": all_issues,
            "iterations": iterations,
            "max_iterations": self._max_iterations,
            "termination_reason": termination_reason,
        }
        return ReflectionResult(
            response=out,
            status=status,
            issues=all_issues,
            source_agents=out["sources"],
            iterations=iterations,
            max_iterations=self._max_iterations,
            termination_reason=termination_reason,
        )
# ***********************
