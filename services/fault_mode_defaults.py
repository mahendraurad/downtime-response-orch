"""
Fault-mode-keyed typical telemetry values for PDF rendering of seed cases.

These are representative values a domain SME would consider plausible at a
typical detection stage.  Applied only when a case dict has no measured
telemetry — real agent-generated cases with actual measurements override
these entirely.

Key names in "action" and "outcome_targets" match pdf_renderer.py exactly:
  action        -> parts, duration_hr, sop        (NOT parts_required / sop_reference)
  outcome_targets -> vib_target, temp_target, kurt_target, qa_window
"""

FAULT_MODE_DEFAULTS: dict = {

    # ------------------------------------------------------------------
    # OUTER RACE FAULT
    # BPFO dominant.  Stationary outer race -> steady impulse rate,
    # moderate kurtosis, medium RMS.  Progresses at medium rate.
    # Targets grounded in CASE_001 lessons_learned: 1.8 mm/s, 55 C.
    # ------------------------------------------------------------------
    "outer_race_fault": {
        "detection": {
            "anomaly_score": 0.82,
            "vib_rms_mm_s":  4.2,
            "kurtosis":      5.1,
            "temp_c":        64,
            "bpfo_energy":   3.8,    # BPFO dominant
            "signal_quality": "good",
        },
        "diagnosis": {
            "confidence":   0.91,
            "rul_estimate": 18,
        },
        "action": {
            "work_order":  "auto-generated on dispatch",
            "parts":       "replacement bearing (matched spec), housing inspection kit",
            "duration_hr": 3.5,
            "sop":         "SOP_004",
        },
        "outcome_targets": {
            "vib_target":  "<= 1.8 mm/s",
            "temp_target": "<= 55 C",
            "kurt_target": "<= 3.0",
            "qa_window":   "72 hours post-repair",
        },
    },

    # ------------------------------------------------------------------
    # INNER RACE FAULT
    # BPFI dominant.  Inner race rotates with shaft -> amplitude modulation
    # at shaft frequency, higher kurtosis than outer race.  Slightly hotter
    # due to loading pattern.
    # Targets grounded in CASE_003 lessons_learned: 2.0 mm/s, 57 C.
    # ------------------------------------------------------------------
    "inner_race_fault": {
        "detection": {
            "anomaly_score": 0.87,
            "vib_rms_mm_s":  4.8,
            "kurtosis":      5.6,    # higher than outer race -- modulated impacts
            "temp_c":        68,
            "bpfo_energy":   1.2,    # BPFO low -- inner race fault, not outer
            "signal_quality": "good",
        },
        "diagnosis": {
            "confidence":   0.89,
            "rul_estimate": 21,
        },
        "action": {
            "work_order":  "auto-generated on dispatch",
            "parts":       "replacement bearing (matched spec), shaft seal kit",
            "duration_hr": 4.0,      # shaft alignment check adds time
            "sop":         "SOP_006",
        },
        "outcome_targets": {
            "vib_target":  "<= 2.0 mm/s",
            "temp_target": "<= 57 C",
            "kurt_target": "<= 3.2",
            "qa_window":   "72 hours post-repair",
        },
    },

    # ------------------------------------------------------------------
    # CAGE FAULT
    # FTF dominant with irregular BSF spacing.  Cage damage -> erratic
    # impacts -> elevated kurtosis despite moderate RMS.  Short RUL.
    # CASE_004 lessons: kurtosis > 4.5 confirms damage, RUL ~4 days at Stage 3.
    # ------------------------------------------------------------------
    "cage_fault": {
        "detection": {
            "anomaly_score": 0.91,
            "vib_rms_mm_s":  3.2,    # moderate RMS -- cage faults are spiky not loud
            "kurtosis":      7.2,    # high -- erratic cage impacts
            "temp_c":        61,
            "bpfo_energy":   1.6,
            "signal_quality": "acceptable",
        },
        "diagnosis": {
            "confidence":   0.86,
            "rul_estimate": 7,       # cage faults progress fast
        },
        "action": {
            "work_order":  "auto-generated on dispatch",
            "parts":       "replacement bearing (complete assembly), shaft seal",
            "duration_hr": 4.0,
            "sop":         "SOP_007",
        },
        "outcome_targets": {
            "vib_target":  "<= 2.0 mm/s",
            "temp_target": "<= 60 C",
            "kurt_target": "<= 3.5",
            "qa_window":   "48 hours post-repair",
        },
    },

    # ------------------------------------------------------------------
    # BALL FAULT / ROLLING ELEMENT FAULT
    # BSF dominant with sidebands at BSF +/- FTF.  Ball defects produce
    # high kurtosis -- impacts localised to individual rolling elements.
    # Progresses faster than outer race.
    # ------------------------------------------------------------------
    "ball_fault": {
        "detection": {
            "anomaly_score": 0.84,
            "vib_rms_mm_s":  3.8,
            "kurtosis":      5.8,    # localised ball impacts
            "temp_c":        62,
            "bpfo_energy":   1.3,
            "signal_quality": "good",
        },
        "diagnosis": {
            "confidence":   0.83,
            "rul_estimate": 14,
        },
        "action": {
            "work_order":  "auto-generated on dispatch",
            "parts":       "replacement bearing (matched spec)",
            "duration_hr": 3.5,
            "sop":         "SOP_005",
        },
        "outcome_targets": {
            "vib_target":  "<= 2.0 mm/s",
            "temp_target": "<= 58 C",
            "kurt_target": "<= 3.2",
            "qa_window":   "72 hours post-repair",
        },
    },

    # ------------------------------------------------------------------
    # LUBRICATION ISSUE  (used by CASE_002)
    # Broadband noise -- not specific bearing frequencies.  Low kurtosis
    # (continuous noise, not impulsive).  Elevated temperature is the
    # dominant indicator.  Long RUL if re-lubricated promptly.
    # Targets from CASE_002 lessons_learned: 2.2 mm/s, 60 C.
    # ------------------------------------------------------------------
    "lubrication_issue": {
        "detection": {
            "anomaly_score": 0.68,
            "vib_rms_mm_s":  2.8,
            "kurtosis":      2.1,    # low -- broadband noise, not impulsive
            "temp_c":        75,     # high temp is the key indicator
            "bpfo_energy":   0.9,
            "signal_quality": "good",
        },
        "diagnosis": {
            "confidence":   0.84,
            "rul_estimate": 30,      # early stage; bearing survives if re-lubricated
        },
        "action": {
            "work_order":  "auto-generated on dispatch",
            "parts":       "lubricant (specified grade), seal inspection kit",
            "duration_hr": 1.5,
            "sop":         "SOP_002",
        },
        "outcome_targets": {
            "vib_target":  "<= 2.2 mm/s",
            "temp_target": "<= 60 C",
            "kurt_target": "<= 3.0",
            "qa_window":   "24 hours post-service",  # lube needs time to distribute
        },
    },

    # ------------------------------------------------------------------
    # LUBRICATION FAULT / STARVATION  (generic alias)
    # More severe -- may have begun bearing damage.  Higher temp, more
    # broadband content, shorter RUL than lubrication_issue.
    # ------------------------------------------------------------------
    "lubrication_fault": {
        "detection": {
            "anomaly_score": 0.76,
            "vib_rms_mm_s":  3.3,
            "kurtosis":      2.8,
            "temp_c":        80,
            "bpfo_energy":   1.2,
            "signal_quality": "good",
        },
        "diagnosis": {
            "confidence":   0.80,
            "rul_estimate": 14,
        },
        "action": {
            "work_order":  "auto-generated on dispatch",
            "parts":       "lubricant (specified grade), replacement seal, bearing inspection kit",
            "duration_hr": 2.0,
            "sop":         "SOP_002",
        },
        "outcome_targets": {
            "vib_target":  "<= 2.5 mm/s",
            "temp_target": "<= 62 C",
            "kurt_target": "<= 3.2",
            "qa_window":   "24 hours post-service",
        },
    },

    # ------------------------------------------------------------------
    # MISALIGNMENT
    # Mechanical fault -- not a bearing frequency.  High 2x RPM, radial
    # loading -> elevated RMS.  Kurtosis low (sinusoidal forcing).
    # BPFO/BPFI low (no bearing damage at early detection stage).
    # ------------------------------------------------------------------
    "misalignment": {
        "detection": {
            "anomaly_score": 0.73,
            "vib_rms_mm_s":  5.5,    # high RMS from rotational forcing
            "kurtosis":      3.2,    # low -- continuous sinusoidal
            "temp_c":        66,
            "bpfo_energy":   0.8,    # low -- not a bearing frequency fault
            "signal_quality": "good",
        },
        "diagnosis": {
            "confidence":   0.82,
            "rul_estimate": 14,      # accelerates bearing damage if uncorrected
        },
        "action": {
            "work_order":  "auto-generated on dispatch",
            "parts":       "flexible coupling, alignment shims",
            "duration_hr": 6.0,      # laser alignment takes time
            "sop":         "SOP_008",
        },
        "outcome_targets": {
            "vib_target":  "<= 2.5 mm/s",
            "temp_target": "<= 60 C",
            "kurt_target": "<= 3.2",
            "qa_window":   "72 hours post-repair",
        },
    },

    # ------------------------------------------------------------------
    # IMBALANCE
    # High 1x RPM, dominant radial vibration.  Low kurtosis (sinusoidal).
    # Not particularly hot.  Gradual bearing wear -> long RUL.
    # ------------------------------------------------------------------
    "imbalance": {
        "detection": {
            "anomaly_score": 0.69,
            "vib_rms_mm_s":  6.2,    # high radially
            "kurtosis":      2.8,    # low -- sinusoidal excitation
            "temp_c":        58,
            "bpfo_energy":   0.7,
            "signal_quality": "good",
        },
        "diagnosis": {
            "confidence":   0.85,
            "rul_estimate": 30,
        },
        "action": {
            "work_order":  "auto-generated on dispatch",
            "parts":       "balancing weights (mass determined by measurement)",
            "duration_hr": 4.0,
            "sop":         "SOP_009",
        },
        "outcome_targets": {
            "vib_target":  "<= 2.5 mm/s",
            "temp_target": "<= 55 C",
            "kurt_target": "<= 3.0",
            "qa_window":   "72 hours post-repair",
        },
    },

    # ------------------------------------------------------------------
    # LOOSENESS
    # Structural: harmonics of running speed, sub-harmonic content.
    # Moderate kurtosis.  Can progress rapidly if fasteners fail.
    # ------------------------------------------------------------------
    "looseness": {
        "detection": {
            "anomaly_score": 0.78,
            "vib_rms_mm_s":  4.5,
            "kurtosis":      4.1,
            "temp_c":        56,
            "bpfo_energy":   1.1,
            "signal_quality": "acceptable",
        },
        "diagnosis": {
            "confidence":   0.81,
            "rul_estimate": 10,      # can progress fast
        },
        "action": {
            "work_order":  "auto-generated on dispatch",
            "parts":       "locking compound (Loctite grade), replacement fasteners",
            "duration_hr": 2.0,
            "sop":         "SOP_010",
        },
        "outcome_targets": {
            "vib_target":  "<= 2.0 mm/s",
            "temp_target": "<= 55 C",
            "kurt_target": "<= 3.5",
            "qa_window":   "72 hours post-repair",
        },
    },

    # ------------------------------------------------------------------
    # HEALTHY
    # No fault.  Baseline from CASE_005 lessons_learned: 1.8-2.0 mm/s,
    # 51-54 C.  Kurtosis near 3 (Gaussian noise baseline).
    # ------------------------------------------------------------------
    "healthy": {
        "detection": {
            "anomaly_score": 0.08,
            "vib_rms_mm_s":  1.9,
            "kurtosis":      2.8,    # near-Gaussian -- no fault impulses
            "temp_c":        52,
            "bpfo_energy":   0.3,
            "signal_quality": "good",
        },
        "diagnosis": {
            "confidence":   0.95,
            "rul_estimate": 365,     # no fault -- annual horizon
        },
        "action": {
            "work_order":  "auto-generated on dispatch",
            "parts":       "grease (standard grade)",
            "duration_hr": 0.5,
            "sop":         "SOP_001",
        },
        "outcome_targets": {
            "vib_target":  "<= 2.0 mm/s",
            "temp_target": "<= 54 C",
            "kurt_target": "<= 3.0",
            "qa_window":   "routine monitoring",
        },
    },

    # ------------------------------------------------------------------
    # SENSOR FAULT  (used by CASE_006)
    # Not a bearing mechanical fault.  Burst dropout pattern from loose
    # terminal connection.  Bearing itself is healthy; anomaly is in
    # signal quality.  Measured vibration values reflect healthy bearing.
    # ------------------------------------------------------------------
    "sensor_fault": {
        "detection": {
            "anomaly_score": 0.65,   # elevated by dropout pattern, not bearing damage
            "vib_rms_mm_s":  1.8,   # bearing is healthy between dropouts
            "kurtosis":      3.1,
            "temp_c":        55,
            "bpfo_energy":   0.4,
            "signal_quality": "poor -- burst dropouts detected",
        },
        "diagnosis": {
            "confidence":   0.93,    # high confidence: IS a sensor issue
            "rul_estimate": 14,      # signal should be restored well within this
        },
        "action": {
            "work_order":  "auto-generated on dispatch",
            "parts":       "terminal connector, calibrated torque screwdriver (5-6 Nm)",
            "duration_hr": 0.5,
            "sop":         "SOP_003",
        },
        "outcome_targets": {
            "vib_target":  "signal restored to nominal",
            "temp_target": "signal restored to nominal",
            "kurt_target": "N/A (sensor fault, not bearing fault)",
            "qa_window":   "24 hours post-repair",
        },
    },

    # ------------------------------------------------------------------
    # DEFAULT -- unknown or unrecognised fault mode
    # ------------------------------------------------------------------
    "_default": {
        "detection": {
            "anomaly_score": 0.75,
            "vib_rms_mm_s":  3.5,
            "kurtosis":      4.5,
            "temp_c":        60,
            "bpfo_energy":   1.5,
            "signal_quality": "acceptable",
        },
        "diagnosis": {
            "confidence":   0.75,
            "rul_estimate": 30,
        },
        "action": {
            "work_order":  "auto-generated on dispatch",
            "parts":       "see SOP",
            "duration_hr": 4.0,
            "sop":         "SOP_GENERIC",
        },
        "outcome_targets": {
            "vib_target":  "<= 2.0 mm/s",
            "temp_target": "<= 60 C",
            "kurt_target": "<= 3.5",
            "qa_window":   "72 hours post-repair",
        },
    },
}

# Aliases
FAULT_MODE_DEFAULTS["rolling_element_fault"] = FAULT_MODE_DEFAULTS["ball_fault"]
FAULT_MODE_DEFAULTS["lubrication_starvation"] = FAULT_MODE_DEFAULTS["lubrication_fault"]


def get_defaults(fault_mode: str) -> dict:
    """Return typical defaults for fault_mode, falling back to _default."""
    return FAULT_MODE_DEFAULTS.get(
        fault_mode,
        FAULT_MODE_DEFAULTS["_default"],
    )
