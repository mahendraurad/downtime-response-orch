"""
tools/enrichment.py

Context-joining functions called by the Data Foundation Agent after
all validation checks pass.  Converts AssetMaster and BearingMaster
full records into the leaner AssetContext and BearingContext that get
embedded in TrustedBearingSignal.
"""

from __future__ import annotations

from typing import Dict, Optional


def build_asset_context(asset_master_record) -> Optional[object]:
    """
    Creates an AssetContext from a full AssetMaster record.
    Returns None if the record is None (mapping failed).
    """
    if asset_master_record is None:
        return None

    from src.schemas.bearing_signal import AssetContext
    return AssetContext(
        asset_id               = asset_master_record.asset_id,
        asset_name             = asset_master_record.asset_name,
        asset_type             = asset_master_record.asset_type,
        bearing_type           = asset_master_record.bearing_type,
        rated_rpm              = asset_master_record.rated_rpm,
        power_kw               = asset_master_record.power_kw,
        production_line_id     = asset_master_record.production_line_id,
        criticality            = asset_master_record.criticality,
        is_bottleneck          = asset_master_record.is_bottleneck,
        downtime_cost_per_hour = asset_master_record.downtime_cost_per_hour,
        iso_10816_zone         = asset_master_record.iso_10816_zone,
        status                 = asset_master_record.status,
    )


def build_bearing_context(bearing_master_record) -> Optional[object]:
    """
    Creates a BearingContext from a full BearingMaster record.
    Returns None if the record is None (mapping failed).
    """
    if bearing_master_record is None:
        return None

    from src.schemas.bearing_signal import BearingContext
    return BearingContext(
        bearing_id              = bearing_master_record.bearing_id,
        bearing_position        = bearing_master_record.bearing_position,
        bearing_model           = bearing_master_record.bearing_model,
        lubrication_type        = bearing_master_record.lubrication_type,
        last_lubrication_date   = bearing_master_record.last_lubrication_date,
        current_health_score    = bearing_master_record.current_health_score,
        historian_tag           = bearing_master_record.historian_tag,
        vib_min_valid           = bearing_master_record.vib_min_valid,
        vib_max_valid           = bearing_master_record.vib_max_valid,
        temp_min_valid          = bearing_master_record.temp_min_valid,
        temp_max_valid          = bearing_master_record.temp_max_valid,
        baseline_vib_rms_mean   = bearing_master_record.baseline_vib_rms_mean,
        baseline_vib_rms_std    = bearing_master_record.baseline_vib_rms_std,
        baseline_kurtosis_mean  = bearing_master_record.baseline_kurtosis_mean,
        baseline_kurtosis_std   = bearing_master_record.baseline_kurtosis_std,
        baseline_temp_mean      = bearing_master_record.baseline_temp_mean,
        baseline_temp_std       = bearing_master_record.baseline_temp_std,
        baseline_bpfo_mean           = bearing_master_record.baseline_bpfo_mean,
        baseline_bpfo_std            = bearing_master_record.baseline_bpfo_std,
        baseline_corr_vib_kurtosis   = bearing_master_record.baseline_corr_vib_kurtosis,
        baseline_corr_vib_temp       = bearing_master_record.baseline_corr_vib_temp,
        baseline_corr_vib_bpfo       = bearing_master_record.baseline_corr_vib_bpfo,
        baseline_corr_kurtosis_temp  = bearing_master_record.baseline_corr_kurtosis_temp,
        baseline_corr_kurtosis_bpfo  = bearing_master_record.baseline_corr_kurtosis_bpfo,
        baseline_corr_temp_bpfo      = bearing_master_record.baseline_corr_temp_bpfo,
    )
