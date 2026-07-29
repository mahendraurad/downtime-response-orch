import React, { useContext, useEffect, useRef, useState, useCallback } from 'react';
import { AppContext } from '../../context/AppContext';
import { PD } from '../../data/personas';
import { ts } from '../../utils/helpers';
import { authedFetch } from '../../api/http';
import { API } from '../../config/api';
import { resolveHITLRemediation, resolveHITLMonitoring, resolveHITLDiagnosis, resolveHITLKnowledge, resolveHITLAdvisory, runExecutor } from '../../api/pipeline';
import ChatSidebar from './ChatSidebar';

function mkId() { return `m-${Date.now()}-${Math.floor(Math.random() * 1000)}`; }

export default function ChatView() {
  const { persona, messages, setMessages, addMessage } = useContext(AppContext);
  const [inputVal, setInputVal] = useState('');
  const [thinking, setThinking] = useState(false);
  const msgsRef = useRef(null);
  const inpRef = useRef(null);
  const conversationIdRef = useRef(null);
  const p = PD[persona];

  // Scroll to bottom whenever messages change
  useEffect(() => {
    if (msgsRef.current) msgsRef.current.scrollTop = msgsRef.current.scrollHeight;
  }, [messages, thinking]);

  // On persona switch: reset conversation, restore stored history or show home
  useEffect(() => {
    conversationIdRef.current = null;
    if (!messages || messages.length === 0) {
      renderChatHome();
    }
    if (inpRef.current) {
      inpRef.current.placeholder = `Ask as ${p.nm.split(' — ')[0]}: faults, risk, decisions, what-if…`;
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [persona]);

  // Listen for global events from Topbar / PersonaPanel
  useEffect(() => {
    function onSq(e) { sendMsg(e.detail); }
    function onRunPipeline(e) {
      const { scenario, persona: pArg } = e.detail;
      sendMsg(`Run the orchestrated analysis for scenario ${scenario}`, {
        scenario,
        requestedPersona: pArg,
      });
    }
    function onExecutorHITL(e) { renderExecutorHITLCard(e.detail); }

    window.addEventListener('dro-sq', onSq);
    window.addEventListener('dro-run-pipeline', onRunPipeline);
    window.addEventListener('dro-executor-hitl', onExecutorHITL);
    window.addEventListener('dro-persona-change', () => {});
    return () => {
      window.removeEventListener('dro-sq', onSq);
      window.removeEventListener('dro-run-pipeline', onRunPipeline);
      window.removeEventListener('dro-executor-hitl', onExecutorHITL);
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [persona]);

  function renderChatHome() {
    const pd = PD[persona];
    const homeMsg = {
      id: mkId(),
      type: 'home',
      persona,
      gr: pd.gr,
      actions: pd.actions,
      time: ts(),
    };
    setMessages([homeMsg]);
  }

  function appendA(html, routes, chips) {
    addMessage({ id: mkId(), type: 'agent', html, routes: routes || [], chips: chips || [], time: ts() });
  }

  function appendU(txt) {
    addMessage({ id: mkId(), type: 'user', text: txt, time: ts() });
  }

  function doThink(cb) {
    setThinking(true);
    setTimeout(async () => {
      try {
        await cb();
      } finally {
        setThinking(false);
      }
    }, 1100);
  }

  function handlePipelineResult(data, scenario, pArg) {
    if (data.hitl_required && data.hitl_required.type === 'remediation') {
      addMessage({ id: mkId(), type: 'hitl_remediation', data, scenario, persona: pArg, time: ts() });
      return;
    }
    if (data.hitl_monitoring) {
      addMessage({ id: mkId(), type: 'hitl_monitoring', data, scenario, persona: pArg, time: ts() });
      return;
    }
    if (data.hitl_diagnosis) {
      addMessage({ id: mkId(), type: 'hitl_diagnosis', data, scenario, persona: pArg, time: ts() });
      return;
    }
    const ms = data.pipeline_log ? data.pipeline_log.reduce((s, n) => s + (n.latency_ms || 0), 0) : 0;
    const nodes = data.pipeline_log ? data.pipeline_log.length : 0;
    const html = `<div style="margin-bottom:4px"><span style="font-size:9px;font-weight:600;color:var(--ac2);font-family:var(--m)">▶ REAL PIPELINE · ${nodes} agents · ${ms}ms</span></div>`
      + `<strong style="color:var(--t)">${data.headline}</strong><br><br>`
      + data.details.map(d => '• ' + d).join('<br>')
      + (data.actions && data.actions.length ? '<br><br><strong>Recommended actions:</strong><br>' + data.actions.map(a => '→ ' + a).join('<br>') : '');
    const routes = data.pipeline_log ? data.pipeline_log.map(n => n.node ? n.node.replace(/_/g, ' ') : '') : [];
    appendA(html, routes);
    if (data.hitl_advisory && data.hitl_advisory.advisory_note) {
      addMessage({ id: mkId(), type: 'hitl_advisory', data, time: ts() });
    }
    if (data.hitl_knowledge) {
      addMessage({ id: mkId(), type: 'hitl_knowledge', data, persona: pArg, time: ts() });
    }
    if (data.recommendation && data.recommendation.recommendation_status === 'ok') {
      renderExecutorHITLCard(data.recommendation);
    }
  }

  async function sendMsg(txt, options = {}) {
    const t = (txt || inputVal).trim();
    if (!t) return;
    setInputVal('');
    if (inpRef.current) inpRef.current.style.height = 'auto';
    appendU(t);

    // All queries go to /api/chat. The backend LLM classifier decides whether
    // this is a new pipeline request, a conversational follow-up, or general knowledge.
    // Conversation history is included so the classifier understands follow-up context.
    // (Future: swap this history source for a Cosmos DB episode fetch.)
    const conversationHistory = messages
      .filter(m => m.type === 'user' || m.type === 'agent')
      .slice(-8)
      .map(m => ({
        role: m.type === 'user' ? 'user' : 'assistant',
        content: m.type === 'user'
          ? (m.text || '')
          : (m.html || '').replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim().slice(0, 500),
      }))
      .filter(m => m.content.length > 0);

    doThink(async () => {
      try {
        const resp = await authedFetch(`${API}/api/chat`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            message: t,
            persona,
            conversation_id: conversationIdRef.current,
            conversation_history: conversationHistory,
          }),
        });
        if (!resp.ok) throw new Error(`Chat request failed (HTTP ${resp.status}).`);
        const d = await resp.json();
        conversationIdRef.current = d.conversation_id || conversationIdRef.current;
        const routes = d.pipeline_log ? d.pipeline_log.map(n => n.node ? n.node.replace(/_/g, ' ') : '') : [];

        // HITL blocking gates — show card and stop rendering the response
        if (d.hitl_required?.type === 'remediation') {
          addMessage({ id: mkId(), type: 'hitl_remediation', data: d, scenario: null, persona, time: ts() });
          return;
        }
        if (d.hitl_monitoring) {
          addMessage({ id: mkId(), type: 'hitl_monitoring', data: d, scenario: null, persona, time: ts() });
          return;
        }
        if (d.hitl_diagnosis) {
          addMessage({ id: mkId(), type: 'hitl_diagnosis', data: d, scenario: null, persona, time: ts() });
          return;
        }

        // Normal response
        const details = d.details && d.details.length ? '<br><br>' + d.details.map(x => '• ' + x).join('<br>') : '';
        const actions = d.actions && d.actions.length ? '<br><br><strong>Actions:</strong><br>' + d.actions.map(x => '→ ' + x).join('<br>') : '';
        const questions = d.clarification && d.clarification.questions ? '<br><br><strong>Needed:</strong><br>' + d.clarification.questions.map(x => '? ' + x).join('<br>') : '';
        appendA(d.response + details + actions + questions, routes);

        // Non-blocking gates shown alongside the response
        if (d.hitl_advisory?.advisory_note) {
          addMessage({ id: mkId(), type: 'hitl_advisory', data: d, time: ts() });
        }
        if (d.hitl_knowledge) {
          addMessage({ id: mkId(), type: 'hitl_knowledge', data: d, persona, time: ts() });
        }
        if (d.recommendation?.recommendation_status === 'ok') {
          addMessage({ id: mkId(), type: 'hitl_executor', rec: d.recommendation, time: ts() });
        }
      } catch (error) {
        appendA(`The orchestrated chat service is currently unavailable. ${error.message}`, ['Chat API']);
      }
    });
  }

  function renderExecutorHITLCard(rec) {
    addMessage({ id: mkId(), type: 'hitl_executor', rec, time: ts() });
  }

  function handleKeyDown(e) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      sendMsg();
    }
  }

  function autoResize(e) {
    e.target.style.height = 'auto';
    e.target.style.height = Math.min(e.target.scrollHeight, 90) + 'px';
  }

  return (
    <div style={{ display: 'flex', flex: 1, overflow: 'hidden' }}>
      <div className="chat-main">
        {/* Header */}
        <div className="chdr">
          <div className="chav" style={{ background: p.bg }}>{p.av}</div>
          <div>
            <div className="chnm">{p.nm}</div>
            <div className="chds">{p.ds}</div>
          </div>
        </div>

        {/* Messages */}
        <div className="msgs" ref={msgsRef}>
          {messages.map(msg => (
            <MessageBubble
              key={msg.id}
              msg={msg}
              persona={persona}
              onSq={sendMsg}
              doThink={doThink}
              appendA={appendA}
              addMessage={addMessage}
            />
          ))}
          {thinking && (
            <div className="tdg fi">
              <div className="mav" style={{ background: 'var(--ag)' }}>🤖</div>
              <div>
                <div className="tdts"><span></span><span></span><span></span></div>
                <div style={{ fontSize: '11px', color: 'var(--t2)', marginTop: '4px', fontStyle: 'italic' }}>Agents are processing…</div>
              </div>
            </div>
          )}
        </div>

        {/* Input */}
        <div className="cinp">
          <div className="ctags">
            {p.tags.map((tag, i) => (
              <span key={i} className={`ctag ${i === 0 ? 'ctb' : i === 1 ? 'ctr' : 'cta'}`}>{tag}</span>
            ))}
          </div>
          <div className="cirow">
            <textarea
              ref={inpRef}
              className="cinptx"
              rows={1}
              placeholder={thinking ? 'Agents are processing…' : `Ask as ${p.nm.split(' — ')[0]}: faults, risk, decisions, what-if…`}
              value={inputVal}
              onChange={e => setInputVal(e.target.value)}
              onKeyDown={handleKeyDown}
              onInput={autoResize}
              disabled={thinking}
              style={thinking ? { opacity: 0.5, cursor: 'not-allowed' } : {}}
            />
            <button
              className="sbtn"
              onClick={() => sendMsg()}
              disabled={thinking}
              style={thinking ? { opacity: 0.35, cursor: 'not-allowed' } : {}}
            >
              <svg viewBox="0 0 24 24"><path d="M2.01 21L23 12 2.01 3 2 10l15 2-15 2z" /></svg>
            </button>
          </div>
          <div className="qps">
            {p.qp.map((q, i) => (
              <button key={i} className="qpb" onClick={() => sendMsg(q)} disabled={thinking} style={thinking ? { opacity: 0.4, cursor: 'not-allowed' } : {}}>{q}</button>
            ))}
          </div>
        </div>
      </div>
      <ChatSidebar onSq={sendMsg} />
    </div>
  );
}

