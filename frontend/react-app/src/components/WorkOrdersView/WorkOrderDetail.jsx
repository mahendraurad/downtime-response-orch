import React, { useContext, useState } from 'react';
import { AppContext } from '../../context/AppContext';

const PR_COLOR = { URGENT: 'var(--rd)', MEDIUM: 'var(--am)', LOW: 'var(--gn)' };
const PR_BG = { URGENT: 'var(--rdm)', MEDIUM: 'var(--adm)', LOW: 'var(--gd)' };

const WO_PERSONA_ACTS = {
  supervisor: {
    pending: [
      { l: '✓ Approve WO', cls: 'wpablu', q: 'Approve WO-2024-1847 — confirm Wednesday 06:00 crew deployment and release all logistics' },
      { l: '🤖 Why this WO?', cls: 'wpagry', q: 'Why did DRO create this WO? Explain the fault evidence and urgency' },
      { l: '✗ Reject', cls: 'wpared', q: 'Reject this WO — what further evidence or conditions are needed before resubmitting?' },
    ],
    scheduled: [
      { l: '▶ Mark In Progress', cls: 'wpablu', q: 'Mark this WO as in progress — confirm crew is on site and work has started' },
      { l: '📅 Reschedule', cls: 'wpagry', q: 'Reschedule this WO — what is the next available window without increasing M-104 failure risk?' },
    ],
    closed: [
      { l: '📋 Closure Report', cls: 'wpagry', q: 'Full closure report for this WO: fault found on teardown, repair performed, post-repair baseline, outcome' },
      { l: '↻ Follow-up WO', cls: 'wpagrn', q: 'Create a follow-up 6-month inspection WO from the closure' },
    ],
  },
  engineer: {
    pending: [
      { l: '🔬 Review Technical Evidence', cls: 'wpablu', q: 'What fault data confirms this WO is the right action? Show FFT evidence, kurtosis, crest factor, KB matches' },
      { l: '📐 Set QA Baseline', cls: 'wpagrn', q: 'What post-repair vibration and temperature baselines should be set? Target for new SKF 6310-2RS' },
    ],
    scheduled: [
      { l: '📊 Export Pre-Repair Data', cls: 'wpablu', q: 'Export pre-repair vibration and temperature data to compare against post-repair baseline' },
      { l: '📋 Verify SOP Specs', cls: 'wpagry', q: 'SOP for this WO: confirm torque spec, press fit procedure, lubrication type and volume' },
    ],
    closed: [
      { l: '📈 Compare Pre/Post Signature', cls: 'wpablu', q: 'Compare M-104 pre-repair (14.7 mm/s, BPFO 4.02×) vs post-repair baseline. Did it normalise as expected?' },
      { l: '🔁 Update Model', cls: 'wpagrn', q: 'How should this WO outcome data update the RUL prediction model? What was actual vs predicted fault stage?' },
    ],
  },
  maintenance: {
    pending: [
      { l: '📦 Confirm Parts Ready', cls: 'wpagrn', q: 'Confirm all parts and tools are staged: bearing, lube, seals, tools. Anything still to collect?' },
      { l: '👥 Assign Crew', cls: 'wpablu', q: 'Assign T.Rodriguez + K.Mensah — confirm both available and LOTO-qualified for Wednesday 06:00' },
    ],
    scheduled: [
      { l: '🖨 Print Job Pack', cls: 'wpablu', q: 'Complete job pack: SOP steps, parts with bin locations, tools, torque specs, LOTO reference' },
      { l: '⏱ Check Duration', cls: 'wpagry', q: 'Is the time estimate accurate for this WO? Should we allow more buffer?' },
    ],
    closed: [
      { l: '⏱ Log Actual Hours', cls: 'wpagry', q: 'Log actual hours and parts used vs estimate. Update duration benchmark for similar future jobs' },
      { l: '📦 Reorder Parts', cls: 'wpagrn', q: 'Do any parts consumed need reordering? Check SKF 6310-2RS stock at Bin A-14 vs reorder threshold' },
    ],
  },
  manager: {
    pending: [
      { l: '✓ Approve from Ops Level', cls: 'wpablu', q: 'Approve this WO at Plant Manager level — confirm Wednesday window is authorised' },
      { l: '💰 Cost-Benefit Model', cls: 'wpagrn', q: 'Full cost-benefit: planned $18K vs emergency $619K+ — detailed breakdown and net avoidance case' },
    ],
    scheduled: [
      { l: '📊 Line Impact', cls: 'wpablu', q: 'How does the Wednesday window affect Line 4 throughput and daily OEE targets?' },
      { l: '🔄 Contingency', cls: 'wpagry', q: 'Contingency if this WO overruns: backup crew, schedule recovery, production re-route' },
    ],
    closed: [
      { l: '💵 Final Cost vs Budget', cls: 'wpagry', q: 'Final cost vs $18K estimate — any overruns? Update maintenance budget tracker' },
      { l: '📈 OEE Impact Report', cls: 'wpablu', q: 'How did the planned window affect Line 4 monthly OEE vs an unplanned failure scenario?' },
    ],
  },
  executive: {
    pending: [
      { l: '💰 Financial Exposure', cls: 'wpared', q: 'Probability-weighted financial exposure if this WO is not approved today. Show expected value calculation' },
      { l: '✓ Executive Approval', cls: 'wpablu', q: 'Approve at executive level — confirm strategic priority and resource commitment' },
    ],
    scheduled: [
      { l: '📊 KPI Contribution', cls: 'wpablu', q: 'How does this WO contribute to Q3 reliability KPIs and DRO ROI metrics for executive scorecard?' },
      { l: '📰 Add to Exec Report', cls: 'wpagry', q: 'Add this WO as a DRO value example in the next executive reliability report' },
    ],
    closed: [
      { l: '💵 Avoidance Value', cls: 'wpagrn', q: 'Total downtime avoidance value from completing this WO as planned vs the emergency scenario' },
      { l: '📊 Update YTD Metrics', cls: 'wpablu', q: 'Add this WO outcome to YTD DRO performance metrics and avoidance tracker' },
    ],
  },
  ot: {
    pending: [
      { l: '📡 Sensor Readiness', cls: 'wpablu', q: 'All sensors on M-104 healthy and ready for Wednesday maintenance? Any calibration or data quality issues?' },
      { l: '🔗 CMMS Pre-Check', cls: 'wpagrn', q: 'Is SAP PM ready to receive the approval update? Any integration issues?' },
    ],
    scheduled: [
      { l: '📊 Data Continuity', cls: 'wpablu', q: 'Ensure data logging continuity on M-104 during the Wednesday maintenance window' },
      { l: '📏 Configure Baselines', cls: 'wpagrn', q: 'Historian baselines and alert thresholds to configure after completion. Specify target values.' },
    ],
    closed: [
      { l: '📈 Signals Normalised?', cls: 'wpagrn', q: 'Did M-104 readings normalise as expected after completion? Compare pre vs post across all 4 channels' },
      { l: '📚 Archive Event', cls: 'wpagry', q: 'Archive the complete data record: vibration trend, fault event, post-repair baseline' },
    ],
  },
  safety: {
    pending: [
      { l: '🛡 Permit Validation', cls: 'wpablu', q: 'Validate all safety permits: LOTO EL-104-A current, full scope, crew LOTO-certified?' },
      { l: '📋 Safety Brief', cls: 'wpagrn', q: 'Pre-task safety brief for crew: hazards, LOTO steps, PPE, isolation points, emergency contacts' },
    ],
    scheduled: [
      { l: '✅ Pre-Task Sign-off', cls: 'wpablu', q: 'Safety sign-off: LOTO in place, crew briefed, PPE available, cleared to start?' },
      { l: '🔒 Energy Isolation Check', cls: 'wpagry', q: 'Verify LOTO EL-104-A covers all energy sources: MCB-104A, MCB-104B, IL-104-M' },
    ],
    closed: [
      { l: '✅ Compliance Record', cls: 'wpagrn', q: 'Log safety compliance record: all permits completed, LOTO de-issued, no incidents' },
      { l: '⚠ Near-Miss Check', cls: 'wpagry', q: 'Any near-misses, LOTO deviations or safety observations during this WO to document?' },
    ],
  },
};

