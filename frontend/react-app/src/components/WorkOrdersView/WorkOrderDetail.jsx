import React, { useContext, useState } from 'react';
import { AppContext } from '../../context/AppContext';
import { patchWorkOrder } from '../../api/workOrders';
import { askChat } from '../../api/chat';
import { authedFetch } from '../../api/http';
import { API } from '../../config/api';

const PR_COLOR = { URGENT: 'var(--rd)', MEDIUM: 'var(--am)', LOW: 'var(--gn)' };
const PR_BG    = { URGENT: 'var(--rdm)', MEDIUM: 'var(--adm)', LOW: 'var(--gd)' };

const WO_ASSIGNEES = {
  'Pending':     'Plant Supervisor / Plant Manager / VP Operations',
  'Scheduled':   'Plant Supervisor',
  'In Progress': 'Plant Supervisor / Maintenance Planner',
};

// actions — real system actions (status transitions, export). Rendered as buttons.
// chips   — suggested AI questions. Rendered as small query chips below.
const WO_PERSONA_ACTS = {
  supervisor: {
    pending:     {
      actions: [
        { l: '✓ Approve WO',  cls: 'wpablu', newSt: 'Scheduled' },
        { l: '✗ Reject WO',   cls: 'wpared', newSt: 'Rejected'  },
      ],
      chips:   ['Why did DRO create this WO? Explain the fault evidence and urgency rating', 'What further evidence or conditions are needed before approving?'],
    },
    scheduled:   {
      actions: [{ l: '▶ Mark In Progress', cls: 'wpablu', newSt: 'In Progress' }],
      chips:   ['What is the next available maintenance window without increasing failure risk?'],
    },
    in_progress: {
      actions: [{ l: '✓ Mark Complete', cls: 'wpagry', newSt: 'Closed' }],
      chips:   ['What is the current execution status of this work order?'],
    },
    closed:      {
      actions: [],
      chips:   ['Full closure report: fault found on teardown, repair performed, post-repair baseline', 'Should we create a follow-up 6-month inspection work order?'],
    },
    rejected:    { actions: [], chips: ['What conditions would need to be met to resubmit this work order?'] },
  },
  engineer: {
    pending:     {
      actions: [{ l: '📊 Export Pre-Repair Data', cls: 'wpablu', export: true }],
      chips:   ['What fault data confirms this WO is the right action? Show FFT evidence, kurtosis, crest factor', 'What post-repair vibration and temperature baselines should be set for this bearing?'],
    },
    scheduled:   {
      actions: [{ l: '📊 Export Pre-Repair Data', cls: 'wpablu', export: true }],
      chips:   ['SOP for this WO: torque spec, press fit procedure, lubrication type and volume'],
    },
    in_progress: {
      actions: [],
      chips:   ['What is the technical status of this repair? Any issues found on teardown?'],
    },
    closed:      {
      actions: [],
      chips:   ['Compare pre-repair vs post-repair baseline — did readings normalise as expected?', 'How should this WO outcome update the RUL prediction model?'],
    },
    rejected:    { actions: [], chips: ['What conditions would need to be met to resubmit this work order?'] },
  },
  maintenance: {
    pending:     {
      actions: [],
      chips:   ['Confirm all parts and tools are staged for this WO — anything still to collect?', 'Crew availability and LOTO qualifications needed for this work order?'],
    },
    scheduled:   {
      actions: [],
      chips:   ['Complete job pack: SOP steps, parts with bin locations, tools, torque specs, LOTO reference', 'Is the time estimate accurate for this WO? Should we allow more buffer?'],
    },
    in_progress: {
      actions: [{ l: '✓ Mark Complete', cls: 'wpagry', newSt: 'Closed' }],
      chips:   ['Log actual hours and parts used vs estimate'],
    },
    closed:      {
      actions: [],
      chips:   ['Log actual hours and parts used vs estimate — update duration benchmark for similar jobs', 'Do any parts consumed need reordering? Check stock vs reorder threshold'],
    },
    rejected:    { actions: [], chips: ['What conditions would need to be met to resubmit this work order?'] },
  },
  manager: {
    pending:     {
      actions: [
        { l: '✓ Approve from Ops Level', cls: 'wpablu', newSt: 'Scheduled' },
        { l: '✗ Reject WO',             cls: 'wpared', newSt: 'Rejected'  },
      ],
      chips:   ['Full cost-benefit: planned vs emergency — detailed breakdown and net avoidance case'],
    },
    scheduled:   {
      actions: [],
      chips:   ['How does the maintenance window affect line throughput and daily OEE targets?', 'Contingency if this WO overruns: backup crew, schedule recovery, production re-route'],
    },
    in_progress: {
      actions: [],
      chips:   ['What is the current execution status and estimated completion time?'],
    },
    closed:      {
      actions: [],
      chips:   ['Final cost vs estimate — any overruns? Update maintenance budget tracker', 'How did the planned window affect monthly OEE vs an unplanned failure scenario?'],
    },
    rejected:    { actions: [], chips: ['What conditions would need to be met to resubmit this work order?'] },
  },
  executive: {
    pending:     {
      actions: [{ l: '✓ Executive Approval', cls: 'wpablu', newSt: 'Scheduled' }],
      chips:   ['Probability-weighted financial exposure if this WO is not approved today'],
    },
    scheduled:   {
      actions: [],
      chips:   ['How does this WO contribute to Q3 reliability KPIs and DRO ROI metrics?'],
    },
    in_progress: {
      actions: [],
      chips:   ['What is the execution timeline and any risks to on-time completion?'],
    },
    closed:      {
      actions: [],
      chips:   ['Total downtime avoidance value: planned completion vs emergency failure scenario', 'Add this WO outcome to YTD DRO performance metrics and avoidance tracker'],
    },
    rejected:    { actions: [], chips: ['What conditions would need to be met to resubmit this work order?'] },
  },
  ot: {
    pending:     {
      actions: [],
      chips:   ['Are all sensors on this asset healthy and ready for maintenance? Any calibration issues?', 'Is CMMS ready to receive the approval update? Any integration issues?'],
    },
    scheduled:   {
      actions: [],
      chips:   ['Ensure data logging continuity on this asset during the maintenance window', 'Historian baselines and alert thresholds to configure after completion'],
    },
    in_progress: {
      actions: [],
      chips:   ['Are sensors logging correctly during the maintenance window?'],
    },
    closed:      {
      actions: [],
      chips:   ['Did readings normalise as expected after completion? Compare pre vs post across all channels', 'Archive the complete data record: vibration trend, fault event, post-repair baseline'],
    },
    rejected:    { actions: [], chips: ['What conditions would need to be met to resubmit this work order?'] },
  },
  safety: {
    pending:     {
      actions: [],
      chips:   ['Validate all safety permits: LOTO current, full scope, crew LOTO-certified?', 'Pre-task safety brief for crew: hazards, LOTO steps, PPE, isolation points, emergency contacts'],
    },
    scheduled:   {
      actions: [],
      chips:   ['Safety sign-off checklist: LOTO in place, crew briefed, PPE available, cleared to start?', 'Verify LOTO covers all energy sources for this asset'],
    },
    in_progress: {
      actions: [],
      chips:   ['Is the work proceeding safely? Any LOTO deviations or safety concerns to flag?'],
    },
    closed:      {
      actions: [],
      chips:   ['Log safety compliance record: all permits completed, LOTO de-issued, no incidents', 'Any near-misses, LOTO deviations or safety observations during this WO to document?'],
    },
    rejected:    { actions: [], chips: ['What conditions would need to be met to resubmit this work order?'] },
  },
};

