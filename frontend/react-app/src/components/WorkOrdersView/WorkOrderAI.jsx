import React, { useContext, useEffect, useRef, useState } from 'react';
import { AppContext } from '../../context/AppContext';
import { askChat } from '../../api/chat';
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
    pending: ['Risk of deferring by 48h?', 'What to tell Line 4 operators?', 'What if M-104 fails before this WO runs?'],
    scheduled: ['Is the WO on track?', 'Who to notify about the window?'],
    closed: ['Did the repair fully resolve the fault?', 'Lessons learned?'],
  },
  engineer: {
    pending: ['Fault evidence confirming this WO?', 'Post-repair QA pass/fail criteria?'],
    scheduled: ['Which SOP version applies?', 'What should teardown confirm?'],
    closed: ['Pre vs post repair signature comparison?', 'Update fault model with outcome?'],
  },
  maintenance: {
    pending: ['All parts and tools ready?', 'Optimal crew for this job?'],
    scheduled: ['Is the job pack complete?', 'Crew briefing done?'],
    closed: ['Actual vs estimated time?', 'Parts to reorder?'],
  },
  manager: {
    pending: ['Net avoidance value of approving?', 'Production impact if delayed?'],
    scheduled: ['Line impact of the window?', 'Contingency if it overruns?'],
    closed: ['Final cost vs budget?', 'OEE impact report?'],
  },
  executive: {
    pending: ['Expected-value risk of not approving?', 'Q3 DRO ROI contribution?'],
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
    scheduled: ['Pre-task sign-off complete?', 'All energy isolation confirmed?'],
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

function mkId() { return `wo-${Date.now()}-${Math.floor(Math.random() * 1000)}`; }

export default function WorkOrderAI({ wo }) {
  const { persona } = useContext(AppContext);
  const [inputVal, setInputVal] = useState('');
  const [thinking, setThinking] = useState(false);
  const [collapsed, setCollapsed] = useState(true);
  // Local chat history: [{id, role:'user'|'agent', text, html, time}]
  const [msgs, setMsgs] = useState([]);
  const conversationIdRef = useRef(null);
  const msgsRef = useRef(null);
  const inputRef = useRef(null);

  const stateKey = wo ? (wo.st === 'Closed' ? 'closed' : wo.st === 'Pending' ? 'pending' : 'scheduled') : 'pending';
  const chips = (AI_CHIPS[persona] || AI_CHIPS.supervisor)[stateKey] || [];
  const placeholder = WO_PLACEHOLDER[persona] || 'Ask about this work order…';
  const guidanceTitle = `${GUIDANCE_TITLES[persona] || 'Agent Guidance'}${wo ? ' — ' + wo.id : ''}`;

  // Scroll to bottom when messages or thinking state changes
  useEffect(() => {
    if (msgsRef.current) msgsRef.current.scrollTop = msgsRef.current.scrollHeight;
  }, [msgs, thinking]);

  // Listen for dro-wo-ask events from WorkOrderDetail persona action buttons
  useEffect(() => {
    function handleWoAsk(e) {
      setCollapsed(false);
      woAsk(e.detail);
    }
    window.addEventListener('dro-wo-ask', handleWoAsk);
    return () => window.removeEventListener('dro-wo-ask', handleWoAsk);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [wo, persona, msgs]);

  // Reset local chat when the selected WO changes
  useEffect(() => {
    setMsgs([]);
    setInputVal('');
    conversationIdRef.current = null;
  }, [wo?.id]);

  async function woAsk(txt) {
    const t = (txt || inputVal).trim();
    if (!t || thinking) return;
    setInputVal('');

    // Embed WO facts so the LLM has full context without needing telemetry
    const woPrefix = wo
      ? `[Work Order: ${wo.id} | Title: ${wo.ti} | Asset: ${wo.as} | Status: ${wo.st} | Priority: ${wo.pr} | RUL: ${wo.rul} | Risk: ${wo.risk} | Parts: ${wo.parts} | Assigned: ${wo.asgn} | Cost: ${wo.cost} | Type: ${wo.tp}]\n\n`
      : '';
    const fullMsg = `${woPrefix}${t}`;

    // Append user bubble
    setMsgs(prev => [...prev, { id: mkId(), role: 'user', text: t, time: ts() }]);
    setThinking(true);

    // Build conversation history from local msgs (same pattern as ChatView)
    const conversationHistory = msgs
      .filter(m => m.role === 'user' || m.role === 'agent')
      .slice(-8)
      .map(m => ({
        role: m.role === 'user' ? 'user' : 'assistant',
        content: m.role === 'user'
          ? (m.text || '')
          : (m.html || '').replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim().slice(0, 500),
      }))
      .filter(m => m.content.length > 0);

    try {
      const data = await askChat({
        message: fullMsg,
        persona,
        conversationId: conversationIdRef.current,
        conversationHistory,
      });
      conversationIdRef.current = data.conversation_id || conversationIdRef.current;

      const details = data.details?.length ? '<br><br>' + data.details.map(x => '• ' + x).join('<br>') : '';
      const actions = data.actions?.length ? '<br><br><strong>Actions:</strong><br>' + data.actions.map(x => '→ ' + x).join('<br>') : '';
      const html = (data.response || '') + details + actions;

      setMsgs(prev => [...prev, { id: mkId(), role: 'agent', html, time: ts() }]);
    } catch (err) {
      const errHtml = `<span style="color:var(--rd)">${err.message || 'Request failed. Please try again.'}</span>`;
      setMsgs(prev => [...prev, { id: mkId(), role: 'agent', html: errHtml, time: ts() }]);
    } finally {
      setThinking(false);
    }
  }

  return (
    <div className="wodai" style={collapsed ? { padding: '8px 16px' } : {}}>

      {/* Title bar — always visible, click to toggle */}
      <div
        className="wodai-hdr"
        onClick={() => setCollapsed(c => !c)}
        title={collapsed ? 'Expand AI guidance' : 'Collapse AI guidance'}
      >
        <span className="waitl" style={{ margin: 0 }}>{guidanceTitle}</span>
        <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
          {msgs.length > 0 && collapsed && (
            <span style={{ fontSize: '9px', fontWeight: 700, color: 'var(--ac2)', fontFamily: 'var(--m)', background: 'rgba(79,142,255,.1)', border: '1px solid rgba(79,142,255,.2)', borderRadius: '4px', padding: '1px 5px' }}>
              {msgs.length} msg{msgs.length !== 1 ? 's' : ''}
            </span>
          )}
          {thinking && collapsed && (
            <div className="tdts" style={{ transform: 'scale(0.8)' }}><span></span><span></span><span></span></div>
          )}
          <span style={{ fontSize: '13px', color: 'var(--t3)', lineHeight: 1 }}>
            {collapsed ? '▲' : '▼'}
          </span>
        </div>
      </div>

      {/* Collapsible body */}
      {!collapsed && (
        <>
          {/* Chip shortcuts — only before first message */}
          {msgs.length === 0 && (
            <div className="waichips" style={{ marginTop: '8px' }}>
              {chips.map((c, i) => (
                <button
                  key={i}
                  className={`waic${i === 0 ? ' b' : ' g'}`}
                  onClick={e => { e.stopPropagation(); woAsk(c); }}
                  disabled={thinking}
                >
                  {c}
                </button>
              ))}
            </div>
          )}

          {/* WO context tags */}
          {wo && msgs.length === 0 && (
            <div className="wocc">
              <span className="ctag ctb">{wo.id}</span>
              <span className="ctag ctb">{wo.as.split('·')[0].trim()}</span>
              <span className={`ctag ${wo.st === 'Pending' ? 'ctr' : 'cta'}`}>{wo.st}</span>
            </div>
          )}

          {/* Conversation bubbles */}
          {msgs.length > 0 && (
            <div
              ref={msgsRef}
              style={{
                maxHeight: '220px',
                overflowY: 'auto',
                padding: '8px 0',
                display: 'flex',
                flexDirection: 'column',
                gap: '8px',
              }}
            >
              {msgs.map(m => (
                <div key={m.id} style={{ display: 'flex', flexDirection: 'column', alignItems: m.role === 'user' ? 'flex-end' : 'flex-start' }}>
                  <div style={{
                    maxWidth: '90%',
                    padding: '7px 10px',
                    borderRadius: m.role === 'user' ? '10px 10px 2px 10px' : '10px 10px 10px 2px',
                    background: m.role === 'user' ? 'var(--ac)' : 'var(--bg)',
                    border: m.role === 'agent' ? '1px solid var(--b)' : 'none',
                    fontSize: '12px',
                    color: m.role === 'user' ? '#fff' : 'var(--t)',
                    lineHeight: '1.6',
                  }}>
                    {m.role === 'user'
                      ? m.text
                      : <span dangerouslySetInnerHTML={{ __html: (m.html || '').replace(/\n/g, '<br>') }} />
                    }
                  </div>
                  <div style={{ fontSize: '9.5px', color: 'var(--t3)', marginTop: '2px', padding: '0 2px' }}>{m.time}</div>
                </div>
              ))}
              {thinking && (
                <div style={{ display: 'flex', alignItems: 'center', gap: '8px', padding: '2px 0' }}>
                  <div className="tdts"><span></span><span></span><span></span></div>
                  <span style={{ fontSize: '11px', color: 'var(--t3)', fontStyle: 'italic' }}>Agent thinking…</span>
                </div>
              )}
            </div>
          )}

          {/* Input row */}
          <div className="woinrow" style={{ marginTop: '8px' }}>
            <input
              ref={inputRef}
              type="text"
              className="ainp"
              placeholder={thinking ? 'Agent thinking…' : placeholder}
              value={inputVal}
              onChange={e => setInputVal(e.target.value)}
              onKeyDown={e => { if (e.key === 'Enter') woAsk(); }}
              onClick={e => e.stopPropagation()}
              disabled={thinking}
              style={thinking ? { opacity: 0.5, cursor: 'not-allowed' } : {}}
            />
            <button
              className="sbtn"
              onClick={e => { e.stopPropagation(); woAsk(); }}
              disabled={thinking}
              style={thinking ? { opacity: 0.35, cursor: 'not-allowed' } : {}}
            >
              <svg viewBox="0 0 24 24"><path d="M2.01 21L23 12 2.01 3 2 10l15 2-15 2z" /></svg>
            </button>
          </div>

          {/* Follow-up chips after conversation */}
          {msgs.length > 0 && !thinking && (
            <div className="waichips" style={{ marginTop: '6px' }}>
              {chips.slice(0, 2).map((c, i) => (
                <button key={i} className="waic g" onClick={e => { e.stopPropagation(); woAsk(c); }}>{c}</button>
              ))}
            </div>
          )}
        </>
      )}
    </div>
  );
}
