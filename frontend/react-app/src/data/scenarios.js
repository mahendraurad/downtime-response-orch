export const ASSET_SCENARIO = {
  'low confidence': 'fi_hitl_test',
  'no sop': 'knowledge_hitl_test',
  'inner race': 'inner_race_fault',
  'unknown asset': 'unknown_asset',
  'M-104': 'outer_race_fault',
  'P-207': 'lubrication_issue',
  'C-301': 'signal_dropout',
  'M-089': 'healthy',
  'G-112': 'gearbox_fault',
};

export const URGENCY_COLOR = { immediate: '#ef4444', urgent: '#f97316', planned: '#3b82f6', monitor: '#6b7280' };

export const SAMPLE_REC = {
  case_id: 'DEMO-004',
  asset_id: 'AST_PMP_001',
  bearing_id: 'BRG_005',
  recommended_action: { name: 'lubrication_service', description: 'lubrication_service (in_window) per SOP_003', estimated_duration_hours: 3.0 },
  urgency: 'planned',
  required_parts: [{ part_number: 'MOBIL-DTE-25', quantity: 1, lead_time_days: 0 }],
  window_chosen: 'WIN_005',
  rationale: "Lubrication Issue detected at severity 'stage_2' on bearing BRG_005 (asset AST_PMP_001). Remaining useful life estimate: ~14 days. Stage 2 indicates active degradation — action required soon.",
  recommendation_status: 'ok',
  approval_status: 'pending',
  responsible_person: 'Plant Supervisor – James Kowalski',
  responsible_person_id: 'PERSONA_SUP',
  responsible_approver: 'Plant Supervisor – James Kowalski',
  responsible_approver_id: 'PERSONA_SUP',
  contributors: [{ role: 'Maintenance Planner', name: 'Tom Rodriguez', concern: 'confirms parts and crew readiness' }],
  generated_at_utc: new Date().toISOString(),
};
