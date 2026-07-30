export const PERSONA_TASKS = {
  supervisor: {
    'M-104': [
      ['Review alert', 'DRO has raised M-104 as CRITICAL. Review the fault summary: outer race spall Stage 3, RUL 5–8 days, Line 4 bottleneck asset.'],
      ['Assess run risk', 'Decide: approve Wednesday intervention or run to weekend. Running to Saturday = 67% failure probability and $619K+ exposure.'],
      ['Approve WO-2024-1847', 'Approve the work order to confirm Wednesday 06:00 crew deployment. All parts and crew are ready — zero lead time on approval.'],
      ['Brief shift team', 'Notify Line 4 operators: watch for worsening vibration or noise. Call immediately if speed drops or unusual sounds.'],
      ['Prepare handover', 'Include M-104 status, WO approval decision, and Wednesday window in shift handover brief for incoming supervisor.']
    ],
    'P-207': [
      ['Note secondary warning', 'P-207 BSF sub-harmonic flagged — early stage. RUL ~18 days. Not urgent vs M-104 but keep on watch list.'],
      ['Confirm Wednesday slot', 'Ensure P-207 lubrication check at Wed 09:00 is not displaced by M-104 work. T.Rodriguez can cover both.']
    ],
    'C-301': [
      ['Note cage fault warning', 'C-301 cage fault pattern at early stage. RUL ~26 days. Thursday 14:00 K.Mensah inspection is appropriate.'],
      ['Flag sensor issue to OT', 'CH-31B sensor dropout on C-301 — ask Raj to inspect junction box J-301 before Thursday crew visit.']
    ]
  },
  engineer: {
    'M-104': [
      ['Analyse FFT signature', 'Review BPFO harmonic family: 1× at 87.4 Hz (4.02×), 2× at 174.8 Hz, 3× at 262.2 Hz. Sidebands at ±24.7 Hz confirm rotational spall modulation.'],
      ['Validate Stage 3 classification', 'Confirm: Kurtosis 6.8 (>4.0 threshold), Crest Factor 9.2. Pattern consistent with 25–40% outer race surface damage.'],
      ['Run RUL confidence intervals', 'LSTM+Weibull gives median 6.4 days, P10: 4.1 days, P90: 9.3 days at 82% confidence. Load sensitivity: +20% load → reduces median by 1.4d.'],
      ['Match KB cases', 'Three KB matches: Cases 2021-047, 2022-183, 2023-092. All reached failure in 4.1–9.3 days from this signature. Average post-repair baseline: 1.4 mm/s.'],
      ['Specify post-repair QA', 'Set pass/fail criteria: post-repair vibration ≤2.5 mm/s at operating temp, temperature ≤55°C. Recheck at 1h and 24h post-start.']
    ],
    'P-207': [
      ['Characterise BSF pattern', 'P-207 shows 0.46× BSF sub-harmonic — characteristic of early-stage cage or inner race involvement. Kurtosis 3.1 (below 4.0 fault threshold).'],
      ['Determine lube vs inspect', 'Lubrication degradation is the most probable cause at this stage. Re-lube and recheck kurtosis at 24h. Escalate if kurtosis rises above 4.0.']
    ],
    'C-301': [
      ['Assess cage fault pattern', 'C-301 shows BSF family with irregular spacing — cage deformation or lube starvation. Linked to 2023 lube starvation event (probable cause).'],
      ['Account for sensor dropout', 'CH-31B 3.2% dropout reduces RUL confidence from 85% to 71%. Resolve sensor before drawing further diagnostic conclusions.']
    ]
  },
  maintenance: {
    'M-104': [
      ['Confirm parts ready', 'SKF 6310-2RS: Bin A-14, Qty 3. Mobil SHC 100: 2L in stock. Seal kit: available. Collect to staging area by Tuesday afternoon.'],
      ['Assign and confirm crew', 'T.Rodriguez (lead, LOTO EL-104-A qualified) + K.Mensah (LOTO qualified). Both available Wednesday 06:00. Send confirmation notifications.'],
      ['Prepare job pack', 'SOP M-104-REP-04 Rev 4.1: isolation, housing removal, press fit SKF 6310-2RS, pre-lube Mobil SHC 100, torque to 85 Nm, baseline check.'],
      ['Book Wednesday window', 'Confirm Wed 06:00–10:00 slot in maintenance schedule. Block for 4h + 30min buffer. Production team notified of planned stop.'],
      ['Log actual vs estimated', 'After repair: log actual hours and parts used vs estimate. Update MRO model for future M-104 class jobs.']
    ],
    'P-207': [
      ['Confirm T.Rodriguez Wed 09:00', 'P-207 lube check at 09:00 after M-104 completes. T.Rodriguez can lead both jobs in sequence.'],
      ['Prepare lube kit', 'Mobil SHC 100 (200mL), housing cover gasket if needed. Tools: housing wrench, lube gun. Job estimated 2h.']
    ],
    'C-301': [
      ['Confirm K.Mensah Thu 14:00', 'K.Mensah assigned for C-301 cage inspection Thursday. Confirm availability and LOTO awareness for IL-301-B.'],
      ['Flag J-301 sensor to Raj', 'Ask OT team to check junction box J-301 before Thursday so sensor is repaired before the crew visit.']
    ]
  },
  manager: {
    'M-104': [
      ['Review production exposure', 'M-104 is Line 4 bottleneck. Unplanned failure: 4–6h stop at $41K/h = $164–246K/event + emergency repair costs + winding damage risk.'],
      ['Approve WO from ops level', 'WO-2024-1847 is pending. Approving today: $18K cost, $601K avoidance, Wednesday window available. All logistics ready.'],
      ['Confirm production plan', 'Notify production planning: 4h planned stop Wednesday 06:00–10:00. Adjust Line 4 daily targets accordingly.'],
      ['Review contingency', 'If M-104 fails before Wednesday: emergency crew mobilisation plan, production re-route to Line 3 partial capacity, customer communication trigger.']
    ],
    'P-207': [
      ['Confirm secondary path plan', 'P-207 on Line 2 secondary path. Lubrication check Wednesday is adequate. No production plan adjustment needed.']
    ],
    'C-301': [
      ['Note assembly line impact', 'C-301 on assembly line. Thursday inspection causes no production stop. K.Mensah inspection during low-production window.']
    ]
  },
  executive: {
    'M-104': [
      ['Review portfolio risk', "M-104 contributes 56% of this week's total portfolio risk at $500K–750K probability-weighted exposure."],
      ['Note DRO ROI on this event', '$601K avoidance at $18K repair cost = 33× ROI. Consistent with YTD performance of $2.3M avoidance.'],
      ['Board impact', 'This event is a strong DRO value example: detection 3 days before alarm threshold, full logistics ready, WO drafted automatically.']
    ],
    'P-207': [
      ['Note portfolio position', 'P-207 is a monitored secondary risk. Not a leadership concern currently. DRO managing through routine schedule.']
    ]
  },
  ot: {
    'M-104': [
      ['Confirm M-104 signal health', 'All 4 channels nominal: CH-12A (vib), TT-104 (temp), CT-104 (current), PT-104 (speed). Historian 99.7% last 30d.'],
      ['Validate fault is real', 'BPFO pattern confirmed across 3 consecutive scans. Not a sensor artefact. Signal quality score: 0.97.'],
      ['Prepare post-repair baseline', 'After repair: set historian alert at ≤2.5 mm/s. Update CMMS asset record with new bearing install date and model.']
    ],
    'C-301': [
      ['Diagnose CH-31B dropout', '3.2% missing intervals in 30–90s bursts — consistent with loose connector at J-301. Inspect cable and connector before Thursday.'],
      ['Assess DRO confidence impact', 'Dropout reduces C-301 RUL confidence from 85% to 71%. Resolve before Thursday maintenance visit to restore full diagnostic accuracy.']
    ],
    'P-207': [
      ['Confirm P-207 signals clean', 'P-207 vibration channel clean. Sub-harmonic at 0.46× is a genuine bearing feature confirmed on quality signal data.']
    ]
  },
  safety: {
    'M-104': [
      ['Confirm LOTO EL-104-A valid', 'LOTO EL-104-A: issued 2023-11-14, valid to 2024-11-14. Covers MCB-104A, MCB-104B, IL-104-M. Current — no renewal needed.'],
      ['Verify ISO Zone D compliance', 'M-104 at 14.7 mm/s — Zone D under ISO 10816-3. Mandatory isolation before any approach. No exceptions.'],
      ['Check crew qualifications', 'T.Rodriguez and K.Mensah: both LOTO EL-104-A certified. Confirm PPE availability: Class B hearing protection + vibration-resistant gloves.'],
      ['Issue pre-task safety brief', 'Conduct pre-task brief covering: hazards, isolation steps, PPE, emergency stop location, first aid contact, re-energisation procedure.']
    ],
    'C-301': [
      ['Check IL-301-B currency', 'IL-301-B last reviewed Aug 2023. Review due Feb 2024 — confirm it is still current before Thursday crew dispatch.'],
      ['Confirm Zone C requirements', 'C-301 at 3.2 mm/s — Zone C. No mandatory isolation under ISO 10816-3, but LOTO IL-301-B still required per site procedure.']
    ],
    'P-207': [
      ['Confirm IL-207-B for P-207', 'P-207 LOTO IL-207-B is current (reviewed Sep 2023). Zone C — standard PPE sufficient. Low risk task.']
    ]
  }
};
