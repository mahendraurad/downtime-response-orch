"""
schemas/asset.py

Full AssetMaster and BearingMaster Pydantic models that mirror Tables 3 and 2.
The data_loader populates these from JSON; the enrichment tool converts
them into the leaner AssetContext / BearingContext that gets embedded in
TrustedBearingSignal.
"""

from __future__ import annotations

from typing import Any, Dict, Optional
from pydantic import BaseModel


class AssetMaster(BaseModel):
    asset_id:               str
    asset_name:             str
    asset_type:             str
    oem:                    str
    model_number:           str
    bearing_type:           str
    rated_rpm:              int
    power_kw:               float
    install_date:           str
    production_line_id:     str
    criticality:            str          # low | medium | high
    is_bottleneck:          bool
    downtime_cost_per_hour: float
    iso_10816_zone:         str          # A | B | C | D
    status:                 str          # running | stopped | maintenance

    def to_dict(self) -> Dict[str, Any]:
        return self.model_dump()

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "AssetMaster":
        known = set(cls.model_fields.keys())
        filtered = {k: v for k, v in d.items() if k in known}
        return cls(**filtered)


class BearingMaster(BaseModel):
    bearing_id:                str
    asset_id:                  str
    bearing_position:          str
    bearing_type:              str
    bearing_model:             str
    install_date:              str
    expected_life_days:        int
    lubrication_type:          str
    last_lubrication_date:     str
    current_health_score:      float
    channel_id:                str
    historian_tag:             str
    vib_min_valid:             float
    vib_max_valid:             float
    temp_min_valid:            float
    temp_max_valid:            float
    sample_rate_hz:            int
    baseline_vib_rms_mean:     float
    baseline_vib_rms_std:      float
    baseline_kurtosis_mean:    float
    baseline_kurtosis_std:     float
    baseline_temp_mean:        float
    baseline_temp_std:         float
    baseline_bpfo_mean:        float
    baseline_bpfo_std:         float
    # Pairwise correlations for Hotelling T² covariance matrix.
    baseline_corr_vib_kurtosis:  float = 0.0
    baseline_corr_vib_temp:      float = 0.0
    baseline_corr_vib_bpfo:      float = 0.0
    baseline_corr_kurtosis_temp: float = 0.0
    baseline_corr_kurtosis_bpfo: float = 0.0
    baseline_corr_temp_bpfo:     float = 0.0
    baseline_sample_count:     int    = 0
    baseline_computed_at:      str    = ""

    def to_dict(self) -> Dict[str, Any]:
        return self.model_dump()

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "BearingMaster":
        known = set(cls.model_fields.keys())
        filtered = {k: v for k, v in d.items() if k in known}
        return cls(**filtered)
