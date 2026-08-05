export const ASSET_SCENARIO = {
  'low confidence': 'fi_hitl_test',
  'no sop': 'knowledge_hitl_test',
  'inner race': 'inner_race_fault',
  'unknown asset': 'unknown_asset',
  // New real-world scenarios (BRG_012, BRG_004, BRG_006, BRG_007, BRG_008)
  'cage fault': 'cage_fault',
  'E-501': 'electrical_anomaly',
  'thermal event': 'thermal_event',
  'borderline stage': 'borderline_fault',
  'L-701': 'load_spike',
  // Friendly display names
  'M-104': 'outer_race_fault',
  'P-207': 'lubrication_issue',
  'C-301': 'signal_dropout',
  'M-089': 'healthy',
  'G-112': 'gearbox_fault',
  // Internal asset IDs from asset_master.json
  'AST_MTR_001': 'outer_race_fault',
  'AST_PMP_001': 'lubrication_issue',
  'AST_MTR_002': 'healthy',
  'AST_GBX_001': 'gearbox_fault',
  'AST_PMP_002': 'lubrication_issue',
  'AST_CON_001': 'inner_race_fault',
  // Asset names
  'Conveyor Motor A': 'outer_race_fault',
  'Cooling Pump A': 'lubrication_issue',
  'Conveyor Motor B': 'healthy',
  'Gearbox Unit': 'gearbox_fault',
};

export const URGENCY_COLOR = { immediate: '#ef4444', urgent: '#f97316', planned: '#3b82f6', monitor: '#6b7280' };

export const SAMPLE_REC = {
  case_id: 'DEMO-M104-001',
  asset_id: 'M-104',
  bearing_id: 'BRG_M104_DRIVE',
  recommended_action: { name: 'stop_and_replace', description: 'Bearing replacement — outer race fault Stage 3', estimated_duration_hours: 6.0 },
  urgency: 'immediate',
  required_parts: [{ part_number: 'SKF6310-ZZ', quantity: 1, lead_time_days: 2 }],
  window_chosen: 'WIN_EMERGENCY',
  rationale: 'Outer race fault at Stage 3 means the bearing has crossed the threshold where monitoring no longer reduces risk — degradation at this stage is non-linear and can accelerate without warning. 847 matching historical cases show median time-to-failure of 4.2 days from this signature. Monitor-only scores 0.31 vs. replacement 0.89 on the prescriptive model — the gap is not close.',
  recommendation_status: 'ok',
  approval_status: 'pending',
  responsible_person: 'Plant Supervisor – James Kowalski',
  responsible_person_id: 'PERSONA_SUP',
  responsible_approver: 'Plant Supervisor – James Kowalski',
  responsible_approver_id: 'PERSONA_SUP',
  contributors: [
    { role: 'Reliability Engineer', name: 'Sarah Chen', concern: 'confirms bearing fault classification' },
    { role: 'Maintenance Planner', name: 'Tom Rodriguez', concern: 'confirms parts availability and crew' },
  ],
  generated_at_utc: new Date().toISOString(),
  // Decision Support fields (A3)
  cost_if_approved: { amount: 48000, breakdown: 'parts ($8k) + labour ($12k) + 6h Line 4 stop ($28k)' },
  cost_if_deferred: { per_hour: 7500, total: 2016000, basis: 'unplanned failure + secondary damage' },
  parts_vs_rul: { eta: '36–48h via PO', rul_window: '0–7d', within_window: true },
  historical_cases: [
    { date: '2024-03-12', action_taken: 'stop_and_replace', outcome: 'success — bearing replaced, line back in 5h' },
    { date: '2023-11-05', action_taken: 'stop_and_replace', outcome: 'success — no secondary damage' },
    { date: '2023-08-19', action_taken: 'monitor', outcome: 'failure — 61h shutdown, secondary shaft damage' },
  ],
  approver_authority: { threshold_usd: 100000, within_authority: true, persona_role: 'Plant Supervisor' },
  alternative_action: { label: 'Monitor and reassess in 48h', trade_off: 'Saves planned stop but accumulates $360k additional risk per day at current degradation rate' },
};
