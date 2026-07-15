---
sop_id: SOP_001
title: Outer Race Bearing Replacement - Electric Motor Stage 3
fault_mode: outer_race_fault
asset_type: motor
bearing_type: SKF6310
applicable_assets: [AST_MTR_001, AST_MTR_002]
priority: HIGH
estimated_duration_h: 6
crew_required: 2
related_cases: [CASE_001]
related_sops: [SOP_006]
tags: [outer_race_fault, motor, SKF6310, stage_3, bearing_replacement, LINE_001, loto_001]
version: v1.2
revised: 2026-03-10
---

## Purpose
Stage 3 outer race fault on SKF6310 motor bearings. BPFO > 3.5x baseline AND kurtosis > 5.0.
Bearing replacement is mandatory at Stage 3. Lubrication will NOT arrest Stage 3 degradation.

## Prerequisites
List all prerequisites from SOP_001 section 2

## Safety and LOTO
LOTO_001 for AST_MTR_001 (MCC-12, Valve-4)
LOTO_005 for AST_MTR_002 (MCC-13, Valve-5)
Zone C for AST_MTR_001. Zone A for AST_MTR_002.

## Tools and Parts
PART_001 SKF6310-ZZ from BIN_A1
PART_004 Synthetic Grease from BIN_L1

## Step by Step Procedure
Number all 20 steps from SOP_001 section 5

## Critical Specifications
End cover bolts: 45 Nm cross pattern
Bearing mount temp: 110C target, max 125C
Grease fill: 30-40% cavity, approximately 45-60g

## Post Repair QA Criteria
vib_rms <= 2.5 mm/s at both 1h and 24h
temp <= 60C at 1h, <= 55C at 24h
kurtosis <= 2.8 at 24h
bpfo_energy <= 1.2x baseline
signal_quality >= 0.97
CRITICAL: check BOTH 1h and 24h - work order cannot close on 1h check alone

## Lessons from Cases
From CASE_001: Housing end cover seal must be inspected and replaced with every bearing change.
Failure to replace seal allowed contamination re-entry over 4-6 months.
Post-repair baseline for BRG_001: 1.8 mm/s vib, 55C temp.
