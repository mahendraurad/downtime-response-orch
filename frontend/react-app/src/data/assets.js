export const ASSETS = [
  { id: 'M-104', nm: 'Drive Motor — Line 4 Press Station', tp: 'Motor', br: 'SKF 6310-2RS', kw: 45, rpm: 1480, ln: 'Line 4', st: 'critical', rul: '~6 days', vib: 14.7, tmp: 87.4, bpfo: 4.02, last: '14 months', iso: 'Zone D',
    trend: [22, 21, 20, 18, 15, 13, 11, 10, 8, 7, 6, 5],
    faults: [{ dt: '2024-01-07', ty: 'Outer Race Spall', st: 'Stage 3 Active', sc: 'var(--rd)' }, { dt: '2023-03-12', ty: 'Rotor Imbalance', st: 'Corrected', sc: 'var(--gn)' }, { dt: '2022-09-01', ty: 'Shaft Looseness', st: 'Corrected', sc: 'var(--gn)' }],
    insights: {
      supervisor: [{ l: 'Can M-104 run safely until Saturday?', c: 'r' }, { l: 'What do I tell Line 4 operators?', c: '' }, { l: 'Consequence if M-104 seizes mid-shift?', c: 'r' }],
      engineer: [{ l: 'Full FFT analysis for M-104', c: 'b' }, { l: 'RUL confidence interval?', c: '' }, { l: 'Root cause of outer race initiation?', c: '' }],
      maintenance: [{ l: 'All parts in stock for M-104?', c: 'g' }, { l: 'Labour estimate for replacement?', c: '' }, { l: 'What tools are needed?', c: '' }],
      manager: [{ l: 'Production impact if M-104 fails?', c: 'r' }, { l: 'Cost: planned vs emergency?', c: 'b' }, { l: 'Line 4 OEE impact?', c: '' }],
      executive: [{ l: 'Financial exposure from M-104?', c: 'r' }, { l: 'DRO ROI on this event?', c: 'g' }, { l: 'How does M-104 affect Q3?', c: '' }],
      ot: [{ l: 'All M-104 signals healthy?', c: 'g' }, { l: 'Any historian gaps last 48h?', c: '' }, { l: 'CMMS current for M-104?', c: '' }],
      safety: [{ l: 'LOTO EL-104-A — is it current?', c: 'b' }, { l: 'ISO 10816-3 status for M-104?', c: 'r' }, { l: 'PPE for bearing replacement?', c: '' }]
    },
    chatResp: {
      supervisor: '<strong style="color:var(--t)">M-104 — Critical. Approve WO-2024-1847 today.</strong><br>Running to Saturday: 67% failure probability, 12–18h unplanned stop, $619K+ cost. Wednesday planned action: $18K. Net avoidance: $601K. Parts ready, crew available, window available. The decision is clear.',
      engineer: '<strong style="color:var(--t)">M-104 FFT — outer race spall Stage 3.</strong><br>BPFO 4.02× at 87.4 Hz, harmonics at 2× and 3×. Sidebands at ±24.7 Hz (shaft frequency) confirm rotational spall modulation. Kurtosis 6.8, Crest Factor 9.2. Estimated 25–40% surface damage. Three KB cases failed in 4.1–9.3 days from this stage.',
      maintenance: '<strong style="color:var(--gn)">Parts confirmed.</strong> SKF 6310-2RS: Bin A-14, Qty 3, $340. Mobil SHC 100: 2L in stock. Seal kit available. All tools in shop. Collect to staging Tuesday afternoon. Wednesday crew: T.Rodriguez (lead) + K.Mensah, both LOTO-qualified.',
      manager: '<strong style="color:var(--rd)">Line 4 bottleneck risk: HIGH.</strong> Unplanned failure: 4–6h × $41K/h = $164–246K + emergency costs + possible winding damage = $619K+. Planned Wednesday: $18K. Net avoidance: $601K. Approve WO today — all logistics are ready.',
      executive: 'M-104 is the highest-value risk event this week. 67% × $619K = $415K expected-value loss without action. Planned approval delivers $601K avoidance at 33× ROI. YTD: 3 similar events prevented = $1.4M. This is textbook DRO value delivery.',
      ot: '<strong style="color:var(--gn)">M-104: all 4 channels nominal.</strong> CH-12A clean signal — BPFO confirmed genuine, not artefact. TT-104: 87.4°C consistent with increased friction load. CT-104: +2.3% current vs baseline. PT-104: speed stable at 1474 RPM. Historian: 99.7% last 30d.',
      safety: '<strong style="color:var(--rd)">Zone D exceeded — mandatory isolation required.</strong> LOTO EL-104-A is current (Nov 2023, valid Nov 2024). MCB-104A, MCB-104B, IL-104-M must all be locked out. No confined space, no hot work. PPE: Class B hearing + vibration-resistant gloves. T.Rodriguez and K.Mensah are both LOTO-certified.'
    }
  },
  { id: 'P-207', nm: 'Feed Pump — Station 2', tp: 'Pump', br: 'FAG 6206-2RSR', kw: 22, rpm: 2900, ln: 'Line 2', st: 'warning', rul: '~18 days', vib: 4.1, tmp: 61.2, bpfo: 0.46, last: '8 months', iso: 'Zone C',
    trend: [5, 5, 4.8, 4.7, 4.5, 4.4, 4.3, 4.2, 4.2, 4.1, 4.1, 4.1],
    faults: [{ dt: '2024-01-05', ty: 'Inner Race Sub-harmonic', st: 'Monitor', sc: 'var(--am)' }, { dt: '2023-07-19', ty: 'Cavitation Pattern', st: 'Corrected', sc: 'var(--gn)' }],
    insights: {
      supervisor: [{ l: 'P-207 urgency vs M-104?', c: '' }, { l: 'Can P-207 wait until Wednesday?', c: 'g' }],
      engineer: [{ l: 'Inner race vs cage — how to tell on P-207?', c: 'b' }, { l: 'P-207 stage — early or mid?', c: '' }],
      maintenance: [{ l: 'P-207 lube check — what is involved?', c: 'g' }, { l: 'Can P-207 share crew with M-104?', c: '' }],
      ot: [{ l: 'P-207 signal quality check', c: 'g' }],
      safety: [{ l: 'LOTO for P-207 lube check?', c: 'b' }]
    },
    chatResp: {
      supervisor: 'P-207 is <strong style="color:var(--am)">secondary — watch, not act urgently.</strong> BSF sub-harmonic at 0.46× is early-stage. RUL ~18 days. Wednesday 09:00 lube check is the right response. Watch for: vibration above 5 mm/s or temperature above 70°C.',
      engineer: 'P-207 shows <strong style="color:var(--am)">0.46× BSF sub-harmonic</strong> — early cage or inner race involvement, not yet BPFI-class. Kurtosis 3.1 (below 4.0 fault threshold). Likely lubrication degradation. Lube check first, recheck kurtosis at 24h post-lube.',
      maintenance: 'P-207 lube check: 2h, 1 tech (T.Rodriguez after M-104 at 09:00). Job: remove housing cover, inspect, re-lube with Mobil SHC 100 (200mL). Post-lube vibration target: ≤3.5 mm/s.',
      manager: 'P-207 is non-bottleneck on Line 2 secondary path. ~$35K probability-weighted risk. Routine Wednesday lube check is adequate. No management action needed.',
      executive: 'P-207 contributes ~$35K to weekly portfolio risk. Non-bottleneck, managed through routine maintenance. Not a leadership concern.',
      ot: 'P-207 signals nominal. 0.46× BSF is genuine bearing feature on clean signal. No historian gaps. TT-207 at 61.2°C — expected range.',
      safety: 'P-207 lube check: standard low-risk. LOTO IL-207-B required (current Sep 2023). Zone C — no mandatory isolation under ISO 10816-3 but LOTO per site procedure. Standard PPE.'
    }
  },
  { id: 'C-301', nm: 'Conveyor Drive — Assembly Line', tp: 'Conveyor', br: 'NSK 6310', kw: 15, rpm: 960, ln: 'Assembly', st: 'warning', rul: '~26 days', vib: 3.2, tmp: 54.8, bpfo: 0.82, last: '11 months', iso: 'Zone C',
    trend: [3, 3, 3.1, 3.0, 3.2, 3.1, 3.2, 3.2, 3.1, 3.3, 3.2, 3.2],
    faults: [{ dt: '2024-01-06', ty: 'Cage Fault Pattern', st: 'Monitor', sc: 'var(--am)' }, { dt: '2023-05-22', ty: 'Lube Starvation', st: 'Corrected', sc: 'var(--gn)' }],
    insights: {
      supervisor: [{ l: 'C-301 urgency vs M-104?', c: '' }, { l: 'Can C-301 wait until Thursday?', c: 'g' }],
      engineer: [{ l: 'Cage fault vs inner race on C-301?', c: 'b' }, { l: 'C-301 lube history — related fault?', c: '' }],
      maintenance: [{ l: 'What does C-301 inspection involve?', c: 'g' }],
      ot: [{ l: 'C-301 CH-31B dropout — likely cause?', c: 'r' }, { l: 'Impact on DRO confidence?', c: '' }],
      safety: [{ l: 'Safety req. for C-301 inspection', c: '' }, { l: 'IL-301-B currency check', c: 'b' }]
    },
    chatResp: {
      supervisor: 'C-301 is <strong style="color:var(--gn)">low urgency</strong> vs M-104. Cage fault at early stage, ~26 days RUL. Thursday 14:00 K.Mensah inspection is right. Separate: ask Raj to inspect J-301 sensor before Thursday.',
      engineer: 'C-301 shows <strong style="color:var(--am)">cage fault at 0.82× BPFO</strong>. BSF family with irregular spacing — cage deformation or lube starvation (2023 event is probable cause). Kurtosis 2.8 — below fault threshold. Note: CH-31B dropout reduces RUL confidence to 71%.',
      maintenance: 'C-301 inspection: 1.5h, K.Mensah Thursday. Inspect cage, check debris, top up NSK grease. No parts needed. Also flag J-301 sensor to Raj before Thursday.',
      ot: '<strong style="color:var(--rd)">C-301 CH-31B dropout is the priority.</strong> 3.2% missing in 30–90s bursts — probable loose connector at J-301. Reduces DRO confidence from 85% to 71%. Inspect J-301 before Thursday.',
      safety: 'C-301 inspection: standard risk. IL-301-B required — check currency (last Aug 2023, review due Feb 2024). Zone C — standard PPE sufficient.',
      manager: 'C-301 on assembly line — non-bottleneck. Thursday inspection causes no production stop. Minimal financial risk.',
      executive: 'C-301 is a monitored low-risk event. Managed through routine schedule. No leadership action needed.'
    }
  },
  { id: 'M-089', nm: 'Drive Motor — Line 2', tp: 'Motor', br: 'FAG 6309-2Z', kw: 37, rpm: 1480, ln: 'Line 2', st: 'healthy', rul: '~37 days', vib: 1.8, tmp: 48.2, bpfo: 0.9, last: '2 months', iso: 'Zone A',
    trend: [8, 7, 5, 3, 2.2, 2, 1.9, 1.8, 1.8, 1.8, 1.8, 1.8],
    faults: [{ dt: '2023-12-29', ty: 'Emergency Seizure Failure', st: 'Bearing replaced', sc: 'var(--t3)' }],
    insights: {
      supervisor: [{ l: 'M-089 health post-replacement?', c: 'g' }],
      engineer: [{ l: 'M-089 run-in vibration normal?', c: 'g' }, { l: 'Installation issues to watch for?', c: '' }],
      maintenance: [{ l: 'M-089 next inspection milestone?', c: '' }],
      safety: [{ l: 'M-089 post-repair safety clearance?', c: 'g' }]
    },
    chatResp: {
      supervisor: 'M-089 is <strong style="color:var(--gn)">healthy</strong>. Vibration settled to 1.8 mm/s, temp 48.2°C, Zone A. New FAG 6309-2Z running normally at 2 months. Note: this asset failed in December as an emergency — $87K, 10.5h downtime — the DRO warning 6 days prior was not actioned. M-104 is now at the same point.',
      engineer: 'M-089 run-in normal. Vibration settled from 8 mm/s at install to 1.8 mm/s at 2 months — consistent with seat conformance. Kurtosis 1.8, no BPFO activity. Next inspection: 6-month milestone June 2024.',
      maintenance: 'M-089: clear. Next action June 2024 6-month inspection. No parts to pre-order.',
      safety: 'M-089 safety clearance confirmed. LOTO de-issued post-repair. Zone A — no restrictions.',
      manager: 'M-089 healthy — no action needed.',
      executive: 'M-089 healthy. Its December emergency failure at $87K (vs DRO warning not actioned) is a key reference case for the M-104 approval decision.',
      ot: 'M-089 historian baseline: 1.8 mm/s, 48.2°C. All channels nominal. No issues.'
    }
  },
  { id: 'G-112', nm: 'Gearbox — Line 3', tp: 'Gearbox', br: 'SKF 22318E', kw: 55, rpm: 720, ln: 'Line 3', st: 'healthy', rul: '~43 days', vib: 1.2, tmp: 45.1, bpfo: 0.7, last: '1 week', iso: 'Zone A',
    trend: [6, 5.5, 4, 3, 2.5, 2, 1.8, 1.5, 1.3, 1.2, 1.2, 1.2],
    faults: [{ dt: '2023-12-18', ty: 'Gear Mesh Anomaly', st: 'Replaced (SKF 22318E)', sc: 'var(--t3)' }],
    insights: {
      engineer: [{ l: 'G-112 gear mesh frequency post-replacement?', c: '' }, { l: 'Settling rate normal for SKF 22318E?', c: 'g' }],
      maintenance: [{ l: 'G-112 next oil sample due?', c: '' }],
      ot: [{ l: 'G-112 historian baseline updated?', c: 'g' }]
    },
    chatResp: {
      supervisor: 'G-112 is <strong style="color:var(--gn)">healthy</strong> — 1 week post-replacement. Settling normally. No action needed.',
      engineer: 'G-112 post-replacement run-in normal. Vibration 1.2 mm/s, settling curve consistent with SKF 22318E characteristics. Gear mesh 252 Hz nominal, no sidebands. First oil sample at 500h (~3 weeks).',
      maintenance: 'G-112: next oil sample at 500h (~3 weeks). No other action.',
      ot: 'G-112 historian baseline: 1.2 mm/s, 45.1°C. Thresholds set. All 3 channels nominal.',
      safety: 'G-112 safe. Zone A. No restrictions.',
      manager: 'G-112 healthy — no management concern.',
      executive: 'G-112 healthy.'
    }
  }
];

export const FLEET_TOTAL = ASSETS.length;
