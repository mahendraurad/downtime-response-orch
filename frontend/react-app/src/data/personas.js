import { ASSETS } from './assets';
const N = ASSETS.length; // fleet total — single source of truth

export const PD = {
  supervisor: {
    nm: 'Plant Supervisor — James Kowalski',
    ds: 'Free-form reasoning: fault risk, shift decisions, escalations, run/defer trade-offs',
    av: '🦺', bg: 'var(--odm)', col: 'var(--or)',
    gr: 'Good morning James. Sweep complete on Line 4.\n\n<strong style="color:var(--t)">M-104 is your urgent priority</strong> — outer race spall Stage 3, RUL 5–8 days. Without action by Wednesday, failure probability by Saturday reaches 67%.\n\nBelow are your key actions. Ask me anything about risk, decisions, or what-if scenarios.',
    actions: [
      { icon: '⚡', lbl: 'Critical decision', nm: 'Approve WO-2024-1847', col: 'rgba(255,77,106,.1)', bc: 'rgba(255,77,106,.35)', tc: 'var(--rd)', q: "Approve WO-2024-1847 — confirm crew deployment Wednesday 06:00 and release all logistics" },
      { icon: '📊', lbl: 'Risk assessment', nm: 'Run-to-Weekend Model', col: 'rgba(255,183,64,.1)', bc: 'rgba(255,183,64,.35)', tc: 'var(--am)', q: "Model the risk of running M-104 to Saturday: failure probability curve, consequence if it seizes, cost difference vs acting Wednesday" },
      { icon: '📤', lbl: 'Management escalation', nm: 'Escalate to Plant Manager', col: 'rgba(167,139,250,.1)', bc: 'rgba(167,139,250,.35)', tc: 'var(--pu)', q: "Escalate M-104 to Plant Manager Sarah Chen: fault severity, production exposure $520–750K, recommended action and approval needed" },
      { icon: '📋', lbl: 'Shift management', nm: 'Generate Shift Handover', col: 'rgba(46,204,138,.1)', bc: 'rgba(46,204,138,.35)', tc: 'var(--gn)', q: "Generate complete shift handover brief for Line 4: all active alerts, current status, pending approvals, priorities for incoming supervisor" },
      { icon: '🗓', lbl: 'Planning', nm: "This Week's Action Plan", col: 'rgba(79,142,255,.12)', bc: 'rgba(79,142,255,.35)', tc: 'var(--ac2)', q: "Give me the complete action plan for this week across M-104, P-207 and C-301 — what do I need to do and by when?" },
      { icon: '💬', lbl: 'Operator communication', nm: 'Brief Line 4 Operators', col: 'rgba(255,122,53,.1)', bc: 'rgba(255,122,53,.3)', tc: 'var(--or)', q: "What should I tell the Line 4 operators about M-104? What signs to watch for and when to call me immediately?" }
    ],
    qp: ['Can M-104 safely run until Saturday?', 'What happens if M-104 fails catastrophically mid-shift?', 'Total risk exposure across Line 4 this week?', 'What is the cost of not acting on M-104 today?'],
    tags: ['📍 Line 4 · M-104', '⚠ Outer Race · Stage 3', '⏱ RUL ~6 days']
  },
  engineer: {
    nm: 'Reliability Engineer — Priya Nair',
    ds: 'Fault diagnostics: FFT analysis, RUL modelling, root cause, fleet comparison, prognostics',
    av: '🔧', bg: 'var(--ag)', col: 'var(--ac)',
    gr: 'Hello Priya. M-104 diagnostic summary:\n\n<strong style="color:var(--t)">BPFO 4.02× baseline at 87.4 Hz</strong> — outer race spall Stage 3, 25–40% surface damage estimated. Kurtosis 6.8, Crest Factor 9.2. Three KB case matches retrieved. Ask me anything technical.',
    actions: [
      { icon: '📊', lbl: 'Spectral analysis', nm: 'Full FFT Harmonic Report', col: 'rgba(79,142,255,.12)', bc: 'rgba(79,142,255,.35)', tc: 'var(--ac2)', q: "Full FFT analysis for M-104: BPFO harmonic family at 1×/2×/3×, sideband structure at ±shaft frequency, spectral kurtosis map, and fault stage conclusion" },
      { icon: '📐', lbl: 'Prognosis', nm: 'RUL Confidence Intervals', col: 'rgba(255,183,64,.1)', bc: 'rgba(255,183,64,.35)', tc: 'var(--am)', q: "RUL confidence intervals for M-104: median 6.4 days — show 10th/90th percentile bounds and sensitivity to load and temperature" },
      { icon: '🔍', lbl: 'Root cause', nm: 'Fault Initiation Analysis', col: 'rgba(255,77,106,.1)', bc: 'rgba(255,77,106,.35)', tc: 'var(--rd)', q: "Root cause analysis for M-104 outer race spall: rank initiation factors — misalignment, overload, lube starvation, installation error, contamination ingress" },
      { icon: '📁', lbl: 'Historical comparison', nm: 'Match KB Case Studies', col: 'rgba(46,204,138,.1)', bc: 'rgba(46,204,138,.35)', tc: 'var(--gn)', q: "Compare M-104 signature to 3 KB case matches: similarity score, what happened in those cases, time-to-failure from this stage" },
      { icon: '⚙', lbl: 'Fleet health', nm: 'Fleet Bearing Trend Analysis', col: 'rgba(167,139,250,.1)', bc: 'rgba(167,139,250,.35)', tc: 'var(--pu)', q: "Analyse bearing health trends across all monitored rotating assets: which are in degrading trajectories and what is the 30-day outlook?" },
      { icon: '🎯', lbl: 'QA specification', nm: 'Post-Repair Baseline Spec', col: 'rgba(45,212,191,.1)', bc: 'rgba(45,212,191,.3)', tc: 'var(--tl)', q: "What vibration and temperature baselines should be set post-repair on M-104? Specify target values for a new SKF 6310-2RS at this operating point" }
    ],
    qp: ['Full FFT harmonic analysis for M-104', 'RUL confidence interval and uncertainty band?', 'Root cause of M-104 outer race initiation?', 'Compare M-104 to the 3 KB cases'],
    tags: ['📍 M-104 · BPFO 4.02×', '🔬 Stage 3 · Kurtosis 6.8', '📈 CI: 4.1–9.3d · 82% conf']
  },
  maintenance: {
    nm: 'Maintenance Planner — Tom Rodriguez',
    ds: 'Scheduling, parts availability, crew allocation, repair sequencing, MRO logistics',
    av: '🛠', bg: 'var(--gd)', col: 'var(--gn)',
    gr: 'Hi Tom. Two active interventions this week:\n\n<strong style="color:var(--t)">M-104 — Urgent:</strong> Wed 06:00–10:00. SKF 6310-2RS in stock (Bin A-14). 2 techs, 4h.\n<strong style="color:var(--t)">P-207 — Scheduled:</strong> Wed 09:00, 2h, lube check. T. Rodriguez assigned.\n\nAsk me about parts, crew, or scheduling.',
    actions: [
      { icon: '📦', lbl: 'Inventory check', nm: 'Confirm All Parts Ready', col: 'rgba(46,204,138,.1)', bc: 'rgba(46,204,138,.35)', tc: 'var(--gn)', q: "Confirm all parts and tools are available for M-104 Wednesday: SKF 6310-2RS, Mobil SHC 100, seal kit, bearing puller, press, torque wrench — anything still to collect?" },
      { icon: '👥', lbl: 'Crew planning', nm: 'Assign Crew Wednesday', col: 'rgba(79,142,255,.12)', bc: 'rgba(79,142,255,.35)', tc: 'var(--ac2)', q: "Assign crew for M-104 Wednesday 06:00: confirm T.Rodriguez (lead) and K.Mensah are available and LOTO-qualified. Any gaps to fill?" },
      { icon: '📅', lbl: 'Schedule optimisation', nm: 'Optimise Weekly Schedule', col: 'rgba(255,183,64,.1)', bc: 'rgba(255,183,64,.35)', tc: 'var(--am)', q: "Optimise this week's maintenance schedule: M-104 urgent Wed 06:00, P-207 lube Wed 09:00, C-301 cage Thu 14:00 — best sequence to minimise Line 4 disruption" },
      { icon: '🖨', lbl: 'Documentation', nm: 'Print M-104 Job Pack', col: 'rgba(255,122,53,.1)', bc: 'rgba(255,122,53,.3)', tc: 'var(--or)', q: "Generate complete job pack for M-104: SOP steps, parts list with bin locations, tools, torque specs (85 Nm), lube type (Mobil SHC 100), LOTO reference" },
      { icon: '🔄', lbl: 'Procurement', nm: 'Check Emergency Spare Stock', col: 'rgba(255,77,106,.1)', bc: 'rgba(255,77,106,.35)', tc: 'var(--rd)', q: "Check if additional emergency bearing stock is needed given current RUL projections for M-104, P-207 and C-301. Any reorder points being approached?" },
      { icon: '📊', lbl: 'Cost tracking', nm: 'Labour & Cost Estimate', col: 'rgba(167,139,250,.1)', bc: 'rgba(167,139,250,.35)', tc: 'var(--pu)', q: "Estimate total labour and parts cost for all 3 active interventions this week: M-104, P-207, C-301. Compare against preventive maintenance budget." }
    ],
    qp: ['Confirm SKF 6310-2RS availability for M-104', 'Who is available Wednesday 06:00 — LOTO qualified?', 'Best sequence for M-104, P-207 and C-301 this week?', 'Full job pack for M-104 replacement'],
    tags: ['📍 M-104 · Wed 06:00', '📦 SKF 6310-2RS · Bin A-14', '👥 2 techs · LOTO req.']
  },
  manager: {
    nm: 'Plant Manager — Sarah Chen',
    ds: 'Production impact, cost trade-offs, OEE effects, line-level risk decisions',
    av: '📊', bg: 'var(--pdm)', col: 'var(--pu)',
    gr: 'Good morning Sarah. <strong style="color:var(--t)">M-104 needs a decision today.</strong>\n\nUnplanned failure by Saturday: 67%. Line 4 impact: 4–6h stop (~$520–750K). Planned Wednesday: $18K. Net avoidance: <strong style="color:var(--gn)">$601K</strong>. All logistics are ready. Your key actions are below.',
    actions: [
      { icon: '✓', lbl: 'Approval needed', nm: 'Approve WO-2024-1847', col: 'rgba(79,142,255,.12)', bc: 'rgba(79,142,255,.35)', tc: 'var(--ac2)', q: "Approve WO-2024-1847 at Plant Manager level — confirm Wednesday window is authorised and all resources committed" },
      { icon: '💰', lbl: 'Financial decision', nm: 'Cost-Benefit Analysis', col: 'rgba(46,204,138,.1)', bc: 'rgba(46,204,138,.35)', tc: 'var(--gn)', q: "Full cost-benefit for M-104: planned $18K vs emergency $619K+ — show detailed breakdown, net avoidance and ROI case" },
      { icon: '📈', lbl: 'OEE modelling', nm: 'Line 4 OEE Impact Model', col: 'rgba(255,183,64,.1)', bc: 'rgba(255,183,64,.35)', tc: 'var(--am)', q: "Model Line 4 OEE: 4h planned window Wednesday vs 12–18h unplanned failure. Include monthly target deviation and recovery time" },
      { icon: '⚠', lbl: 'Portfolio risk', nm: 'Total Risk Exposure This Week', col: 'rgba(255,77,106,.1)', bc: 'rgba(255,77,106,.35)', tc: 'var(--rd)', q: "Total production risk exposure this week from M-104, P-207 and C-301: combined financial exposure, probability-weighted impact, top priorities" },
      { icon: '🔄', lbl: 'Contingency', nm: 'Contingency Plan if M-104 Fails', col: 'rgba(167,139,250,.1)', bc: 'rgba(167,139,250,.35)', tc: 'var(--pu)', q: "What is the contingency plan if M-104 fails before Wednesday? Emergency response, crew mobilisation, production re-routing, customer impact" },
      { icon: '📰', lbl: 'Reporting', nm: 'Ops Risk Briefing Report', col: 'rgba(255,122,53,.1)', bc: 'rgba(255,122,53,.3)', tc: 'var(--or)', q: "Draft an operations risk briefing: M-104 situation, production exposure, recommended action and decision needed from leadership" }
    ],
    qp: ['Full production impact if M-104 fails this week?', 'Cost comparison: planned vs emergency?', 'Line 4 OEE model — planned window vs failure?', 'Total risk exposure across all active alerts?'],
    tags: ['📍 Line 4 · HIGH risk', '💰 $601K net avoidance', '📈 OEE impact: 0.4–0.7pp']
  },
  executive: {
    nm: 'VP Operations — Michael Osei',
    ds: 'Portfolio risk, reliability KPIs, financial returns, board-level metrics, DRO value delivery',
    av: '👔', bg: 'var(--adm)', col: 'var(--am)',
    gr: `Good morning Michael. <strong style="color:var(--t)">Fleet: 2 critical, 5 warnings</strong> across ${N} assets. MTD: 3 failures prevented, ~$1.4M avoided. M-104 exposure: $500K–$750K unmanaged. Q3 MTBF up 18%. Key actions below.`,
    actions: [
      { icon: '📊', lbl: 'KPI review', nm: 'Q3 Reliability Performance', col: 'rgba(79,142,255,.12)', bc: 'rgba(79,142,255,.35)', tc: 'var(--ac2)', q: "Q3 reliability KPI report: MTBF trend, MTTR, unplanned downtime %, OEE contribution, DRO avoidance value — are we on target?" },
      { icon: '💼', lbl: 'Board preparation', nm: 'Board Briefing Pack', col: 'rgba(255,183,64,.1)', bc: 'rgba(255,183,64,.35)', tc: 'var(--am)', q: "Board-ready briefing: DRO deployment impact, YTD financial returns, fleet health, Q3 vs target, forward risk outlook for Q4" },
      { icon: '💰', lbl: 'Financial return', nm: 'YTD DRO Value Delivered', col: 'rgba(46,204,138,.1)', bc: 'rgba(46,204,138,.35)', tc: 'var(--gn)', q: "YTD DRO value: avoidance events, total savings, MTBF improvement, maintenance cost reduction — vs deployment investment cost" },
      { icon: '🌍', lbl: 'Portfolio view', nm: 'Fleet Risk Heat Map', col: 'rgba(255,77,106,.1)', bc: 'rgba(255,77,106,.35)', tc: 'var(--rd)', q: `Fleet risk map: all ${N} assets by health status, combined financial exposure by line, top 5 assets needing leadership attention this month` },
      { icon: '📉', lbl: 'Trend analysis', nm: 'Downtime Trend Since DRO', col: 'rgba(167,139,250,.1)', bc: 'rgba(167,139,250,.35)', tc: 'var(--pu)', q: "How has unplanned downtime trended since DRO deployment? Show monthly comparison, bearing MTBF change, unplanned ratio reduction" },
      { icon: '🏭', lbl: 'Strategic', nm: 'Expansion Opportunity', col: 'rgba(45,212,191,.1)', bc: 'rgba(45,212,191,.3)', tc: 'var(--tl)', q: "Based on DRO results so far, what is the business case for expanding to adjacent asset classes (pumps, conveyors, gearboxes) across other plants?" }
    ],
    qp: ['Total financial risk exposure this week?', 'Q3 reliability KPI performance vs targets?', 'YTD DRO value delivered — what is the ROI?', 'Bearing MTBF trend since DRO deployment?'],
    tags: [`📍 ${N} assets · 2 critical`, '💰 $1.4M avoided MTD', '📈 MTBF +18% vs baseline']
  },
  ot: {
    nm: 'OT / Controls — Raj Iyer',
    ds: 'Signal health, historian integrity, CMMS sync, sensor calibration, data pipeline validation',
    av: '📡', bg: 'var(--rdm)', col: 'var(--rd)',
    gr: 'Hi Raj. Integration status:\n\n<strong style="color:var(--t)">M-104: 4/4 channels nominal.</strong> Historian: 99.7% coverage last 30d.\n\n<strong style="color:var(--rd)">C-301 CH-31B dropout: 3.2% missing.</strong> Junction box J-301 — probable connector fault. SAP PM: synced 4m ago.',
    actions: [
      { icon: '📡', lbl: 'Signal audit', nm: 'Full Fleet Signal Report', col: 'rgba(79,142,255,.12)', bc: 'rgba(79,142,255,.35)', tc: 'var(--ac2)', q: `Full signal quality audit: all ${N} assets, each channel status, historian coverage %, dropout events, calibration currency, immediate issues` },
      { icon: '🔧', lbl: 'Fault diagnosis', nm: 'Diagnose C-301 Dropout', col: 'rgba(255,77,106,.1)', bc: 'rgba(255,77,106,.35)', tc: 'var(--rd)', q: "Diagnose C-301 CH-31B dropout: analyse pattern, probable cause at J-301, impact on DRO prediction confidence, recommended fix" },
      { icon: '🔗', lbl: 'Integration health', nm: 'CMMS Integration Check', col: 'rgba(46,204,138,.1)', bc: 'rgba(46,204,138,.35)', tc: 'var(--gn)', q: "SAP PM CMMS health: sync frequency, last write, failed writes last 24h, data completeness, outstanding issues to resolve" },
      { icon: '📏', lbl: 'Baseline config', nm: 'Post-Repair Baseline Setup', col: 'rgba(255,183,64,.1)', bc: 'rgba(255,183,64,.35)', tc: 'var(--am)', q: "What historian baselines and alert thresholds need configuring after M-104 bearing replacement? Target values for new SKF 6310-2RS" },
      { icon: '✅', lbl: 'Data validation', nm: 'Validate M-104 Pipeline', col: 'rgba(167,139,250,.1)', bc: 'rgba(167,139,250,.35)', tc: 'var(--pu)', q: "Validate M-104 data pipeline integrity: check for any gaps, calibration drift, signal anomalies, or CMMS sync issues in the last 30 days" },
      { icon: '🛎', lbl: 'Alert config', nm: 'Review Alert Thresholds', col: 'rgba(45,212,191,.1)', bc: 'rgba(45,212,191,.3)', tc: 'var(--tl)', q: "Review vibration and temperature alert thresholds across all critical assets — are they correctly calibrated for each bearing type and operating condition?" }
    ],
    qp: [`Full signal quality report — all ${N} assets?`, 'C-301 CH-31B dropout: cause and fix?', 'CMMS integration health — any failed writes?', 'Validate M-104 data pipeline for completeness?'],
    tags: ['📡 M-104 signals: 4/4 OK', '⚠ C-301 CH-31B dropout', '🔗 SAP PM: synced 4m ago']
  },
  safety: {
    nm: 'Safety Officer — Lisa Park',
    ds: 'LOTO procedures, permit validation, PPE requirements, ISO compliance, crew qualifications',
    av: '🛡', bg: 'var(--tdm)', col: 'var(--tl)',
    gr: 'Hello Lisa. Safety status for active interventions:\n\n<strong style="color:var(--t)">M-104 — LOTO EL-104-A required.</strong> Zone D — mandatory isolation. PPE: Class B hearing + vibration gloves.\n\n<strong style="color:var(--am)">C-301 Thu:</strong> Verify IL-301-B currency before crew dispatch.',
    actions: [
      { icon: '📋', lbl: 'Pre-task safety', nm: 'M-104 Safety Brief', col: 'rgba(45,212,191,.1)', bc: 'rgba(45,212,191,.3)', tc: 'var(--tl)', q: "Generate pre-task safety briefing for M-104 bearing replacement: hazards, LOTO EL-104-A steps, isolation points, PPE requirements, emergency contacts" },
      { icon: '🔒', lbl: 'Permit validation', nm: 'Validate All Active Permits', col: 'rgba(79,142,255,.12)', bc: 'rgba(79,142,255,.35)', tc: 'var(--ac2)', q: "Validate all active safety permits for this week: LOTO EL-104-A (M-104) and IL-301-B (C-301) — are both current and do they cover the planned scope?" },
      { icon: '✅', lbl: 'Compliance audit', nm: 'ISO 10816-3 Fleet Audit', col: 'rgba(255,183,64,.1)', bc: 'rgba(255,183,64,.35)', tc: 'var(--am)', q: "ISO 10816-3 compliance audit: which assets are in Zone C or D, what mandatory actions are required, current compliance status across fleet" },
      { icon: '👷', lbl: 'Crew safety', nm: 'Crew Qualification Check', col: 'rgba(46,204,138,.1)', bc: 'rgba(46,204,138,.35)', tc: 'var(--gn)', q: "Verify safety qualifications for M-104 crew: LOTO certification status and expiry for T.Rodriguez and K.Mensah, PPE availability, training gaps" },
      { icon: '⚠', lbl: 'Risk assessment', nm: 'Pre-Job Risk Assessment', col: 'rgba(255,77,106,.1)', bc: 'rgba(255,77,106,.35)', tc: 'var(--rd)', q: "Conduct pre-job risk assessment for M-104 bearing replacement: all hazards, likelihood, severity, controls in place, residual risk level" },
      { icon: '📊', lbl: 'Compliance reporting', nm: 'Weekly Safety Compliance Report', col: 'rgba(167,139,250,.1)', bc: 'rgba(167,139,250,.35)', tc: 'var(--pu)', q: "Weekly safety compliance report: all active jobs this week, permit status, LOTO in place, any near-misses or observations to log" }
    ],
    qp: ['All safety requirements for M-104 intervention?', 'Is LOTO EL-104-A current — what does it cover?', 'ISO 10816-3 compliance status across the fleet?', 'Pre-task safety brief for M-104 crew?'],
    tags: ['🛡 LOTO EL-104-A req.', '⚠ Zone D · ISO 10816-3', '✅ No confined space']
  }
};
