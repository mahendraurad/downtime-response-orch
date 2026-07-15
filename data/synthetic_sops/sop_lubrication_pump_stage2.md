---
sop_id: SOP_003
fault_mode: lubrication_issue
asset_type: pump
bearing_type: SKF6208
applicable_assets: [AST_PMP_001, AST_PMP_002]
priority: MEDIUM
tags: [lubrication_issue, pump, SKF6208, stage_2, oil_service, LINE_002]
related_cases: [CASE_002]
---

## Purpose
Re-lube interval is 60 days NOT 90 days. The 90-day system default was wrong - confirmed by CASE_002 root cause analysis.

## Prerequisites
Verify pump shutdown and isolation. Confirm oil drain path and receptacles.

## Safety and LOTO
Use appropriate pump isolation and lockout-tagout procedures.

## Tools and Parts
Oil drain container, ISO68 lubricant, filter materials, torque wrench.

## Step by Step
1. Shut down pump and isolate from the system.
2. Drain existing oil safely.
3. Inspect housing for contamination and sludge.
4. Refill with ISO68 oil as specified.
5. Restore pump and monitor for leaks.

## 24h QA Note
Improvement is not immediate at 30min. Schedule a 24h recheck to confirm reduced vibration and temperature.

## Lessons from Cases
Use 60-day re-lube interval for SKF6208 pumps; 90-day default is incorrect for this asset class.
