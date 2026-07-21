import React, { useContext, useState, useEffect, useRef } from 'react';
import { AppContext } from '../../context/AppContext';
import { ts } from '../../utils/helpers';

const GUIDANCE_TITLES = {
  supervisor: 'Supervisor Guidance',
  engineer: 'Technical Guidance',
  maintenance: 'Logistics Guidance',
  manager: 'Operations Guidance',
  executive: 'Executive Guidance',
  ot: 'Integration Guidance',
  safety: 'Safety Guidance',
};

const AI_CHIPS = {
  supervisor: {
    pending: ['Risk of deferring by 48h?', 'What to tell Line 4 operators about this WO?', 'If M-104 fails before this WO runs — what happens?'],
    scheduled: ['WO on track for Wednesday?', 'Who to notify about the Wednesday window?'],
    closed: ['Did the repair fully resolve the fault?', 'Lessons learned from closure?'],
  },
  engineer: {
    pending: ['Fault evidence confirming this WO is correct?', 'Post-repair QA pass/fail criteria?'],
    scheduled: ['Which SOP version applies?', 'What should teardown confirm?'],
    closed: ['Pre vs post repair signature comparison?', 'Update fault model with outcome?'],
  },
  maintenance: {
    pending: ['All parts and tools ready?', 'Optimal crew?'],
    scheduled: ['Full job pack?', 'Crew briefing completed?'],
    closed: ['Actual vs estimated time?', 'Parts to reorder?'],
  },
  manager: {
    pending: ['Net avoidance value of approving?', 'Production impact if delayed?'],
    scheduled: ['Line impact of window?', 'Contingency if overruns?'],
    closed: ['Final cost vs budget?', 'OEE impact?'],
  },
  executive: {
    pending: ['Expected-value risk of not approving?', 'Contribution to Q3 DRO ROI?'],
    scheduled: ['In executive KPI metrics?'],
    closed: ['Avoidance value?', 'Add to YTD tracker?'],
  },
  ot: {
    pending: ['M-104 signal readiness before WO?', 'CMMS ready for approval?'],
    scheduled: ['Data logging continuity during WO?', 'Post-repair baselines?'],
    closed: ['Signals normalised after WO?', 'Archive WO event data?'],
  },
  safety: {
    pending: ['All permits valid?', 'Crew LOTO-qualified?'],
    scheduled: ['Pre-task sign-off?', 'All energy isolation confirmed?'],
    closed: ['Safety compliance record?', 'Near-misses during WO?'],
  },
};

const WO_PLACEHOLDER = {
  supervisor: 'Ask about approval, risk, crew, escalation…',
  engineer: 'Ask about fault evidence, SOP, QA baseline…',
  maintenance: 'Ask about parts, crew, schedule, job pack…',
  manager: 'Ask about cost, OEE, production plan…',
  executive: 'Ask about financial exposure, ROI, KPI…',
  ot: 'Ask about signals, CMMS, data continuity…',
  safety: 'Ask about permits, LOTO, PPE, compliance…',
};

function getWOResp(txt, wo) {
  if (!wo) return 'No work order selected.';
  if (/approv/i.test(txt)) return `<strong style="color:var(--gn)">✓ ${wo.id} approved and released.</strong><br>SAP PM: RELEASED. T.Rodriguez + K.Mensah notified. SKF 6310-2RS reserved (Bin A-14). Wednesday 06:00 slot confirmed. Alert will fire when post-repair vibration baseline check passes.`;
  if (/why|reason|evidence|trigger/i.test(txt)) return `${wo.id} created because M-104 exceeded <strong style="color:var(--t)">ISO 13373-1 Stage 3</strong> — BPFO 4.02×, Kurtosis 6.8, Crest Factor 9.2, RUL 5–8 days (82% conf). Three KB cases failed in 4.1–9.3 days from this level. Prescriptive Agent identified Wednesday 06:00 as the optimal window: $601K net avoidance at 33× ROI.`;
  if (/defer|delay|risk|weekend|saturday/i.test(txt)) return `Deferring ${wo.id} by 48h raises failure probability to <strong style="color:var(--rd)">67%</strong>. Consequence: bearing seizure, 12–18h stop, possible winding damage ($80K). Emergency total: $619K+. Net additional exposure vs Wednesday: $601K.`;
  if (/parts|sku|stock|SKF|bearing|stores/i.test(txt)) return `Parts confirmed: <strong style="color:var(--t)">SKF 6310-2RS</strong> Bin A-14, Qty 3, $340. Mobil SHC 100: 2L. Seal kit: available. All tools in shop. 1 unit reserved for ${wo.id}.`;
  if (/crew|assign|who|rodriguez|mensah/i.test(txt)) return `Crew for ${wo.id}: <strong style="color:var(--t)">T.Rodriguez</strong> (Lead, LOTO EL-104-A ✓, M-104 prior experience ✓) + <strong style="color:var(--t)">K.Mensah</strong> (LOTO ✓). Both available Wednesday 06:00.`;
  if (/cost|financ|saving|avoidance/i.test(txt)) return `${wo.id} cost analysis:<br>• Planned: <strong style="color:var(--gn)">$18K</strong> (4h labour + $340 parts)<br>• Emergency alternative: <strong style="color:var(--rd)">$619K+</strong><br>• <strong style="color:var(--gn)">Net avoidance: $601K · 33× ROI</strong>`;
  if (/LOTO|permit|safety|PPE|isolat/i.test(txt)) return `Safety for ${wo.id}: <strong style="color:var(--gn)">LOTO EL-104-A current</strong> (Nov 2023, valid Nov 2024). MCB-104A, MCB-104B, IL-104-M. No confined space, no hot work. PPE: Class B hearing + vibration gloves. Both crew LOTO-qualified.`;
  if (/closure|teardown|outcome|post.repair/i.test(txt)) return `${wo.id} closure: outer race spall Stage 3 confirmed (~35% surface damage). SKF 6310-2RS installed. Post-repair baseline: <strong style="color:var(--gn)">1.4 mm/s, 46°C</strong>. No secondary damage. Total elapsed: 3h 52min.`;
  if (/oee|production|throughput|line/i.test(txt)) return `${wo.id} production impact: 4h planned stop Wednesday 06:00–10:00 (~$164K). Alternative unplanned: 12–18h × $41K/h = $492–738K. Planned window is already in the production schedule.`;
  return `Processing ${wo.id}: full context available — fault evidence, logistics, cost case, crew, safety. Ask anything specific.`;
}

