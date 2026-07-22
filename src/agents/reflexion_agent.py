"""Bounded response critic/refiner; never changes authoritative agent decisions."""
from copy import deepcopy
from pydantic import BaseModel, Field
from typing import Any, Dict, List
from langsmith import traceable

# ************** Added by Prateek Mittal on 20th July 2026 ******************
class ReflectionResult(BaseModel):
    response: Dict[str, Any] = Field(default_factory=dict)
    status: str = "accepted"  # accepted | refined | blocked
    issues: List[str] = Field(default_factory=list)
    source_agents: List[str] = Field(default_factory=list)
    llm_used: bool = False

class ReflexionAgent:
    def __init__(self, llm_client=None, max_characters=4000):
        self._llm=llm_client; self._max=max_characters
    @traceable(name="Reflexion Agent", run_type="chain", tags=["dro", "reflexion"])
    def process(self, draft: dict, state: dict = None, call_plan=None) -> ReflectionResult:
        if not isinstance(draft, dict):
            return ReflectionResult(status="blocked", issues=["response draft must be a dictionary"])
        state=state or {}; out=deepcopy(draft); issues=[]
        text=str(out.get("response") or out.get("headline") or "").strip()
        if not text: issues.append("empty response")
        if len(text)>self._max:
            text=text[:self._max].rstrip()+"…"; issues.append("response length capped")
        # Never present execution as completed unless Agent 7 produced success/partial.
        execution=state.get("execution_result")
        if any(word in text.lower() for word in ("work order created","parts reserved","execution completed")):
            if execution is None or execution.status not in {"success","partial"}:
                text="Execution has not occurred. Human approval and a successful Executor result are required."
                issues.append("unsupported execution claim removed")
        key="response" if "response" in out else "headline"; out[key]=text
        sources=[]
        for node in state.get("pipeline_log",[]):
            if node.get("status") not in {"error"}: sources.append(node.get("node",""))
        out["sources"]=[s for s in dict.fromkeys(sources) if s]
        out["reflection"]={"status":"refined" if issues else "accepted","issues":issues}
        return ReflectionResult(response=out,status="refined" if issues else "accepted",issues=issues,source_agents=out["sources"])
# ***********************