export default function WorkOrderDetail({ wo }) {
  const { persona, updateWOStatus, updateAssetPipelineState, updateAssetStatus } = useContext(AppContext);
  const [checklist, setChecklist]   = useState(() => wo ? wo.cl.map(item => ({ ...item })) : []);
  const [chipAnswers, setChipAnswers] = useState({});
  const [chipLoading, setChipLoading] = useState(false);
  const [activeChipIdxs, setActiveChipIdxs] = useState({});
  const [loadingForPersona, setLoadingForPersona] = useState(null);
  const chipAnswer = chipAnswers[persona] ?? null;
  const activeChipIdx = activeChipIdxs[persona] ?? null;
  const isThinking = chipLoading && loadingForPersona === persona;
  const [exporting, setExporting]   = useState(false);

  React.useEffect(() => {
    if (wo) setChecklist(wo.cl.map(item => ({ ...item })));
    setChipAnswers({});
    setActiveChipIdxs({});
  }, [wo && wo.id]);

  async function handleChipClick(q, idx) {
    if (chipLoading) return;
    if (activeChipIdx === idx && chipAnswer !== null) {
      setChipAnswers(prev => ({ ...prev, [persona]: null }));
      setActiveChipIdxs(prev => ({ ...prev, [persona]: null }));
      return;
    }
    const callingPersona = persona;
    setChipLoading(true);
    setLoadingForPersona(callingPersona);
    setActiveChipIdxs(prev => ({ ...prev, [callingPersona]: idx }));
    setChipAnswers(prev => ({ ...prev, [callingPersona]: null }));
    try {
      const prefix = wo
        ? `[Work Order: ${wo.id} | Asset: ${wo.as} | Status: ${wo.st} | Priority: ${wo.pr} | RUL: ${wo.rul} | Risk: ${wo.risk}]\n\n`
        : '';
      const data = await askChat({ message: prefix + q, persona: callingPersona });
      setChipAnswers(prev => ({ ...prev, [callingPersona]: data.response || data.message || 'No answer returned.' }));
    } catch {
      setChipAnswers(prev => ({ ...prev, [callingPersona]: 'Could not fetch answer. Please try again.' }));
    } finally {
      setChipLoading(false);
      setLoadingForPersona(null);
    }
  }

  async function handleExport(assetId) {
    if (exporting) return;
    setExporting(true);
    try {
      const resp = await authedFetch(`${API}/api/assets/${encodeURIComponent(assetId)}/export/pre-repair`);
      if (!resp.ok) { alert('Export failed — asset data not found.'); return; }
      const blob = await resp.blob();
      const url  = URL.createObjectURL(blob);
      const a    = document.createElement('a');
      a.href     = url;
      a.download = `${assetId}_pre-repair_${new Date().toISOString().slice(0, 10)}.csv`;
      a.click();
      URL.revokeObjectURL(url);
    } catch {
      alert('Export failed. Please try again.');
    } finally {
      setExporting(false);
    }
  }

  if (!wo) return <div className="wodhdr"><div style={{ padding: '16px', color: 'var(--t3)' }}>Select a work order</div></div>;

  const pc = PR_COLOR[wo.pr] || 'var(--t3)';
  const pb = PR_BG[wo.pr]   || 'rgba(255,255,255,.04)';
  const sc = wo.st === 'Pending'     ? 'var(--am)'  : wo.st === 'Scheduled'    ? 'var(--ac2)'
           : wo.st === 'Rejected'   ? 'var(--rd)'  : wo.st === 'Closed'       ? 'var(--t3)'
           : 'var(--gn)';
  const sb = wo.st === 'Pending'     ? 'var(--adm)' : wo.st === 'Scheduled'    ? 'var(--ag)'
           : wo.st === 'Rejected'   ? 'var(--rdm)' : 'rgba(255,255,255,.04)';

  const STATUS_ORDER   = { Pending: 0, Scheduled: 1, 'In Progress': 2, Closed: 3, Rejected: 4 };
  const currentOrder   = STATUS_ORDER[wo.st] ?? 0;
  const stateKey       = wo.st === 'Closed'      ? 'closed'
                       : wo.st === 'Rejected'    ? 'rejected'
                       : wo.st === 'Pending'     ? 'pending'
                       : wo.st === 'In Progress' ? 'in_progress'
                       : 'scheduled';

  const personaActs = (WO_PERSONA_ACTS[persona] || WO_PERSONA_ACTS.supervisor)[stateKey]
                   || { actions: [], chips: [] };
  const { actions, chips } = personaActs;

  function toggleChecklist(i) {
    const newDone = !checklist[i].ck;
    setChecklist(prev => prev.map((item, idx) => idx === i ? { ...item, ck: newDone } : item));
    patchWorkOrder(wo.id, { checklist_index: i, checklist_done: newDone }).catch(() => {});
  }

  return (
    <>
      <div className="wodhdr">
        <div className="wodbdg">
          <span className="pill" style={{ background: pb, color: pc, border: `1px solid ${pc}40` }}>{wo.pr}</span>
          <span className="pill" style={{ background: sb, color: sc, border: `1px solid ${sc}40` }}>{wo.st.toUpperCase()}</span>
          <span className="pill" style={{ background: 'rgba(255,255,255,.04)', color: 'var(--t3)', border: '1px solid var(--b)' }}>{wo.tp}</span>
        </div>
        <div className="wodtt">{wo.id} — {wo.ti}</div>
        <div className="wodsu">Created by {wo.by} · {wo.as} · Est. {wo.est}</div>

        {/* Real action buttons */}
        {actions.length > 0 && (
          <div className="wod-pacts">
            {actions.map((act, i) => {
              const alreadyApplied = act.newSt && (STATUS_ORDER[act.newSt] ?? 0) <= currentOrder;
              return (
                <button
                  key={i}
                  className={`wpa ${act.cls}`}
                  disabled={alreadyApplied || (act.export && exporting)}
                  title={act.newSt ? `Move this work order to "${act.newSt}"` : act.export ? 'Download pre-repair sensor data as CSV' : act.l}
                  style={alreadyApplied ? { opacity: 0.35, cursor: 'not-allowed' } : {}}
                  onClick={() => {
                    if (act.newSt) {
                      updateWOStatus(wo.id, act.newSt, persona);
                      if (act.newSt === 'Closed' && wo.as) {
                        updateAssetPipelineState(wo.as, { woClosed: true });
                        updateAssetStatus(wo.as, 'healthy');
                      }
                    }
                    if (act.export) handleExport(wo.as);
                  }}
                >
                  {act.export && exporting ? 'Exporting…' : act.l}
                </button>
              );
            })}
          </div>
        )}

        {/* AI query chips */}
        {chips.length > 0 && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '5px', marginTop: actions.length ? '10px' : '10px' }}>
            {chips.map((q, i) => (
              <button
                key={i}
                className="wo-chip"
                disabled={chipLoading}
                onClick={() => handleChipClick(q, i)}
              >
                {isThinking && activeChipIdx === i ? '…' : q}
              </button>
            ))}
          </div>
        )}

        {/* Inline AI answer */}
        {(isThinking || chipAnswer) && (
          <div style={{ margin: '10px 0 0', padding: '12px 14px', borderRadius: '8px', background: 'var(--sf)', border: '1px solid var(--b)', borderLeft: '3px solid var(--ac2)', fontSize: '12px', color: 'var(--t2)', lineHeight: '1.7' }}>
            {isThinking
              ? <span style={{ color: 'var(--t3)', fontStyle: 'italic' }}>Agent thinking…</span>
              : chipAnswer}
          </div>
        )}
      </div>

      <div className="wodbody">
        <div className="dsec">
          <div className="sttl">Work Order Summary</div>
          <div className="dg3">
            <div className="dc"><div className="dcl">Asset RUL</div>
              <div className="dcv" style={{ color: wo.rul && wo.rul.includes('6') ? 'var(--rd)' : wo.rul && wo.rul.includes('~') ? 'var(--am)' : 'var(--t3)' }}>{wo.rul}</div>
            </div>
            <div className="dc"><div className="dcl">Risk Level</div>
              <div className="dcv" style={{ color: wo.risk === 'HIGH' ? 'var(--rd)' : wo.risk === 'MEDIUM' ? 'var(--am)' : wo.risk === 'LOW' ? 'var(--gn)' : 'var(--t3)' }}>{wo.risk}</div>
            </div>
            <div className="dc"><div className="dcl">Duration Est.</div><div className="dcv">{wo.est}</div></div>
            <div className="dc"><div className="dcl">Parts</div><div className="dcv" style={{ fontSize: '11px' }}>{wo.parts}</div></div>
            <div className="dc"><div className="dcl">Assigned To</div><div className="dcv" style={{ fontSize: '11px' }}>{WO_ASSIGNEES[wo.st] || (wo.st === 'Closed' ? '✓ Completed' : wo.asgn || '—')}</div></div>
            <div className="dc"><div className="dcl">Cost</div><div className="dcv" style={{ fontSize: '11px' }}>{wo.cost}</div></div>
          </div>
        </div>

        <div className="dsec">
          <div className="sttl">Task Checklist</div>
          {checklist.map((item, i) => (
            <div key={i} className="clitem">
              <div className={`clcb${item.ck ? ' ck' : ''}`} onClick={() => toggleChecklist(i)} />
              <span className={`cltx${item.ck ? ' dk' : ''}`}>{item.tx}</span>
              <span className="cltg">{item.tg}</span>
            </div>
          ))}
        </div>

        <div className="dsec">
          <div className="sttl">Activity Timeline</div>
          {wo.tl.map((ev, i) => (
            <div key={i} className="tlrow">
              <div className="tldc">
                <div className="tldot" style={{ background: ev.dot }} />
                {i < wo.tl.length - 1 && <div className="tlline" />}
              </div>
              <div className="tlct">
                <div className="tlev">{ev.ev}{ev.by && <span style={{ color: 'var(--t3)', fontWeight: 400, marginLeft: '6px' }}>— {ev.by}</span>}</div>
                <div className="tltm">{ev.tm}</div>
              </div>
            </div>
          ))}
        </div>
      </div>
    </>
  );
}