export default function WorkOrderAI({ wo }) {
  const { persona } = useContext(AppContext);
  const [respHtml, setRespHtml] = useState(null);
  const [respTitle, setRespTitle] = useState('🤖 DRO Agent');
  const [thinking, setThinking] = useState(false);
  const [inputVal, setInputVal] = useState('');
  const respRef = useRef(null);

  const stateKey = wo ? (wo.st === 'Closed' ? 'closed' : wo.st === 'Pending' ? 'pending' : 'scheduled') : 'pending';
  const chips = (AI_CHIPS[persona] || AI_CHIPS.supervisor)[stateKey] || [];
  const placeholder = WO_PLACEHOLDER[persona] || 'Ask about this work order…';
  const guidanceTitle = `${GUIDANCE_TITLES[persona] || 'Agent Guidance'}${wo ? ' — ' + wo.id : ''}`;

  // Listen for dro-wo-ask events from WorkOrderDetail persona action buttons
  useEffect(() => {
    function handleWoAsk(e) { woAsk(e.detail); }
    window.addEventListener('dro-wo-ask', handleWoAsk);
    return () => window.removeEventListener('dro-wo-ask', handleWoAsk);
  }, [wo, persona]);

  // Reset response when WO changes
  useEffect(() => {
    setRespHtml(null);
  }, [wo && wo.id]);

  function woAsk(txt) {
    const t = (txt || inputVal).trim();
    if (!t) return;
    setInputVal('');
    setThinking(true);
    setRespHtml(null);

    const resp = getWOResp(t, wo);
    setTimeout(() => {
      setThinking(false);
      setRespTitle(`🤖 DRO Agent · ${ts()}`);
      setRespHtml(resp);
      if (respRef.current) {
        respRef.current.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
      }
    }, 900);
  }

  return (
    <div className="wodai">
      <div className="waitl">{guidanceTitle}</div>
      <div className="waichips">
        {chips.map((c, i) => (
          <button
            key={i}
            className={`waic${i === 0 ? ' b' : ' g'}`}
            onClick={() => woAsk(c)}
          >
            {c}
          </button>
        ))}
      </div>
      <div className="wocc">
        {wo && (
          <>
            <span className="ctag ctb">{wo.id}</span>
            <span className="ctag ctb">{wo.as.split('·')[0].trim()}</span>
            <span className={`ctag ${wo.st === 'Pending' ? 'ctr' : 'cta'}`}>{wo.st}</span>
          </>
        )}
      </div>
      <div className="woinrow">
        <input
          type="text"
          className="ainp"
          placeholder={placeholder}
          value={inputVal}
          onChange={e => setInputVal(e.target.value)}
          onKeyDown={e => { if (e.key === 'Enter') woAsk(); }}
        />
        <button className="sbtn" onClick={() => woAsk()}>
          <svg viewBox="0 0 24 24"><path d="M2.01 21L23 12 2.01 3 2 10l15 2-15 2z" /></svg>
        </button>
      </div>
      {thinking && (
        <div className="woresp" style={{ display: 'flex', alignItems: 'center', gap: '8px', padding: '12px 14px' }}>
          <div className="tdts"><span></span><span></span><span></span></div>
        </div>
      )}
      {!thinking && respHtml && (
        <div className="woresp" ref={respRef}>
          <div className="worttl">{respTitle}</div>
          <div dangerouslySetInnerHTML={{ __html: respHtml.replace(/\n/g, '<br>') }} />
        </div>
      )}
    </div>
  );
}