export default function WorkOrderDetail({ wo }) {
  const { persona } = useContext(AppContext);
  const [checklist, setChecklist] = useState(() => wo ? wo.cl.map(item => ({ ...item })) : []);

  // Sync checklist when wo changes
  React.useEffect(() => {
    if (wo) setChecklist(wo.cl.map(item => ({ ...item })));
  }, [wo && wo.id]);

  if (!wo) return <div className="wodhdr"><div style={{ padding: '16px', color: 'var(--t3)' }}>Select a work order</div></div>;

  const pc = PR_COLOR[wo.pr] || 'var(--t3)';
  const pb = PR_BG[wo.pr] || 'rgba(255,255,255,.04)';
  const sc = wo.st === 'Pending' ? 'var(--am)' : wo.st === 'Scheduled' ? 'var(--ac2)' : wo.st === 'Closed' ? 'var(--t3)' : 'var(--gn)';
  const sb = wo.st === 'Pending' ? 'var(--adm)' : wo.st === 'Scheduled' ? 'var(--ag)' : 'rgba(255,255,255,.04)';

  const stateKey = wo.st === 'Closed' ? 'closed' : wo.st === 'Pending' ? 'pending' : 'scheduled';
  const acts = ((WO_PERSONA_ACTS[persona] || WO_PERSONA_ACTS.supervisor)[stateKey] || WO_PERSONA_ACTS.supervisor.pending);

  function toggleChecklist(i) {
    const updated = checklist.map((item, idx) => idx === i ? { ...item, ck: !item.ck } : item);
    setChecklist(updated);
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
        <div className="wod-pacts">
          {acts.map((act, i) => (
            <button
              key={i}
              className={`wpa ${act.cls}`}
              onClick={() => window.dispatchEvent(new CustomEvent('dro-wo-ask', { detail: act.q }))}
            >
              {act.l}
            </button>
          ))}
        </div>
      </div>

      <div className="wodbody">
        {/* Summary */}
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
            <div className="dc"><div className="dcl">Assigned To</div><div className="dcv" style={{ fontSize: '11px' }}>{wo.asgn}</div></div>
            <div className="dc"><div className="dcl">Cost</div><div className="dcv" style={{ fontSize: '11px' }}>{wo.cost}</div></div>
          </div>
        </div>

        {/* Checklist */}
        <div className="dsec">
          <div className="sttl">Task Checklist</div>
          {checklist.map((item, i) => (
            <div key={i} className="clitem">
              <div
                className={`clcb${item.ck ? ' ck' : ''}`}
                onClick={() => toggleChecklist(i)}
              ></div>
              <span className={`cltx${item.ck ? ' dk' : ''}`}>{item.tx}</span>
              <span className="cltg">{item.tg}</span>
            </div>
          ))}
        </div>

        {/* Timeline */}
        <div className="dsec">
          <div className="sttl">Activity Timeline</div>
          {wo.tl.map((ev, i) => (
            <div key={i} className="tlrow">
              <div className="tldc">
                <div className="tldot" style={{ background: ev.dot }}></div>
                {i < wo.tl.length - 1 && <div className="tlline"></div>}
              </div>
              <div className="tlct">
                <div className="tlev">{ev.ev}</div>
                <div className="tltm">{ev.tm}</div>
              </div>
            </div>
          ))}
        </div>
      </div>
    </>
  );
}
