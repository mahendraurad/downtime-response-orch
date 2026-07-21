"""
tools/sop_catalog.py  —  Phase 6

Synthetic SOP + KB content for dev/test (no PDF files required).
Each entry is a chunk that the retriever indexes.  In production these
would come from chunked PDFs stored in Azure Blob / AI Search.

Structure per document:
  source     — filename (matches what KnowledgeGuidance.source_documents shows)
  fault_mode — primary fault this chunk is relevant to ("" = generic)
  asset_type — primary asset type ("" = all)
  iso_stage  — relevant ISO stage (0 = generic/all)
  text       — the actual passage the retriever embeds and returns
"""
from __future__ import annotations

from typing import List, Dict

SOP_CHUNKS: List[Dict] = [
    # ────────────────────────────────────────────────────────────
    # SOP_001  Outer-race spall — motor — Stage 3
    # ────────────────────────────────────────────────────────────
    {
        "source": "SOP_001_outer_race_motor_stage3.pdf",
        "fault_mode": "outer_race_fault",
        "asset_type": "motor",
        "iso_stage": 3,
        "text": (
            "OUTER RACE SPALL — MOTOR — STAGE 3 REPLACEMENT PROCEDURE (Rev 4.1).\n"
            "Scope: Drive motors with confirmed BPFO harmonic family at ≥4× baseline and "
            "ISO 10816-3 Zone D vibration (>7.1 mm/s). Kurtosis ≥6.5 indicates ≥25% "
            "surface damage. Replacement — not inspection — is mandatory at Stage 3.\n"
            "Step 1: Obtain LOTO EL-104-A permit. Isolate at MCB-104A, MCB-104B, IL-104-M. "
            "Verify zero energy with approved tester before approaching drive end.\n"
            "Step 2: Remove drive-end bearing housing cover. Label all fasteners.\n"
            "Step 3: Extract failed bearing using hydraulic bearing puller (no impact tools).\n"
            "Step 4: Clean housing bore to Ra ≤1.6 µm. Check for secondary damage on shaft.\n"
            "Step 5: Press-fit replacement bearing (SKF 6310-2RS or equivalent). "
            "Apply Mobil SHC 100 grease, 80–100 g, before installation.\n"
            "Step 6: Torque housing bolts to 85 Nm in star pattern, two passes.\n"
            "Step 7: Re-energise and run 15 min no-load. Record baseline vibration ≤2.5 mm/s "
            "and temperature ≤55 °C before returning to production.\n"
            "Torque spec: 85 Nm. Lubricant: Mobil SHC 100 (80–100 g).\n"
            "Post-repair QA: vibration ≤2.5 mm/s at operating temp; recheck at 1h and 24h."
        ),
    },
    {
        "source": "SOP_001_outer_race_motor_stage3.pdf",
        "fault_mode": "outer_race_fault",
        "asset_type": "motor",
        "iso_stage": 3,
        "text": (
            "SAFETY REQUIREMENTS — SOP_001 OUTER RACE SPALL MOTOR.\n"
            "LOTO reference: EL-104-A. Hazard category: rotating machinery (Class B).\n"
            "Mandatory PPE: Class B hearing protection; vibration-resistant gloves (EN ISO 10819); "
            "safety glasses; steel-toed footwear.\n"
            "Zone classification: ISO 10816-3 Zone D — NO approach without full LOTO in place.\n"
            "Confined space: Not applicable for standard motor frame.\n"
            "Hot-work permit: Not required.\n"
            "Crew minimum: 2 technicians. Lead must hold LOTO EL-104-A authorisation.\n"
            "Emergency stop: Red push-button E-STOP-104 at panel P4-West or pull master isolator.\n"
            "Fire risk: Low. Grease is not flammable at operating temperature."
        ),
    },
    # ────────────────────────────────────────────────────────────
    # SOP_002  Preventive motor — healthy (Stage 0/1)
    # ────────────────────────────────────────────────────────────
    {
        "source": "SOP_002_preventive_motor_healthy.pdf",
        "fault_mode": "",
        "asset_type": "motor",
        "iso_stage": 0,
        "text": (
            "MOTOR BEARING — PREVENTIVE INSPECTION (Stage 0/1, Zone A/B).\n"
            "Applicable when: vibration <4.5 mm/s, no BPFO harmonic confirmed, kurtosis <4.0.\n"
            "Step 1: LOTO per asset-specific procedure. Visual inspection of housing and coupling.\n"
            "Step 2: Check lubrication via grease port. Re-grease if interval >6 months: "
            "Mobil SHC 100, 40–60 g. Do not over-grease.\n"
            "Step 3: Check coupling alignment. Acceptable parallel offset: ≤0.1 mm; "
            "angular: ≤0.05 mm/100 mm.\n"
            "Step 4: Record baseline vibration and temperature at operating speed.\n"
            "Re-inspection interval: 6 months or 3,000 operating hours, whichever first."
        ),
    },
    # ────────────────────────────────────────────────────────────
    # SOP_003  Lubrication / inner-race degradation — pump — Stage 1/2
    # ────────────────────────────────────────────────────────────
    {
        "source": "SOP_003_lubrication_pump_stage2.pdf",
        "fault_mode": "inner_race_fault",
        "asset_type": "pump",
        "iso_stage": 2,
        "text": (
            "PUMP BEARING — LUBRICATION DEGRADATION & INNER-RACE EARLY FAULT (Stage 1/2).\n"
            "Trigger: BSF sub-harmonic 0.4–0.6× or BPFI elevated to 1.5–2.5×; kurtosis 3–5.\n"
            "Step 1: LOTO per pump LOTO procedure (IL-207-B or equivalent).\n"
            "Step 2: Remove bearing housing cover. Inspect lube condition: "
            "colour (should be amber, not dark/black), consistency, debris count.\n"
            "Step 3: Flush old grease. Re-lubricate with Mobil SHC 100 (200 mL for P-207 class).\n"
            "Step 4: Inspect inner race under magnification: pitting <5% surface = monitor; "
            "pitting 5–20% = schedule replacement within 30 days; >20% = replace now.\n"
            "Step 5: Reassemble. Torque: 35 Nm housing cover bolts.\n"
            "Step 6: Record post-lube vibration. Target ≤3.5 mm/s. "
            "If kurtosis still >4.0 at 24h re-check, escalate to bearing replacement.\n"
            "Lubricant: Mobil SHC 100. Volume: 200 mL. Housing cover torque: 35 Nm."
        ),
    },
    # ────────────────────────────────────────────────────────────
    # SOP_004  Sensor replacement — conveyor
    # ────────────────────────────────────────────────────────────
    {
        "source": "SOP_004_sensor_replacement_conveyor.pdf",
        "fault_mode": "cage_fault",
        "asset_type": "conveyor",
        "iso_stage": 1,
        "text": (
            "CONVEYOR BEARING — CAGE FAULT INSPECTION (Stage 1, Zone C).\n"
            "Trigger: BSF family with irregular spacing; kurtosis 2.5–4.5; "
            "vibration 2–5 mm/s (ISO Zone C).\n"
            "Step 1: LOTO per conveyor procedure (IL-301-B or equivalent). "
            "Confirm currency of permit before dispatch.\n"
            "Step 2: Remove bearing end-cap (4× M10 bolts, 25 Nm).\n"
            "Step 3: Inspect cage for deformation, cracking, or missing rollers. "
            "Any deformation ≥0.5 mm = replace bearing.\n"
            "Step 4: Check for debris and contamination. Flush and re-lubricate "
            "(NSK grease LG2, 50 g).\n"
            "Step 5: Reassemble and re-torque end-cap to 25 Nm.\n"
            "Step 6: Record post-inspection vibration. Target ≤3.0 mm/s.\n"
            "Re-check: vibration + kurtosis measurement 48h after inspection.\n"
            "Lubricant: NSK LG2 grease (50 g). End-cap torque: 25 Nm.\n"
            "Note: if CH-31B vibration channel is showing dropout, inspect junction "
            "box J-301 (connector fault) before drawing diagnostic conclusions."
        ),
    },
    # ────────────────────────────────────────────────────────────
    # SOP_005  Outer-race spall — gearbox — Stage 3
    # ────────────────────────────────────────────────────────────
    {
        "source": "SOP_005_outer_race_gearbox_stage3.pdf",
        "fault_mode": "outer_race_fault",
        "asset_type": "gearbox",
        "iso_stage": 3,
        "text": (
            "GEARBOX BEARING — OUTER RACE SPALL REPLACEMENT (Stage 3).\n"
            "Applicable: gearbox bearing BPFO ≥3× with side-bands; kurtosis ≥5.5; Zone C/D.\n"
            "Step 1: LOTO gearbox (IL-GBX-A or equivalent). Full oil drain required.\n"
            "Step 2: Remove gearbox cover (torque: 120 Nm). Inspect all bearing positions.\n"
            "Step 3: Replace affected bearing (SKF 22318E or specified equivalent). "
            "Pre-coat with EP2 grease before fitting.\n"
            "Step 4: Refill with Mobil Gear 600 XP 220 oil to sight glass midpoint.\n"
            "Step 5: Torque cover to 120 Nm. Run 30 min no-load. Check for leaks.\n"
            "Step 6: Oil sample at 500 operating hours after replacement.\n"
            "Oil: Mobil Gear 600 XP 220. Cover torque: 120 Nm. Bearing: SKF 22318E."
        ),
    },
    # ────────────────────────────────────────────────────────────
    # SOP_006  Post-repair QA — all assets
    # ────────────────────────────────────────────────────────────
    {
        "source": "SOP_006_post_repair_qa_all_assets.pdf",
        "fault_mode": "",
        "asset_type": "",
        "iso_stage": 0,
        "text": (
            "POST-REPAIR QA PROTOCOL — ALL ASSET CLASSES.\n"
            "Step 1: After LOTO removal, conduct slow run (no-load) for 15 min minimum.\n"
            "Step 2: Record vibration RMS at operating speed. Accept criteria:\n"
            "  Motor (new bearing): ≤2.5 mm/s\n"
            "  Pump (post-lube): ≤3.5 mm/s\n"
            "  Conveyor (inspection): ≤3.0 mm/s\n"
            "  Gearbox (new bearing): ≤1.5 mm/s\n"
            "Step 3: Record temperature. Accept: ≤55 °C at steady state.\n"
            "Step 4: Verify historian alarm thresholds reset in DCS/SCADA.\n"
            "Step 5: Re-check at 1h and 24h after return to production. "
            "Kurtosis must be <2.5 at 24h for bearing-replacement jobs.\n"
            "Step 6: Update CMMS asset record: bearing model, date replaced, technician.\n"
            "Step 7: Close WO with actual hours and parts used."
        ),
    },
    # ────────────────────────────────────────────────────────────
    # CASE studies — learned historical cases
    # ────────────────────────────────────────────────────────────
    {
        "source": "CASE_001_motor_outer_race_stage3_2021.pdf",
        "fault_mode": "outer_race_fault",
        "asset_type": "motor",
        "iso_stage": 3,
        "text": (
            "CASE 001 — M-089 Motor Outer Race Spall, 2021. DRO WARNING raised Day 0. "
            "Action taken Day 5 (planned). Fault confirmed: outer race spall ~30% surface damage. "
            "Repair duration: 4.1h. Post-repair baseline: 1.2 mm/s. No secondary damage. "
            "Avoidance: $540K (unplanned stop estimate). Lesson: Stage 3 detection-to-action "
            "window of 5 days is adequate; vibration stable through the window."
        ),
    },
    {
        "source": "CASE_002_motor_outer_race_stage3_2022.pdf",
        "fault_mode": "outer_race_fault",
        "asset_type": "motor",
        "iso_stage": 3,
        "text": (
            "CASE 002 — M-112 Motor Outer Race Spall, 2022. Detected at Stage 3 "
            "(kurtosis 7.1, BPFO 4.3×). Action Day 3. Repair: 4.3h. Post-repair: 1.5 mm/s. "
            "Finding: 35% outer race surface damage, contamination from worn seal. "
            "Root cause: seal replacement overdue by 4 months. Recommendation: "
            "include seal inspection at every bearing replacement on this motor class."
        ),
    },
    {
        "source": "CASE_003_motor_outer_race_stage3_2023.pdf",
        "fault_mode": "outer_race_fault",
        "asset_type": "motor",
        "iso_stage": 3,
        "text": (
            "CASE 003 — M-104 Motor Outer Race Spall, 2023. Stage 3 confirmed Day 0. "
            "WARNING not actioned. Bearing seized at Day 8 (Saturday shift). "
            "Emergency crew: 18h downtime, winding damage ($80K), emergency parts premium $8K. "
            "Total cost: $619K. Lesson: once BPFO >4× and kurtosis >6, do not run past 7 days. "
            "This case is a reference for current M-104 alert (same asset class, same fault mode)."
        ),
    },
    {
        "source": "CASE_004_pump_inner_race_lube_2022.pdf",
        "fault_mode": "inner_race_fault",
        "asset_type": "pump",
        "iso_stage": 1,
        "text": (
            "CASE 004 — P-118 Pump Inner Race Degradation, 2022. BSF sub-harmonic 0.5×. "
            "Lubrication check at Day 5 reduced kurtosis from 3.2 to 1.9 within 48h. "
            "Bearing replacement avoided. Estimated avoidance: $35K. "
            "Root cause: lube interval exceeded by 2 months. Corrective action: "
            "pump class lube interval revised from 12 months to 8 months."
        ),
    },
    {
        "source": "CASE_005_conveyor_cage_lube_2023.pdf",
        "fault_mode": "cage_fault",
        "asset_type": "conveyor",
        "iso_stage": 1,
        "text": (
            "CASE 005 — C-301 Conveyor Cage Fault, 2023. Lube starvation confirmed at inspection. "
            "Cage showed early deformation (0.3 mm). Re-lube restored vibration to 2.1 mm/s. "
            "Bearing not replaced. Follow-up at 6 months: still healthy (kurtosis 1.8). "
            "Root cause: wrong grease type used at previous PM (EP1 instead of NSK LG2). "
            "Corrective action: grease type now locked in CMMS job plan."
        ),
    },
    # ────────────────────────────────────────────────────────────
    # SOP_007  Gearbox fault — gear tooth degradation — Stage 3
    # ────────────────────────────────────────────────────────────
    {
        "source": "SOP_007_gearbox_fault_stage3.pdf",
        "fault_mode": "gearbox_fault",
        "asset_type": "gearbox",
        "iso_stage": 3,
        "text": (
            "GEARBOX — GEAR TOOTH DEGRADATION OVERHAUL (Stage 3).\n"
            "Applicable: BSF energy ≥3.5× baseline dominant over BPFO/BPFI; kurtosis ≥4.5; Zone C/D.\n"
            "LOTO reference: IL-GBX-A (full isolation required; DO NOT operate under load).\n"
            "Step 1: LOTO gearbox — IL-GBX-A. Drain oil completely. Capture for particle analysis.\n"
            "Step 2: Remove gearbox cover (torque: 120 Nm). Photograph all gear mesh surfaces before handling.\n"
            "Step 3: Inspect gear teeth with 10× loupe. Document pitting depth, spalling percentage, "
            "and location on tooth face (root/flank/tip).\n"
            "Step 4: Replace gear set if pit area >20% of tooth face or spall depth >0.5 mm. "
            "Otherwise re-lube and retest at reduced load for 24h.\n"
            "Step 5: Inspect input/output shaft bearings while cover is off. Replace if kurtosis >4.5 at rest-run.\n"
            "Step 6: Refill with Mobil Gear 600 XP 220 to sight glass midpoint. Check for leaks.\n"
            "Step 7: Commission at no-load 15 min → 50% load 30 min → full load with vibration sign-off.\n"
            "Acceptance: BSF energy ≤1.0×, kurtosis <2.5 at steady state. Oil sample at 500h."
        ),
    },
    # ────────────────────────────────────────────────────────────
    # CASE_006  Gearbox fault — BSF dominant — Stage 3 — 2024
    # ────────────────────────────────────────────────────────────
    {
        "source": "CASE_006_gearbox_bsf_stage3_2024.pdf",
        "fault_mode": "gearbox_fault",
        "asset_type": "gearbox",
        "iso_stage": 3,
        "text": (
            "CASE 006 — G-055 Gearbox BSF Stage 3, 2024. DRO WARNING raised Day 0 "
            "(BSF 3.8×, kurtosis 6.9, no outer/inner race dominance). "
            "Action taken Day 4. Gear tooth inspection revealed 28% pitting on drive flank. "
            "Full gear set replaced. Repair duration: 6.5h. Post-repair BSF: 0.7×, kurtosis 1.9. "
            "Avoidance: $2.1M (catastrophic failure of gearbox + connected drivetrain). "
            "Key finding: BSF energy without BPFO/BPFI dominance is a reliable early indicator of "
            "gear tooth degradation distinct from bearing race faults — do not misclassify as outer race."
        ),
    },
]
