"""Generate the deterministic extended demo fleet from one editable config.

The six original assets and their established scenarios are preserved. Assets
whose IDs start with ``AST_SYN_`` and scenarios whose names start with
``fleet_`` are regenerated, making this command safe to repeat.
"""
from __future__ import annotations

import json
import math
import sys
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "synthetic_fleet_config.json"
DATA_DIR = ROOT / "data"

BASE_MAPPINGS = [
    {"display_asset_id": "M-104", "asset_id": "AST_MTR_001", "scenario": "outer_race_fault", "profile": "outer_stage3"},
    {"display_asset_id": "M-089", "asset_id": "AST_MTR_002", "scenario": "healthy", "profile": "healthy"},
    {"display_asset_id": "P-207", "asset_id": "AST_PMP_001", "scenario": "lubrication_issue", "profile": "lubrication_stage2"},
    {"display_asset_id": "P-208", "asset_id": "AST_PMP_002", "scenario": "borderline_fault", "profile": "borderline"},
    {"display_asset_id": "C-301", "asset_id": "AST_CON_001", "scenario": "signal_dropout", "profile": "signal_dropout"},
    {"display_asset_id": "G-112", "asset_id": "AST_GBX_001", "scenario": "fleet_g_112_post_repair", "profile": "healthy"},
]

PROFILE_TARGETS = {
    "healthy": {"vib_ratio": 1.04, "kurtosis": 2.1, "temp_rise": 2, "bpfo": 0.75, "bpfi": 0.65, "bsf": 0.45, "ftf": 0.30},
    "outer_stage1": {"vib_ratio": 1.75, "kurtosis": 5.4, "temp_rise": 9, "bpfo": 1.8, "bpfi": 0.7, "bsf": 0.5, "ftf": 0.3},
    "outer_stage2": {"vib_ratio": 2.65, "kurtosis": 6.1, "temp_rise": 16, "bpfo": 2.8, "bpfi": 0.8, "bsf": 0.6, "ftf": 0.4},
    "outer_stage3": {"vib_ratio": 3.85, "kurtosis": 7.2, "temp_rise": 27, "bpfo": 4.2, "bpfi": 0.9, "bsf": 0.7, "ftf": 0.5},
    "inner_stage1": {"vib_ratio": 1.75, "kurtosis": 5.9, "temp_rise": 9, "bpfo": 0.8, "bpfi": 1.8, "bsf": 0.5, "ftf": 0.3},
    "inner_stage2": {"vib_ratio": 2.70, "kurtosis": 6.5, "temp_rise": 17, "bpfo": 0.9, "bpfi": 2.8, "bsf": 0.6, "ftf": 0.4},
    "lubrication_stage1": {"vib_ratio": 1.45, "kurtosis": 3.0, "temp_rise": 12, "bpfo": 0.9, "bpfi": 0.85, "bsf": 0.75, "ftf": 0.65},
    "lubrication_stage2": {"vib_ratio": 2.20, "kurtosis": 3.2, "temp_rise": 21, "bpfo": 1.0, "bpfi": 0.95, "bsf": 0.85, "ftf": 0.75},
    "lubrication_stage3": {"vib_ratio": 3.25, "kurtosis": 3.4, "temp_rise": 32, "bpfo": 1.1, "bpfi": 1.0, "bsf": 0.9, "ftf": 0.8},
    "cage_stage2": {"vib_ratio": 2.35, "kurtosis": 5.3, "temp_rise": 12, "bpfo": 0.8, "bpfi": 0.75, "bsf": 1.1, "ftf": 2.3},
    "cage_stage3": {"vib_ratio": 3.70, "kurtosis": 6.4, "temp_rise": 23, "bpfo": 0.9, "bpfi": 0.8, "bsf": 1.3, "ftf": 3.8},
    "rolling_stage1": {"vib_ratio": 1.90, "kurtosis": 5.0, "temp_rise": 4, "bpfo": 0.8, "bpfi": 0.7, "bsf": 2.0, "ftf": 0.4},
    "rolling_stage2": {"vib_ratio": 2.70, "kurtosis": 5.7, "temp_rise": 17, "bpfo": 0.9, "bpfi": 0.8, "bsf": 2.8, "ftf": 0.5},
    "rolling_stage3": {"vib_ratio": 3.90, "kurtosis": 6.8, "temp_rise": 29, "bpfo": 1.0, "bpfi": 0.9, "bsf": 4.2, "ftf": 0.6},
    "thermal": {"vib_ratio": 1.35, "kurtosis": 2.7, "temp_rise": 43, "bpfo": 0.8, "bpfi": 0.7, "bsf": 0.5, "ftf": 0.3},
    "startup": {"vib_ratio": 4.20, "kurtosis": 5.2, "temp_rise": 7, "bpfo": 1.1, "bpfi": 0.9, "bsf": 0.7, "ftf": 0.5},
    "electrical": {"vib_ratio": 1.08, "kurtosis": 2.2, "temp_rise": 3, "bpfo": 0.75, "bpfi": 0.65, "bsf": 0.45, "ftf": 0.30},
    "load_spike": {"vib_ratio": 1.18, "kurtosis": 2.4, "temp_rise": 5, "bpfo": 0.80, "bpfi": 0.70, "bsf": 0.50, "ftf": 0.35},
    "signal_dropout": {"vib_ratio": 1.10, "kurtosis": 2.3, "temp_rise": 4, "bpfo": 0.80, "bpfi": 0.70, "bsf": 0.50, "ftf": 0.35},
}