function MessageBubble({ msg, persona, onSq, doThink, appendA, addMessage }) {
  const p = PD[persona];

  if (msg.type === 'home') {
    const pd = PD[msg.persona] || PD[persona];
    return (
      <div className="mg fi">
        <div className="mav" style={{ background: 'var(--ag)' }}>🤖</div>
        <div className="mb">
          <div className="mmeta"><span className="msndr">DRO Agent</span><span className="mtm">{msg.time}</span></div>
          <div className="bbl a" dangerouslySetInnerHTML={{ __html: pd.gr.replace(/\n/g, '<br>') }} />
          <div className="act-cards">
            {pd.actions.map((a, i) => (
              <button
                key={i}
                className="act-card"
                style={{ background: a.col, borderColor: a.bc, color: a.tc }}
                onClick={() => onSq(a.q)}
              >
                <div className="ac-icon">{a.icon}</div>
                <div className="ac-lbl">{a.lbl}</div>
                <div className="ac-nm">{a.nm}</div>
              </button>
            ))}
          </div>
        </div>
      </div>
    );
  }

  if (msg.type === 'user') {
    return (
      <div className="mg u fi">
        <div className="mav" style={{ background: p.bg }}>{p.av}</div>
        <div className="mb">
          <div className="mmeta" style={{ justifyContent: 'flex-end' }}>
            <span className="mtm">{msg.time}</span>
            <span className="msndr">{p.nm.split(' — ')[0]}</span>
          </div>
          <div className="bbl u">{msg.text}</div>
        </div>
      </div>
    );
  }

  if (msg.type === 'agent') {
    return (
      <div className="mg fi">
        <div className="mav" style={{ background: 'var(--ag)' }}>🤖</div>
        <div className="mb">
          <div className="mmeta"><span className="msndr">DRO Agent</span><span className="mtm">{msg.time}</span></div>
          {msg.routes && msg.routes.length > 0 && (
            <div className="rrow">
              <span className="rtx">via →</span>
              {msg.routes.map((r, i) => (
                <React.Fragment key={i}>
                  <span className="rs">{r}</span>
                  {i < msg.routes.length - 1 && <span className="rra">›</span>}
                </React.Fragment>
              ))}
            </div>
          )}
          <div className="bbl a" dangerouslySetInnerHTML={{ __html: msg.html.replace(/\n/g, '<br>') }} />
          {msg.chips && msg.chips.length > 0 && (
            <div className="qbrow">
              {msg.chips.map((c, i) => (
                <button key={i} className="qb b" onClick={() => onSq(c)}>{c}</button>
              ))}
            </div>
          )}
        </div>
      </div>
    );
  }

  if (msg.type === 'hitl_remediation') {
    return <HITLRemediationMsg msg={msg} onSq={onSq} doThink={doThink} appendA={appendA} addMessage={addMessage} persona={persona} />;
  }
  if (msg.type === 'hitl_advisory') {
    return <HITLAdvisoryMsg msg={msg} doThink={doThink} appendA={appendA} persona={persona} />;
  }
  if (msg.type === 'hitl_monitoring') {
    return <HITLMonitoringMsg msg={msg} onSq={onSq} doThink={doThink} appendA={appendA} addMessage={addMessage} persona={persona} />;
  }
  if (msg.type === 'hitl_diagnosis') {
    return <HITLDiagnosisMsg msg={msg} onSq={onSq} doThink={doThink} appendA={appendA} addMessage={addMessage} persona={persona} />;
  }
  if (msg.type === 'hitl_knowledge') {
    return <HITLKnowledgeMsg msg={msg} doThink={doThink} appendA={appendA} persona={persona} />;
  }
  if (msg.type === 'hitl_executor') {
    return <HITLExecutorMsg msg={msg} doThink={doThink} appendA={appendA} persona={persona} />;
  }

  return null;
}

