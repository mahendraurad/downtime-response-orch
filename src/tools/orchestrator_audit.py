"""Append-only JSONL orchestration audit utility."""
import json
from datetime import datetime, timezone
from pathlib import Path

# ************** Added by Prateek Mittal on 20th July 2026 ******************
def write_audit(path, *, event, run_id="", status="", intent="", agents=None, error_code=""):
    row={"timestamp_utc":datetime.now(timezone.utc).isoformat(),"event":event,"run_id":run_id,
         "status":status,"intent":intent,"agents":list(agents or []),"error_code":error_code}
    target=Path(path); target.parent.mkdir(parents=True,exist_ok=True)
    with target.open("a",encoding="utf-8") as fh: fh.write(json.dumps(row,sort_keys=True)+"\n")
    return row
# ***********************