BASELINES = {
    "motor": (1.8, 52.0), "pump": (2.1, 55.0), "conveyor": (2.3, 51.0),
    "gearbox": (2.0, 57.0), "fan": (1.7, 49.0), "compressor": (2.2, 60.0),
}


def _read(name: str):
    return json.loads((DATA_DIR / name).read_text(encoding="utf-8"))


def _write(name: str, value) -> None:
    (DATA_DIR / name).write_text(
        json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def _bearing_rows(asset: dict, index: int) -> list[dict]:
    vib_mean, temp_mean = BASELINES[asset["asset_type"]]
    profile = asset["scenario_profile"]
    health = 0.97 if profile == "healthy" else 0.72 if "stage1" in profile else 0.50 if "stage2" in profile else 0.28
    if profile in {"startup", "electrical", "load_spike"}:
        health = 0.90
    if profile == "signal_dropout":
        health = 0.76
    installed = f"202{index % 5}-0{(index % 8) + 1}-15"
    lubricated = f"2026-0{(index % 6) + 1}-10"
    rows = []
    compact = asset["display_asset_id"].replace("-", "")
    for suffix, position in (("DE", "drive_end"), ("NDE", "non_drive_end")):
        offset = 0.0 if suffix == "DE" else 0.12
        rows.append({
            "bearing_id": f"BRG_SYN_{compact}_{suffix}",
            "asset_id": asset["asset_id"],
            "bearing_position": position,
            "bearing_type": asset["bearing_model"],
            "bearing_model": asset["bearing_model"],
            "install_date": installed,
            "expected_life_days": 1460,
            "lubrication_type": "Mobil SHC 100" if asset["asset_type"] != "gearbox" else "Mobilgear 600 XP 220",
            "last_lubrication_date": lubricated,
            "current_health_score": round(max(0.1, health - offset / 2), 2),
            "channel_id": f"CH_SYN_{compact}_{suffix}",
            "historian_tag": f"DRO.{asset['production_line_id'].replace(' ', '_')}.{asset['display_asset_id']}.{suffix}",
            "vib_min_valid": 0.0, "vib_max_valid": 25.0,
            "temp_min_valid": -20.0, "temp_max_valid": 120.0,
            "sample_rate_hz": 25600,
            "baseline_vib_rms_mean": round(vib_mean + offset, 2),
            "baseline_vib_rms_std": 0.35,
            "baseline_kurtosis_mean": 2.0, "baseline_kurtosis_std": 0.45,
            "baseline_temp_mean": round(temp_mean + offset * 4, 2),
            "baseline_temp_std": 4.0,
            "baseline_bpfo_mean": 0.7, "baseline_bpfo_std": 0.22,
            "baseline_corr_vib_kurtosis": 0.35,
            "baseline_corr_vib_temp": 0.25,
            "baseline_corr_vib_bpfo": 0.30,
            "baseline_corr_kurtosis_temp": 0.15,
            "baseline_corr_kurtosis_bpfo": 0.28,
            "baseline_corr_temp_bpfo": 0.12,
            "baseline_sample_count": 2160,
            "baseline_computed_at": "2026-06-30T00:00:00Z"
        })
    return rows


def _signal_rows(asset: dict, bearing: dict, points: int, index: int) -> list[dict]:
    profile = asset["scenario_profile"]
    target = PROFILE_TARGETS[profile]
    vib_mean = bearing["baseline_vib_rms_mean"]
    temp_mean = bearing["baseline_temp_mean"]
    start = datetime(2026, 7, 1, tzinfo=timezone.utc) + timedelta(hours=index * 2)
    rows = []
    for point in range(points):
        progress = point / max(points - 1, 1)
        eased = progress * progress
        ripple = math.sin((point + index) * 0.7) * 0.03
        vib = vib_mean * (1.02 + (target["vib_ratio"] - 1.02) * eased + ripple)
        temp = temp_mean + target["temp_rise"] * eased + ripple * 4
        kurtosis = 2.05 + (target["kurtosis"] - 2.05) * eased
        def band(name: str) -> float:
            return 0.65 + (target[name] - 0.65) * eased
        machine_state = "startup" if profile == "startup" else "running"
        load = 108.0 if profile == "load_spike" and point == points - 1 else 68.0 + (index % 5) * 4
        current_deviation = 0.34 if profile == "electrical" and point == points - 1 else 0.05
        row = {
            "telemetry_id": f"TEL-SYN-{asset['display_asset_id'].replace('-', '')}-{point + 1:02d}",
            "timestamp_utc": (start + timedelta(days=point)).isoformat().replace("+00:00", "Z"),
            "asset_id": asset["asset_id"],
            "bearing_id": bearing["bearing_id"],
            "channel_id": bearing["channel_id"],
            "rpm": float(asset["rated_rpm"]), "load_pct": load,
            "machine_state": machine_state,
            "startup_shutdown_flag": profile == "startup",
            "vib_rms_mm_s": round(vib, 3), "vib_peak_g": round(vib * 0.38, 3),
            "kurtosis": round(kurtosis, 3), "temp_c": round(temp, 3),
            "bpfo_energy": round(band("bpfo"), 3),
            "bpfi_energy": round(band("bpfi"), 3),
            "bsf_energy": round(band("bsf"), 3),
            "ftf_energy": round(band("ftf"), 3),
            "envelope_peak_hz": round(45 + asset["rated_rpm"] / 20, 2),
            "motor_current_a": round(asset["power_kw"] * 1.55, 2),
            "voltage_v": 415.0, "current_deviation": current_deviation,
            "signal_quality_score": 0.99, "data_source": "historian"
        }
        if profile == "signal_dropout" and point == points - 1:
            for field in ("vib_rms_mm_s", "vib_peak_g", "kurtosis", "bpfo_energy", "bpfi_energy", "bsf_energy", "ftf_energy"):
                row[field] = None
            row["signal_quality_score"] = 0.38
        rows.append(row)
    return rows


def _asset_master_row(asset: dict, index: int) -> dict:
    return {
        "asset_id": asset["asset_id"], "asset_name": asset["asset_name"],
        "asset_type": asset["asset_type"], "oem": asset["oem"],
        "model_number": asset["model_number"], "bearing_type": asset["bearing_model"],
        "rated_rpm": asset["rated_rpm"], "power_kw": asset["power_kw"],
        "install_date": f"201{5 + index % 5}-0{index % 8 + 1}-15",
        "production_line_id": asset["production_line_id"],
        "criticality": asset["criticality"], "is_bottleneck": asset["is_bottleneck"],
        "downtime_cost_per_hour": asset["downtime_cost_per_hour"],
        "iso_10816_zone": asset["iso_10816_zone"], "status": asset["status"]
    }


def _work_order(asset: dict, number: int) -> dict | None:
    profile = asset["scenario_profile"]
    if profile in {"healthy", "startup", "electrical", "load_spike"}:
        return None
    stage3 = "stage3" in profile
    data_issue = profile == "signal_dropout"
    thermal = profile == "thermal"
    action = "Inspect sensor and restore signal" if data_issue else "Inspect cooling and lubrication" if thermal else "Inspect and replace bearing as condition requires"
    return {
        "id": f"WO-DEMO-{number:04d}",
        "priority": "CRITICAL" if stage3 else "HIGH" if "stage2" in profile or thermal else "MEDIUM",
        "status": "Pending" if stage3 else "Scheduled",
        "title": f"{asset['display_asset_id']} — {action}",
        "asset_id": asset["asset_id"], "asset_display": asset["display_asset_id"],
        "type": "Instrumentation" if data_issue else "Condition Based",
        "est_hours": 2 if data_issue else 4 if stage3 else 3,
        "parts": "Sensor connector kit" if data_issue else asset["bearing_model"],
        "assigned_to": "Pending" if stage3 else "Demo Reliability Crew",
        "due": "This shift" if stage3 else "Within 7 days",
        "created_by": "DRO synthetic fleet",
        "rul": "0-7 days" if stage3 else "8-30 days",
        "risk": "Critical" if stage3 else "High" if "stage2" in profile else "Medium",
        "cost": f"${max(5000, int(asset['power_kw'] * 260)):,}",
        "checklist": [
            {"text": "Validate current telemetry and diagnosis", "done": True, "tag": "Evidence"},
            {"text": "Confirm LOTO permit and qualified crew", "done": False, "tag": "Safety"},
            {"text": f"Confirm {asset['bearing_model']} or required repair kit", "done": False, "tag": "Parts"},
            {"text": "Capture post-work vibration and temperature baseline", "done": False, "tag": "QA"}
        ],
        "timeline": [
            {"dot": "var(--ac)", "ev": "Generated from synthetic condition evidence", "tm": "Demo dataset"},
            {"dot": "var(--am)", "ev": "Awaiting planning/approval action", "tm": "Current"}
        ],
        "synthetic_data_version": "2026.08.1"
    }


def main() -> int:
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    generated = config["generated_assets"]
    points = int(config["history_points_per_asset"])
    if len({row["asset_id"] for row in generated}) != len(generated):
        raise ValueError("generated asset_id values must be unique")
    if len({row["display_asset_id"] for row in generated}) != len(generated):
        raise ValueError("generated display_asset_id values must be unique")
    unknown_profiles = sorted({row["scenario_profile"] for row in generated} - PROFILE_TARGETS.keys())
    if unknown_profiles:
        raise ValueError(f"unknown scenario profiles: {unknown_profiles}")

    assets = [row for row in _read("asset_master.json") if not row["asset_id"].startswith("AST_SYN_")]
    bearings = [row for row in _read("bearing_master.json") if not row["bearing_id"].startswith("BRG_SYN_")]
    telemetry = {key: value for key, value in _read("telemetry_scenarios.json").items() if not key.startswith("fleet_")}
    mappings = deepcopy(BASE_MAPPINGS)
    work_orders = [
        {"id":"WO-2024-1847","priority":"CRITICAL","status":"Pending","title":"M-104 Drive-End Bearing Replacement","asset_id":"AST_MTR_001","asset_display":"M-104","type":"Condition Based","est_hours":4,"parts":"SKF 6310-2RS (Bin A-14)","assigned_to":"Pending","due":"Wednesday 06:00","created_by":"DRO synthetic fleet","rul":"5-8 days","risk":"Critical","cost":"$18,000","checklist":[{"text":"Validate LOTO EL-104-A","done":True,"tag":"Safety"},{"text":"Confirm SKF 6310-2RS stock","done":True,"tag":"Parts"},{"text":"Assign two LOTO-qualified technicians","done":False,"tag":"Crew"}],"timeline":[{"dot":"var(--rd)","ev":"Stage 3 outer-race fault confirmed","tm":"Current"}],"synthetic_data_version":"2026.08.1"},
        {"id":"WO-DEMO-0207","priority":"HIGH","status":"Scheduled","title":"P-207 Lubrication Inspection","asset_id":"AST_PMP_001","asset_display":"P-207","type":"Preventive","est_hours":2,"parts":"Mobil SHC 100, 200 mL","assigned_to":"Demo Reliability Crew","due":"Wednesday 09:00","created_by":"DRO synthetic fleet","rul":"15-22 days","risk":"Medium","cost":"$2,400","checklist":[{"text":"Confirm IL-207-B","done":True,"tag":"Safety"},{"text":"Collect lubricant and sampling kit","done":False,"tag":"Parts"}],"timeline":[{"dot":"var(--am)","ev":"Lubrication degradation detected","tm":"Current"}],"synthetic_data_version":"2026.08.1"},
        {"id":"WO-DEMO-0301","priority":"MEDIUM","status":"Scheduled","title":"C-301 Sensor Dropout Inspection","asset_id":"AST_CON_001","asset_display":"C-301","type":"Instrumentation","est_hours":1.5,"parts":"CH-31B connector kit","assigned_to":"Demo OT Crew","due":"Thursday 14:00","created_by":"DRO synthetic fleet","rul":"Data review","risk":"Medium","cost":"$1,250","checklist":[{"text":"Inspect junction box J-301","done":False,"tag":"OT"},{"text":"Validate IL-301-B","done":False,"tag":"Safety"}],"timeline":[{"dot":"var(--am)","ev":"Intermittent signal dropout detected","tm":"Current"}],"synthetic_data_version":"2026.08.1"}
    ]

    for index, asset in enumerate(generated, start=1):
        assets.append(_asset_master_row(asset, index))
        new_bearings = _bearing_rows(asset, index)
        bearings.extend(new_bearings)
        scenario = f"fleet_{asset['display_asset_id'].lower().replace('-', '_')}"
        telemetry[scenario] = _signal_rows(asset, new_bearings[0], points, index)
        mappings.append({
            "display_asset_id": asset["display_asset_id"],
            "asset_id": asset["asset_id"], "scenario": scenario,
            "profile": asset["scenario_profile"]
        })
        order = _work_order(asset, 400 + index)
        if order is not None:
            work_orders.append(order)

    # A healthy identity-correct post-repair scenario for the original G-112.
    base_asset = next(row for row in assets if row["asset_id"] == "AST_GBX_001")
    base_bearing = next(row for row in bearings if row["asset_id"] == "AST_GBX_001")
    g112 = {
        "display_asset_id": "G-112", "asset_id": "AST_GBX_001",
        "rated_rpm": base_asset["rated_rpm"], "power_kw": base_asset["power_kw"],
        "scenario_profile": "healthy"
    }
    telemetry["fleet_g_112_post_repair"] = _signal_rows(g112, base_bearing, points, 0)

    _write("asset_master.json", assets)
    _write("bearing_master.json", bearings)
    _write("telemetry_scenarios.json", telemetry)
    _write("fleet_scenario_map.json", {
        "version": config["version"], "assets": mappings
    })
    _write("demo_work_orders.json", work_orders)
    print(f"Generated {len(assets)} assets, {len(bearings)} bearings, "
          f"{len([k for k in telemetry if not k.startswith('_')])} scenarios, "
          f"and {len(work_orders)} work orders.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
