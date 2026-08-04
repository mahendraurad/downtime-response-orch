"""Backend-owned dashboard projections derived from agent pipeline evidence."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone


def build_asset_dashboard(asset_rows, scenario_mapping, telemetry_loader,
                          pipeline_runner) -> dict:
    if isinstance(asset_rows, dict):
        asset_rows = asset_rows.values()
    masters = {}
    for row in asset_rows:
        asset_id = (
            row.asset_id if hasattr(row, "asset_id") else row.get("asset_id")
        )
        masters[asset_id] = row
    assets = []
    for display_id, (asset_id, scenario) in scenario_mapping.items():
        master = masters.get(asset_id)
        if master is None:
            continue
        master_data = (
            master.model_dump() if hasattr(master, "model_dump") else dict(master)
        )
        rows = telemetry_loader(scenario)
        if not rows:
            continue
        signal = deepcopy(rows[-1])
        state = pipeline_runner(signal, intent="risk")
        trusted = state.get("trusted_signal")
        risk = state.get("risk_assessment")
        diagnosis = state.get("fault_diagnosis")
        validation_status = str(
            getattr(trusted, "validation_status", "UNKNOWN")
        ).split(".")[-1]
        if risk is not None:
            status = (
                "critical" if risk.risk_level in {"critical", "high"}
                else "warning" if risk.risk_level == "medium" else "healthy"
            )
            rul = f"{risk.rul_min_days}-{risk.rul_max_days} days"
        elif validation_status == "FLAGGED":
            status, rul = "warning", "Data review"
        else:
            status, rul = "healthy", "Not currently estimated"
        trend = [
            row.get("vib_rms_mm_s") for row in rows
            if row.get("vib_rms_mm_s") is not None
        ][-12:]
        fault_rows = []
        if diagnosis is not None:
            fault_rows.append({
                "date": signal.get("timestamp_utc", ""),
                "type": diagnosis.fault_mode.replace("_", " "),
                "stage": f"Stage {diagnosis.iso_stage}",
                "status": "Active",
            })
        assets.append({
            "id": display_id,
            "asset_id": asset_id,
            "name": master_data["asset_name"],
            "type": master_data["asset_type"],
            "bearing_model": master_data["bearing_type"],
            "power_kw": master_data["power_kw"],
            "rpm": signal.get("rpm") or master_data["rated_rpm"],
            "line": master_data["production_line_id"],
            "status": status,
            "rul": rul,
            "vibration": signal.get("vib_rms_mm_s"),
            "temperature": signal.get("temp_c"),
            "bpfo": signal.get("bpfo_energy"),
            "iso_zone": master_data["iso_10816_zone"],
            "criticality": master_data["criticality"],
            "is_bottleneck": master_data["is_bottleneck"],
            "downtime_cost_per_hour": master_data["downtime_cost_per_hour"],
            "trend": trend,
            "faults": fault_rows,
            "validation_status": validation_status,
            "pipeline_log": state.get("pipeline_log", []),
            "source": "backend_agent_pipeline",
            "scenario": scenario,
        })
    return {
        "assets": assets,
        "fleet_total": len(assets),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": "backend_agent_pipeline",
    }