function HITLRemediationMsg({ msg, doThink, appendA, addMessage, persona }) {
  const [resolved, setResolved] = useState(false);
  const h = msg.data.hitl_required;
  const rid = h.run_id;
  const qPct = h.quality_score != null ? Math.round(h.quality_score * 100) + '%' : 'Low';
  const canImpute = h.imputable_fields && h.imputable_fields.length > 0;
  const impFields = canImpute
    ? `Imputable from bearing baseline: <strong style="color:#22c55e">${h.imputable_fields.join(', ')}</strong>`
    : '<span style="color:#ef4444">No baseline available - imputation not possible</span>';
  const missingStr = h.missing_fields ? h.missing_fields.join(', ') : 'unknown';

  async function resolve(action) {
    setResolved(true);
    doThink(async () => {
      try {
        const data = await resolveHITLRemediation(rid, action, persona);
        const label = action === 'IMPUTE' ? '✓ Imputed from baseline' : action === 'DROP' ? '✗ Signal dropped' : '~ Kept with quality flag';
        const ms = data.pipeline_log ? data.pipeline_log.reduce((s, n) => s + (n.latency_ms || 0), 0) : 0;
        const html = `<div style="margin-bottom:4px"><span style="font-size:9px;font-weight:700;color:#22c55e;font-family:var(--m)">✓ HITL RESOLVED · ${label} · Pipeline resumed · ${ms}ms</span></div>`
          + `<strong style="color:var(--t)">${data.headline}</strong><br><br>`
          + data.details.map(d => '• ' + d).join('<br>')
          + (data.actions && data.actions.length ? '<br><br><strong>Recommended actions:</strong><br>' + data.actions.map(a => '→ ' + a).join('<br>') : '');
        appendA(html, data.pipeline_log ? data.pipeline_log.map(n => n.node ? n.node.replace(/_/g, ' ') : '') : []);
        if (data.hitl_advisory && data.hitl_advisory.advisory_note) {
          addMessage({ id: mkId(), type: 'hitl_advisory', data, time: ts() });
        }
        if (data.recommendation && data.recommendation.recommendation_status === 'ok') {
          addMessage({ id: mkId(), type: 'hitl_executor', rec: data.recommendation, time: ts() });
        }
      } catch (err) {
        appendA('HITL resolution failed: ' + err.message, []);
      }
    });
  }

  return (
    <div className="mg fi">
      <div className="mav" style={{ background: 'var(--ag)' }}>🤖</div>
      <div className="mb" style={{ maxWidth: '90%' }}>
        <div className="mmeta"><span className="msndr">DRO Agent</span><span className="mtm">{msg.time}</span></div>
        <div className="rrow"><span className="rs">data foundation - HITL gate</span></div>
        <div className="bbl a">
          <div style={{ border: '1.5px solid #f59e0b', borderRadius: '10px', padding: '14px', background: 'rgba(245,158,11,0.07)' }}>
            <div style={{ fontSize: '9px', fontWeight: 700, color: '#f59e0b', fontFamily: 'var(--m)', marginBottom: '10px' }}>HITL - DATA FOUNDATION AGENT - Signal Quality Gate</div>
            <strong style={{ color: 'var(--t)' }}>Signal flagged - operator decision required before analysis continues</strong>
            <div style={{ margin: '10px 0', fontSize: '12px', color: 'var(--t2)', lineHeight: '1.8' }}>
              Quality score: <strong style={{ color: '#f59e0b' }}>{qPct}</strong><br />
              Missing fields: <strong>{missingStr}</strong><br />
              <span dangerouslySetInnerHTML={{ __html: impFields }} />
            </div>
            <div style={{ fontSize: '11px', color: 'var(--t3)', marginBottom: '12px' }}>Choose how to handle this signal before the pipeline continues:</div>
            {!resolved ? (
              <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
                <button disabled={!canImpute} onClick={() => resolve('IMPUTE')}
                  style={{ padding: '7px 16px', borderRadius: '6px', border: 'none', background: canImpute ? '#22c55e' : '#374151', color: '#fff', fontWeight: 700, cursor: canImpute ? 'pointer' : 'not-allowed', fontSize: '12px', opacity: canImpute ? 1 : 0.4 }}>
                  Impute from baseline
                </button>
                <button onClick={() => resolve('KEEP')}
                  style={{ padding: '7px 16px', borderRadius: '6px', border: 'none', background: '#f59e0b', color: '#fff', fontWeight: 700, cursor: 'pointer', fontSize: '12px' }}>
                  Keep with quality flag
                </button>
                <button onClick={() => resolve('DROP')}
                  style={{ padding: '7px 16px', borderRadius: '6px', border: 'none', background: '#ef4444', color: '#fff', fontWeight: 700, cursor: 'pointer', fontSize: '12px' }}>
                  Drop this signal
                </button>
              </div>
            ) : (
              <div style={{ fontSize: '11px', color: '#22c55e', fontWeight: 700 }}>✓ Decision made — pipeline resuming…</div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

function HITLAdvisoryMsg({ msg, doThink, appendA, persona }) {
  const [decision, setDecision] = useState(null);
  const h = msg.data.hitl_advisory;

  function resolve(action) {
    setDecision(action.toLowerCase());
    doThink(async () => {
      try {
        const data = await resolveHITLAdvisory(h.run_id, action, persona);
        appendA(`LLM advisory ${data.status}. Deterministic risk and RUL values were unchanged.`, ['predictive risk', 'HITL']);
      } catch (err) {
        setDecision(null);
        appendA('Advisory HITL resolution failed: ' + err.message, []);
      }
    });
  }

  return (
    <div className="mg fi">
      <div className="mav" style={{ background: 'var(--ag)' }}>🤖</div>
      <div className="mb" style={{ maxWidth: '90%' }}>
        <div className="mmeta"><span className="msndr">DRO Agent</span><span className="mtm">{msg.time}</span></div>
        <div className="rrow"><span className="rs">predictive risk - LLM advisory gate</span></div>
        <div className="bbl a">
          <div style={{ border: '1.5px solid #8b5cf6', borderRadius: '10px', padding: '14px', background: 'rgba(139,92,246,0.07)' }}>
            <div style={{ fontSize: '9px', fontWeight: 700, color: '#8b5cf6', fontFamily: 'var(--m)', marginBottom: '10px' }}>HITL - PREDICTIVE RISK AGENT - LLM Advisory Review</div>
            <strong style={{ color: 'var(--t)' }}>LLM generated an advisory - review before committing to assessment</strong>
            <div style={{ margin: '10px 0', fontSize: '12px', color: 'var(--t2)', background: 'rgba(139,92,246,0.08)', padding: '10px 12px', borderRadius: '6px', borderLeft: '3px solid #8b5cf6', lineHeight: '1.7' }}>
              &ldquo;{h.advisory_note}&rdquo;
            </div>
            <div style={{ fontSize: '11px', color: 'var(--t3)', marginBottom: '10px' }}>Accept to include in the assessment, or reject to use deterministic rules only.</div>
            {!decision ? (
              <div style={{ display: 'flex', gap: '8px' }}>
                <button onClick={() => resolve('ACCEPT')}
                  style={{ padding: '7px 16px', borderRadius: '6px', border: 'none', background: '#8b5cf6', color: '#fff', fontWeight: 700, cursor: 'pointer', fontSize: '12px' }}>
                  Accept advisory
                </button>
                <button onClick={() => resolve('REJECT')}
                  style={{ padding: '7px 16px', borderRadius: '6px', border: '1px solid #8b5cf6', background: 'transparent', color: '#8b5cf6', fontWeight: 700, cursor: 'pointer', fontSize: '12px' }}>
                  Reject - rules only
                </button>
              </div>
            ) : decision === 'accept' ? (
              <div style={{ fontSize: '11px', color: '#22c55e', fontWeight: 700 }}>✓ Advisory accepted — included in risk assessment</div>
            ) : (
              <div style={{ fontSize: '11px', color: '#6b7280', fontWeight: 700 }}>✗ Advisory rejected — deterministic rules result retained</div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

function HITLMonitoringMsg({ msg, doThink, appendA, addMessage, persona }) {
  const [resolved, setResolved] = useState(false);
  const h = msg.data.hitl_monitoring;
  const rid = h.run_id;
  const scorePct = h.anomaly_score != null ? (h.anomaly_score * 100).toFixed(0) + '%' : '—';
  const ewmaSignals = (h.ewma_triggered && h.ewma_triggered.length > 0) ? h.ewma_triggered.join(', ') : 'multiple signals';

  async function resolve(action) {
    setResolved(true);
    doThink(async () => {
      try {
        const data = await resolveHITLMonitoring(rid, action, persona);
        const label = action === 'SUPPRESS' ? '✓ Anomaly suppressed — no fault analysis' : '✓ Anomaly confirmed — proceeding with diagnosis';
        if (data.hitl_diagnosis) {
          appendA(`<span style="font-size:10px;color:#22c55e;font-weight:700">✓ ${label}</span>`, []);
          addMessage({ id: mkId(), type: 'hitl_diagnosis', data, scenario: msg.scenario, persona, time: ts() });
          return;
        }
        const ms = data.pipeline_log ? data.pipeline_log.reduce((s, n) => s + (n.latency_ms || 0), 0) : 0;
        const html = `<div style="margin-bottom:4px"><span style="font-size:9px;font-weight:700;color:#22c55e;font-family:var(--m)">✓ HITL RESOLVED · ${label} · ${ms}ms</span></div>`
          + `<strong style="color:var(--t)">${data.headline}</strong><br><br>`
          + data.details.map(d => '• ' + d).join('<br>')
          + (data.actions && data.actions.length ? '<br><br><strong>Recommended actions:</strong><br>' + data.actions.map(a => '→ ' + a).join('<br>') : '');
        appendA(html, data.pipeline_log ? data.pipeline_log.map(n => n.node ? n.node.replace(/_/g, ' ') : '') : []);
        if (data.hitl_advisory && data.hitl_advisory.advisory_note) addMessage({ id: mkId(), type: 'hitl_advisory', data, time: ts() });
        if (data.hitl_knowledge) addMessage({ id: mkId(), type: 'hitl_knowledge', data, persona, time: ts() });
        if (data.recommendation && data.recommendation.recommendation_status === 'ok') addMessage({ id: mkId(), type: 'hitl_executor', rec: data.recommendation, time: ts() });
      } catch (err) { appendA('Monitoring HITL resolution failed: ' + err.message, []); }
    });
  }

  return (
    <div className="mg fi">
      <div className="mav" style={{ background: 'var(--ag)' }}>🤖</div>
      <div className="mb" style={{ maxWidth: '90%' }}>
        <div className="mmeta"><span className="msndr">DRO Agent</span><span className="mtm">{msg.time}</span></div>
        <div className="rrow"><span className="rs">monitoring · HITL gate</span></div>
        <div className="bbl a">
          <div style={{ border: '1.5px solid #f59e0b', borderRadius: '10px', padding: '14px', background: 'rgba(245,158,11,0.06)' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '10px' }}>
              <span style={{ fontSize: '9px', fontWeight: 700, color: '#f59e0b', fontFamily: 'var(--m)' }}>HITL · MONITORING AGENT · Borderline EWMA Alert</span>
              <span style={{ fontSize: '9px', fontWeight: 600, color: '#64748b', marginLeft: 'auto' }}>FOR: OT Controls / Supervisor</span>
            </div>
            <strong style={{ color: 'var(--t)' }}>Borderline anomaly — operator confirmation required before fault diagnosis proceeds</strong>
            <div style={{ margin: '10px 0', fontSize: '12px', color: 'var(--t2)', lineHeight: '1.9' }}>
              Anomaly score: <strong style={{ color: '#f59e0b' }}>{scorePct}</strong><br />
              Trigger: <strong>EWMA control chart only</strong> — Hotelling T² multivariate detector did not fire<br />
              EWMA signals: <strong>{ewmaSignals}</strong><br />
              Regime: {h.regime || 'normal running'}
            </div>
            <div style={{ fontSize: '11px', color: 'var(--t3)', marginBottom: '12px', padding: '8px', background: 'rgba(245,158,11,0.06)', borderRadius: '6px', borderLeft: '2px solid #f59e0b' }}>
              {h.reason || 'EWMA fired on a single signal dimension but the joint multivariate detector did not trigger, which may indicate a startup transient or sensor noise rather than a real bearing defect.'}
            </div>
            {!resolved ? (
              <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
                <button onClick={() => resolve('SUPPRESS')}
                  style={{ padding: '7px 18px', borderRadius: '6px', border: 'none', background: '#f59e0b', color: '#fff', fontWeight: 700, cursor: 'pointer', fontSize: '12px' }}>
                  Suppress — expected transient
                </button>
                <button onClick={() => resolve('CONFIRM')}
                  style={{ padding: '7px 18px', borderRadius: '6px', border: 'none', background: '#ef4444', color: '#fff', fontWeight: 700, cursor: 'pointer', fontSize: '12px' }}>
                  Confirm — proceed with diagnosis
                </button>
              </div>
            ) : (
              <div style={{ fontSize: '11px', color: '#22c55e', fontWeight: 700 }}>✓ Decision made — pipeline resuming…</div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

function HITLDiagnosisMsg({ msg, doThink, appendA, addMessage, persona }) {
  const [resolved, setResolved] = useState(false);
  const h = msg.data.hitl_diagnosis;
  const rid = h.run_id;
  const confPct = h.confidence != null ? (h.confidence * 100).toFixed(0) + '%' : '—';
  const faultLabel = (h.fault_mode || 'unknown').replace(/_/g, ' ');
  const stageLabel = h.iso_stage > 0 ? 'ISO Stage ' + h.iso_stage : 'Stage unknown';

  async function resolve(action) {
    setResolved(true);
    doThink(async () => {
      try {
        const data = await resolveHITLDiagnosis(rid, action, persona);
        const label = action === 'CONFIRM' ? '✓ Classification confirmed' : '✓ Overridden to undetermined — manual inspection';
        const ms = data.pipeline_log ? data.pipeline_log.reduce((s, n) => s + (n.latency_ms || 0), 0) : 0;
        const html = `<div style="margin-bottom:4px"><span style="font-size:9px;font-weight:700;color:#22c55e;font-family:var(--m)">✓ HITL RESOLVED · ${label} · Pipeline resumed · ${ms}ms</span></div>`
          + `<strong style="color:var(--t)">${data.headline}</strong><br><br>`
          + data.details.map(d => '• ' + d).join('<br>')
          + (data.actions && data.actions.length ? '<br><br><strong>Recommended actions:</strong><br>' + data.actions.map(a => '→ ' + a).join('<br>') : '');
        appendA(html, data.pipeline_log ? data.pipeline_log.map(n => n.node ? n.node.replace(/_/g, ' ') : '') : []);
        if (data.hitl_advisory && data.hitl_advisory.advisory_note) addMessage({ id: mkId(), type: 'hitl_advisory', data, time: ts() });
        if (data.hitl_knowledge) addMessage({ id: mkId(), type: 'hitl_knowledge', data, persona, time: ts() });
        if (data.recommendation && data.recommendation.recommendation_status === 'ok') addMessage({ id: mkId(), type: 'hitl_executor', rec: data.recommendation, time: ts() });
      } catch (err) { appendA('Diagnosis HITL resolution failed: ' + err.message, []); }
    });
  }

  return (
    <div className="mg fi">
      <div className="mav" style={{ background: 'var(--ag)' }}>🤖</div>
      <div className="mb" style={{ maxWidth: '90%' }}>
        <div className="mmeta"><span className="msndr">DRO Agent</span><span className="mtm">{msg.time}</span></div>
        <div className="rrow"><span className="rs">failure intelligence · HITL gate</span></div>
        <div className="bbl a">
          <div style={{ border: '1.5px solid #f97316', borderRadius: '10px', padding: '14px', background: 'rgba(249,115,22,0.06)' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '10px' }}>
              <span style={{ fontSize: '9px', fontWeight: 700, color: '#f97316', fontFamily: 'var(--m)' }}>HITL · FAILURE INTELLIGENCE AGENT · Low Confidence Classification</span>
              <span style={{ fontSize: '9px', fontWeight: 600, color: '#64748b', marginLeft: 'auto' }}>FOR: Reliability Engineer</span>
            </div>
            <strong style={{ color: 'var(--t)' }}>Low-confidence fault classification — engineer confirmation required before risk proceeds</strong>
            <div style={{ margin: '10px 0', fontSize: '12px', color: 'var(--t2)', lineHeight: '1.9' }}>
              Classified fault: <strong>{faultLabel}</strong>{h.fault_code && <span style={{ color: 'var(--t3)' }}> ({h.fault_code})</span>}<br />
              Stage: <strong>{stageLabel}</strong><br />
              Confidence: <strong style={{ color: '#f97316' }}>{confPct}</strong> <span style={{ color: 'var(--t3)' }}>(threshold: 60%)</span>
            </div>
            <div style={{ fontSize: '11px', color: 'var(--t2)', marginBottom: '12px', padding: '8px 10px', background: 'rgba(249,115,22,0.06)', borderRadius: '6px', borderLeft: '2px solid #f97316' }}>
              {h.narrative || 'Insufficient signal evidence for a conclusive fault classification.'}
            </div>
            <div style={{ fontSize: '11px', color: 'var(--t3)', marginBottom: '12px' }}>Confirm to proceed with this classification, or override to undetermined to trigger manual inspection.</div>
            {!resolved ? (
              <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
                <button onClick={() => resolve('CONFIRM')}
                  style={{ padding: '7px 18px', borderRadius: '6px', border: 'none', background: '#f97316', color: '#fff', fontWeight: 700, cursor: 'pointer', fontSize: '12px' }}>
                  Confirm — {faultLabel}
                </button>
                <button onClick={() => resolve('MARK_UNDETERMINED')}
                  style={{ padding: '7px 18px', borderRadius: '6px', border: '1px solid #6b7280', background: 'transparent', color: 'var(--t2)', fontWeight: 700, cursor: 'pointer', fontSize: '12px' }}>
                  Override — mark undetermined
                </button>
              </div>
            ) : (
              <div style={{ fontSize: '11px', color: '#22c55e', fontWeight: 700 }}>✓ Decision made — pipeline resuming…</div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

function HITLKnowledgeMsg({ msg, doThink, appendA, persona }) {
  const [resolved, setResolved] = useState(false);
  const h = msg.data.hitl_knowledge;
  const rid = h.run_id;
  const faultLabel = (h.fault_mode || 'unknown').replace(/_/g, ' ');
  const assetId = h.asset_id || 'unknown';

  async function resolve(action) {
    setResolved(true);
    doThink(async () => {
      try {
        const data = await resolveHITLKnowledge(rid, action, persona);
        const label = action === 'FLAG_MANUAL' ? '✓ Flagged for manual SOP lookup' : '✓ Proceeding without SOP';
        const ms = data.pipeline_log ? data.pipeline_log.reduce((s, n) => s + (n.latency_ms || 0), 0) : 0;
        const html = `<div style="margin-bottom:4px"><span style="font-size:9px;font-weight:700;color:#22c55e;font-family:var(--m)">✓ HITL RESOLVED · ${label} · ${ms}ms</span></div>`
          + `<strong style="color:var(--t)">${data.headline}</strong><br><br>`
          + data.details.map(d => '• ' + d).join('<br>')
          + (data.actions && data.actions.length ? '<br><br><strong>Recommended actions:</strong><br>' + data.actions.map(a => '→ ' + a).join('<br>') : '');
        appendA(html, data.pipeline_log ? data.pipeline_log.map(n => n.node ? n.node.replace(/_/g, ' ') : '') : []);
        if (data.recommendation && data.recommendation.recommendation_status === 'ok') addMessage({ id: mkId(), type: 'hitl_executor', rec: data.recommendation, time: ts() });
      } catch (err) { appendA('Knowledge HITL resolution failed: ' + err.message, []); }
    });
  }

  return (
    <div className="mg fi">
      <div className="mav" style={{ background: 'var(--ag)' }}>🤖</div>
      <div className="mb" style={{ maxWidth: '90%' }}>
        <div className="mmeta"><span className="msndr">DRO Agent</span><span className="mtm">{msg.time}</span></div>
        <div className="rrow"><span className="rs">knowledge · HITL gate</span></div>
        <div className="bbl a">
          <div style={{ border: '1.5px solid #06b6d4', borderRadius: '10px', padding: '14px', background: 'rgba(6,182,212,0.06)' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '10px' }}>
              <span style={{ fontSize: '9px', fontWeight: 700, color: '#06b6d4', fontFamily: 'var(--m)' }}>HITL · KNOWLEDGE AGENT · No SOP Found</span>
              <span style={{ fontSize: '9px', fontWeight: 600, color: '#64748b', marginLeft: 'auto' }}>FOR: Safety Officer / Maintenance</span>
            </div>
            <strong style={{ color: 'var(--t)' }}>No standard operating procedure found — manual SOP lookup required</strong>
            <div style={{ margin: '10px 0', fontSize: '12px', color: 'var(--t2)', lineHeight: '1.9' }}>
              Asset: <strong>{assetId}</strong><br />
              Fault mode: <strong>{faultLabel}</strong><br />
              <span style={{ color: '#ef4444', fontWeight: 600 }}>No SOP found in the catalog for this fault / asset combination.</span>
            </div>
            <div style={{ fontSize: '11px', color: 'var(--t3)', marginBottom: '12px' }}>
              A Safety Officer or Maintenance lead must confirm the correct procedure before any work begins. Alternatively, accept and apply general site maintenance guidelines.
            </div>
            {!resolved ? (
              <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
                <button onClick={() => resolve('FLAG_MANUAL')}
                  style={{ padding: '7px 18px', borderRadius: '6px', border: 'none', background: '#ef4444', color: '#fff', fontWeight: 700, cursor: 'pointer', fontSize: '12px' }}>
                  Flag for manual SOP lookup
                </button>
                <button onClick={() => resolve('ACCEPT_EMPTY')}
                  style={{ padding: '7px 18px', borderRadius: '6px', border: '1px solid #06b6d4', background: 'transparent', color: '#06b6d4', fontWeight: 700, cursor: 'pointer', fontSize: '12px' }}>
                  Proceed without SOP
                </button>
              </div>
            ) : (
              <div style={{ fontSize: '11px', color: '#22c55e', fontWeight: 700 }}>✓ Decision made — pipeline resuming…</div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

function buildLearningHtml(lr) {
  const statusColors = { learned: '#10b981', duplicate: '#3b82f6', invalid_input: '#ef4444', persistence_failed: '#f59e0b' };
  const statusLabels = { learned: 'CASE LEARNED & STORED', duplicate: 'DUPLICATE — ALREADY KNOWN', invalid_input: 'INVALID INPUT — NOT STORED', persistence_failed: 'STORAGE ERROR' };
  const color = statusColors[lr.learning_status] || '#6b7280';
  const label = statusLabels[lr.learning_status] || lr.learning_status?.toUpperCase() || 'UNKNOWN';

  let tagsHtml = '';
  if (lr.tags && lr.tags.length) {
    tagsHtml = `<div style="display:flex;flex-wrap:wrap;gap:4px;margin-bottom:10px">`
      + lr.tags.map(t => `<span style="font-size:10px;padding:2px 7px;border-radius:3px;background:rgba(59,130,246,0.12);color:var(--t2)">${t.replace(/_/g, ' ')}</span>`).join('')
      + `</div>`;
  }

  let metaHtml = `<div style="font-size:12px;color:var(--t2);line-height:2;margin-bottom:8px">`;
  if (lr.case_id) metaHtml += `Case ID: <strong style="font-family:var(--m);color:${color}">${lr.case_id}</strong><br/>`;
  if (lr.fault_mode) metaHtml += `Fault mode: <strong>${lr.fault_mode}</strong><br/>`;
  if (lr.outcome) metaHtml += `Outcome: <strong>${lr.outcome}</strong><br/>`;
  if (lr.persistence_status && lr.persistence_status !== 'not_requested') metaHtml += `Persistence: <strong>${lr.persistence_status}</strong><br/>`;
  if (lr.status_reason) metaHtml += `<span style="color:#6b7280;font-size:11px">${lr.status_reason}</span><br/>`;
  metaHtml += `</div>`;

  const bodyText = lr.content || '';

  return `<div style="border:1.5px solid ${color};border-radius:10px;padding:14px;background:rgba(59,130,246,0.03);margin-top:2px">`
    + `<div style="font-size:9px;font-weight:700;color:${color};font-family:var(--m);margin-bottom:10px">PHASE 10 · LEARNING & MEMORY AGENT · ${label}</div>`
    + metaHtml
    + tagsHtml
    + (bodyText ? `<div style="font-size:11px;color:var(--t2);background:rgba(59,130,246,0.06);padding:9px 11px;border-radius:6px;border-left:2px solid ${color};line-height:1.6">${bodyText}</div>` : '')
    + `</div>`;
}

function HITLExecutorMsg({ msg, doThink, appendA, persona }) {
  const [resolved, setResolved] = useState(false);
  const { refreshNotifCounts, pushNotification } = useContext(AppContext);
  const rec = msg.rec;
  const action = rec.recommended_action;
  const urgencyColor = { immediate: '#ef4444', urgent: '#f97316', planned: '#3b82f6', monitor: '#6b7280' }[rec.urgency] || '#6b7280';

  async function resolve(approved) {
    setResolved(true);
    doThink(async () => {
      try {
        const data = await runExecutor(rec, approved, persona);
        refreshNotifCounts();

        // Notify maintenance persona when a work order is successfully created
        if (approved && data.status !== 'blocked' && data.work_order_id) {
          const notifHtml = `<div style="border:1.5px solid #10b981;border-radius:8px;padding:12px;background:rgba(16,185,129,0.06)">`
            + `<div style="font-size:9px;font-weight:700;color:#10b981;font-family:var(--m);margin-bottom:8px">WORK ORDER ASSIGNED TO MAINTENANCE</div>`
            + `<strong>Work order ${data.work_order_id} created — action required</strong>`
            + `<div style="font-size:12px;color:var(--t2);margin-top:8px;line-height:1.9">`
            + `Asset: <strong>${rec.asset_id}</strong><br/>`
            + `Action: <strong>${action.name.replace(/_/g, ' ')}</strong><br/>`
            + `Urgency: <strong style="color:${urgencyColor}">${rec.urgency.toUpperCase()}</strong><br/>`
            + `Window: <strong>${rec.window_chosen}</strong><br/>`
            + `Approved by: <strong>${rec.responsible_approver}</strong></div></div>`;
          pushNotification('maintenance', { id: mkId(), type: 'agent', html: notifHtml, routes: ['executor · notification'], chips: [], time: ts() });
        }

        const statusColor = { success: '#10b981', partial: '#f59e0b', blocked: '#6b7280', failed: '#ef4444' };
        const color = statusColor[data.status] || '#6b7280';
        const label = approved ? '✓ APPROVED & EXECUTED' : '✗ REJECTED';
        let html = `<div style="border:1.5px solid ${color};border-radius:10px;padding:14px;background:rgba(16,185,129,0.04)">`
          + `<div style="font-size:9px;font-weight:700;color:${color};font-family:var(--m);margin-bottom:10px">${label} · EXECUTOR RESULT</div>`
          + `<div style="display:flex;gap:8px;align-items:center;margin-bottom:10px;flex-wrap:wrap">`
          + `<span style="font-size:11px;font-weight:700;padding:3px 10px;border-radius:4px;background:${color};color:#fff">${data.status.toUpperCase()}</span>`
          + `<span style="font-size:11px;color:var(--t2)">action: <strong>${(data.action_taken || '—').replace(/_/g, ' ')}</strong></span></div>`;
        if (data.status === 'blocked') {
          html += `<div style="font-size:12px;color:#6b7280;margin-bottom:8px">⛔ ${data.blocked_reason || 'Execution blocked'}</div>`;
        } else {
          html += `<div style="font-size:12px;color:var(--t2);line-height:2;margin-bottom:8px">`
            + `Work order: <strong style="color:#10b981">${data.work_order_id || '—'}</strong><br/>`
            + `Notification: <strong>${data.notification_status || '—'}</strong><br/>`
            + `Audit ref: <span style="font-family:var(--m);font-size:10px;color:var(--t3)">${data.audit_reference || '—'}</span></div>`;
        }
        html += '</div>';
        appendA(html, ['executor · result']);
      } catch (err) { appendA('Executor call failed: ' + err.message, []); }
    });
  }

  return (
    <div className="mg fi">
      <div className="mav" style={{ background: 'var(--ag)' }}>🤖</div>
      <div className="mb" style={{ maxWidth: '90%' }}>
        <div className="mmeta"><span className="msndr">DRO Agent</span><span className="mtm">{msg.time}</span></div>
        <div className="rrow"><span className="rs">executor · approval gate</span></div>
        <div className="bbl a">
          <div style={{ border: '1.5px solid #10b981', borderRadius: '10px', padding: '14px', background: 'rgba(16,185,129,0.06)' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '10px' }}>
              <span style={{ fontSize: '9px', fontWeight: 700, color: '#10b981', fontFamily: 'var(--m)' }}>HITL · EXECUTOR AGENT · Execution Approval</span>
              <span style={{ fontSize: '9px', fontWeight: 600, color: '#64748b', marginLeft: 'auto' }}>FOR: Plant Supervisor</span>
            </div>
            <strong style={{ color: 'var(--t)' }}>Maintenance recommendation awaiting approval before execution</strong>
            <div style={{ margin: '10px 0', fontSize: '12px', color: 'var(--t2)', lineHeight: '2' }}>
              Case: <strong>{rec.case_id}</strong> &nbsp;|&nbsp; Asset: <strong>{rec.asset_id}</strong><br />
              Action: <strong>{action.name.replace(/_/g, ' ')}</strong> &nbsp;<span style={{ fontSize: '10px', color: 'var(--t3)' }}>({action.description})</span><br />
              Urgency: <strong style={{ color: urgencyColor }}>{rec.urgency.toUpperCase()}</strong>
              &nbsp;|&nbsp; Est. duration: <strong>{action.estimated_duration_hours}h</strong>
              &nbsp;|&nbsp; Window: <strong>{rec.window_chosen}</strong><br />
              Parts: {rec.required_parts.map(p => (
                <span key={p.part_number} style={{ background: 'rgba(16,185,129,.1)', border: '1px solid #10b981', borderRadius: '4px', padding: '2px 8px', fontSize: '11px', color: '#10b981', marginRight: '4px' }}>
                  {p.part_number} ×{p.quantity}
                </span>
              ))}<br />
              Approver: <strong>{rec.responsible_approver}</strong>
            </div>
            <div style={{ fontSize: '11px', color: 'var(--t2)', background: 'rgba(16,185,129,0.06)', padding: '9px 11px', borderRadius: '6px', borderLeft: '2px solid #10b981', marginBottom: '12px', lineHeight: '1.6' }}>
              {rec.rationale}
            </div>
            {!resolved ? (
              <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
                <button onClick={() => resolve(true)}
                  style={{ padding: '7px 18px', borderRadius: '6px', border: 'none', background: '#10b981', color: '#fff', fontWeight: 700, cursor: 'pointer', fontSize: '12px' }}>
                  Approve &amp; Execute
                </button>
                <button onClick={() => resolve(false)}
                  style={{ padding: '7px 18px', borderRadius: '6px', border: '1px solid #ef4444', background: 'transparent', color: '#ef4444', fontWeight: 700, cursor: 'pointer', fontSize: '12px' }}>
                  Reject
                </button>
              </div>
            ) : (
              <div style={{ fontSize: '11px', color: '#22c55e', fontWeight: 700 }}>✓ Processing…</div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
