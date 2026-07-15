---
sop_id: SOP_006
fault_mode: all
asset_type: all
applicable_assets: [AST_MTR_001, AST_MTR_002, AST_PMP_001, AST_PMP_002, AST_CON_001, AST_GBX_001]
priority: MANDATORY
tags: [qa, post_repair, mandatory, all_assets, quality_acceptance]
related_cases: [CASE_001, CASE_002, CASE_003, CASE_005, CASE_006]
---

## Purpose
Mandatory post-repair quality assurance for all assets after any corrective action.

## QA Thresholds
AST_MTR_001: vib_rms <= 2.5, temp <= 60C, kurtosis <= 2.8, bpfo_energy <= 1.2x
AST_MTR_002: vib_rms <= 2.2, temp <= 55C, kurtosis <= 2.5, bpfo_energy <= 1.2x
AST_PMP_001: vib_rms <= 3.5, temp <= 65C, kurtosis <= 3.5
AST_PMP_002: vib_rms <= 3.0, temp <= 60C, kurtosis <= 3.0
AST_CON_001: vib_rms <= 2.8, temp <= 58C, kurtosis <= 2.8
AST_GBX_001: vib_rms <= 3.5, temp <= 65C, kurtosis <= 2.8

## Confirmed Post-Repair Baselines
BRG_001: 1.8 mm/s vib, 55C temp
BRG_005: 2.2 mm/s vib, 60C temp
BRG_009: 2.3 mm/s vib, 57C temp
BRG_003: 1.7 mm/s vib, 51C temp
BRG_007: 2.0 mm/s vib, 57C temp

## Work Order Close Policy
Work order CANNOT close until both 1h AND 24h checks are documented.
