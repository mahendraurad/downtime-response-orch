---
sop_id: SOP_002
fault_mode: healthy
asset_type: motor
bearing_type: SKF6310
applicable_assets: [AST_MTR_001, AST_MTR_002]
priority: LOW
tags: [healthy, motor, SKF6310, preventive, grease_top_up, routine]
related_cases: [CASE_005]
---

## Purpose
Routine preventive inspection and lubrication for SKF6310 motors. This SOP applies when all signals are within healthy thresholds and anomaly score remains below alert.

## Prerequisites
Verify instrument calibration and ensure motor is safe for external inspection.

## Safety and LOTO
Confirm low-voltage lockout and tag-out procedures for external inspection only.

## Tools
Standard inspection kit, torque screwdriver, grease gun.

## Step by Step
1. Verify asset identity and safe access.
2. Inspect motor housing and coupling for visible issues.
3. Check vibration and temperature readings against healthy baselines.
4. Add 35g grease top-up if lubricant is within acceptable condition.
5. Record all observations in the work order.

## Acceptance Criteria
Anomaly score remains below 0.30 and all signals within 2 sigma of baseline.

## When to Escalate to SOP_001
If purged grease is dark/metallic or kurtosis rises above 2.5, escalate to SOP_001.
