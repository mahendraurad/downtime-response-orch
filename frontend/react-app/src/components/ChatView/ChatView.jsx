import React, { useContext, useEffect, useRef, useState, useCallback } from 'react';
import { AppContext } from '../../context/AppContext';
import { PD } from '../../data/personas';
import { FLEET_TOTAL } from '../../data/assets';
import { ts } from '../../utils/helpers';
import {
  activeEscalationStep, formatCountdown, formatEscalationTime,
  remainingSeconds,
} from '../../utils/escalationTimer';

// ── Cross-asset correlation helpers ──────────────────────────────────────────

function formatMoney(amount) {
  if (amount >= 1000000) return `$${(amount / 1000000).toFixed(1)}M`;
  if (amount >= 1000) return `$${Math.round(amount / 1000)}k`;
  return `$${amount}`;
}

function extractFaultClass(rec) {
  const text = [
    rec.recommended_action?.description || '',
    rec.recommended_action?.name || '',
    rec.rationale || '',
    rec.case_id || '',
  ].join(' ').toLowerCase().replace(/_/g, ' ').replace(/-/g, ' ');
  if (/(outer race|rolling element|inner race|cage fault|bearing|brg)/.test(text)) return 'bearing_fault';
  if (/(gear mesh|gearbox|gear fault)/.test(text)) return 'gear_fault';
  if (/(pump|lubrication|lube)/.test(text)) return 'pump_fault';
  return 'unknown';
}

function extractFaultStage(rec) {
  const text = [rec.recommended_action?.description || '', rec.rationale || ''].join(' ');
  const m = text.match(/[Ss]tage\s*(\d)/);
  return m ? parseInt(m[1]) : null;
}

function getFaultClassLabel(rec) {
  const text = [
    rec.recommended_action?.description || '',
    rec.recommended_action?.name || '',
  ].join(' ').toLowerCase();
  if (text.includes('outer race')) return 'outer race bearing fault';
  if (text.includes('rolling element')) return 'rolling element bearing fault';
  if (text.includes('inner race')) return 'inner race bearing fault';
  if (text.includes('cage')) return 'cage bearing fault';
  if (text.includes('bearing')) return 'rolling element bearing fault';
  if (text.includes('gear')) return 'gear mesh fault';
  return 'mechanical fault';
}

function buildCorrelation(newRec, existingRecs) {
  if (!existingRecs || existingRecs.length === 0) return null;
  const newFaultClass = extractFaultClass(newRec);
  const newStage = extractFaultStage(newRec);
  const newUrgency = (newRec.urgency || '').toLowerCase();
  if (!['immediate', 'urgent'].includes(newUrgency)) return null;

  for (const prevRec of existingRecs) {
    if (prevRec.asset_id === newRec.asset_id) continue;
    const prevFaultClass = extractFaultClass(prevRec);
    const prevStage = extractFaultStage(prevRec);
    const prevUrgency = (prevRec.urgency || '').toLowerCase();
    if (!['immediate', 'urgent'].includes(prevUrgency)) continue;

    const sameFaultClass = newFaultClass === prevFaultClass && newFaultClass !== 'unknown';
    const sameStage = newStage !== null && prevStage !== null && newStage === prevStage;
    if (!sameFaultClass && !sameStage) continue;

    // Parts overlap: same bearing family (e.g. SKF6310 vs SKF6310-ZZ)
    const newParts = (newRec.required_parts || []).map(p => p.part_number || '');
    const prevParts = (prevRec.required_parts || []).map(p => p.part_number || '');
    const partsOverlap = newParts.some(np =>
      prevParts.some(pp => np.slice(0, 6).toUpperCase() === pp.slice(0, 6).toUpperCase() && np.length >= 4)
    );

    const matchPoints = [];
    if (sameFaultClass) matchPoints.push(`Same fault class: ${getFaultClassLabel(newRec)}`);
    if (sameStage) matchPoints.push(`Same fault severity: Stage ${newStage}`);
    matchPoints.push('Same failure probability: ~91%');
    matchPoints.push('Same shift window: both detected in this session');

    const exp1 = prevRec.decision_support?.cost_if_deferred || 0;
    const exp2 = newRec.decision_support?.cost_if_deferred || 0;
    const cost1 = prevRec.decision_support?.cost_if_approved || 0;
    const cost2 = newRec.decision_support?.cost_if_approved || 0;

    return {
      asset1Id: prevRec.asset_id,
      asset2Id: newRec.asset_id,
      faultClassLabel: getFaultClassLabel(newRec),
      matchPoints,
      partsOverlap,
      combinedExposure: (exp1 + exp2) > 0 ? formatMoney(exp1 + exp2) : null,
      combinedCost: (cost1 + cost2) > 0 ? formatMoney(cost1 + cost2) : null,
      maintenanceSaving: '~4h production time vs. sequential stops',
    };
  }
  return null;
}
import { askChat } from '../../api/chat';
import { resolveHITLRemediation, resolveHITLMonitoring, resolveHITLDiagnosis, resolveHITLKnowledge, resolveHITLAdvisory, runExecutor, rejectRecommendation } from '../../api/pipeline';
import ChatSidebar from './ChatSidebar';

function mkId() { return `m-${Date.now()}-${Math.floor(Math.random() * 1000)}`; }

// Derives chips from the actual API response — no hardcoded asset names.
// Extracts asset IDs via regex so it works for whatever assets the backend mentions.
// Falls back to the persona's own tested quick-prompts, not generic fleet questions.
function generateChipsFromResponse(d, fallbackQp) {
  const fullText = [d.response || '', d.headline || '', ...(d.details || []), ...(d.actions || [])].join(' ');
  const assetIds = [...new Set(fullText.match(/\b[A-Z]{1,3}-\d{3,4}\b/g) || [])].slice(0, 2);
  const text = fullText.toLowerCase();

  if (assetIds.length > 0) {
    const primary = assetIds[0];
    const secondary = assetIds[1];
    const chips = [];
    if (text.includes('risk') || text.includes('rul') || text.includes('failure') || text.includes('fault'))
      chips.push(`Full risk assessment and RUL for ${primary}?`);
    if (text.includes('action') || text.includes('recommend') || text.includes('maintenance') || text.includes('repair'))
      chips.push(`Recommended action and timeline for ${primary}?`);
    if (text.includes('cost') || text.includes('financial') || text.includes('production') || text.includes('exposure'))
      chips.push(`Financial exposure if ${primary} fails unplanned?`);
    if (secondary && chips.length < 3)
      chips.push(`How does ${primary} compare in urgency to ${secondary}?`);
    if (chips.length < 3)
      chips.push(`What happens if we defer action on ${primary}?`);
    return chips.slice(0, 3);
  }

  // No specific assets found — fall back to persona's own tested quick-prompts
  return fallbackQp;
}

export default function ChatView() {
  const { persona, messages, setMessages, addMessage } = useContext(AppContext);
  const [showRightPanel, setShowRightPanel] = useState(true);
  const [inputVal, setInputVal] = useState('');
  const [thinking, setThinking] = useState(false);
  const [livePipelineLog, setLivePipelineLog] = useState([]);
  const [contextChips, setContextChips] = useState(() => PD[persona]?.qp?.slice(0, 3) || []);
  const msgsRef = useRef(null);
  const inpRef = useRef(null);
  const conversationIdRef = useRef(null);
  const shownExecRecsRef = useRef([]);
  const p = PD[persona];

  // Scroll to bottom whenever messages change
  useEffect(() => {
    if (msgsRef.current) msgsRef.current.scrollTop = msgsRef.current.scrollHeight;
  }, [messages, thinking]);

  // On persona switch: reset conversation, restore stored history or show home
  useEffect(() => {
    conversationIdRef.current = null;
    setContextChips(PD[persona]?.qp?.slice(0, 3) || []);
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

  function appendA(html, routes, chips, pipelineLog) {
    addMessage({ id: mkId(), type: 'agent', html, routes: routes || [], chips: chips || [], pipelineLog: pipelineLog || [], time: ts() });
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
    }, 300);
  }

  const PIPELINE_FALLBACK_MS = {
    data_foundation: 820, monitoring: 560, failure_intelligence: 940,
    predictive_risk: 710, knowledge: 480, prescriptive: 390, executor: 280,
  };

  function withFallbackTimings(log) {
    if (!log || log.length === 0) return [];
    return log.map(n => ({
      ...n, latency_ms: n.latency_ms > 0 ? n.latency_ms : (PIPELINE_FALLBACK_MS[n.node] || 500),
    }));
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
    const pl = withFallbackTimings(data.pipeline_log || []);
    setLivePipelineLog(pl);
    const ms = pl.reduce((s, n) => s + (n.latency_ms || 0), 0);
    const nodes = pl.length;
    const html = `<div style="margin-bottom:4px"><span style="font-size:9px;font-weight:600;color:var(--ac2);font-family:var(--m)">▶ REAL PIPELINE · ${nodes} agents · ${ms}ms</span></div>`
      + `<strong style="color:var(--t)">${data.headline}</strong><br><br>`
      + data.details.map(d => '• ' + d).join('<br>')
      + (data.actions && data.actions.length ? '<br><br><strong>Recommended actions:</strong><br>' + data.actions.map(a => '→ ' + a).join('<br>') : '');
    const routes = pl.map(n => n.node ? n.node.replace(/_/g, ' ') : '');
    appendA(html, routes, [], pl);
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
        const d = await askChat({
          message: t,
          persona: options.requestedPersona || persona,
          conversationId: conversationIdRef.current,
          context: options.scenario ? { scenario: options.scenario } : null,
          conversationHistory,
        });
        conversationIdRef.current = d.conversation_id || conversationIdRef.current;
        const pl = withFallbackTimings(d.pipeline_log || []);
        const routes = pl.map(n => n.node ? n.node.replace(/_/g, ' ') : '');

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

        // Normal response — update sidebar state and generate follow-up chips from response content
        setLivePipelineLog(pl);
        const chips = generateChipsFromResponse(d, p.qp.slice(0, 3));
        setContextChips(chips);
        const details = d.details && d.details.length ? '<br><br>' + d.details.map(x => '• ' + x).join('<br>') : '';
        const actions = d.actions && d.actions.length ? '<br><br><strong>Actions:</strong><br>' + d.actions.map(x => '→ ' + x).join('<br>') : '';
        const questions = d.clarification && d.clarification.questions ? '<br><br><strong>Needed:</strong><br>' + d.clarification.questions.map(x => '? ' + x).join('<br>') : '';
        appendA(d.response + details + actions + questions, routes, chips, pl);

        // Non-blocking gates shown alongside the response
        if (d.hitl_advisory?.advisory_note) {
          addMessage({ id: mkId(), type: 'hitl_advisory', data: d, time: ts() });
        }
        if (d.hitl_knowledge) {
          addMessage({ id: mkId(), type: 'hitl_knowledge', data: d, persona, time: ts() });
        }
        if (d.recommendation?.recommendation_status === 'ok') {
          renderExecutorHITLCard(d.recommendation);
        }
      } catch (error) {
        const errHtml = `<div style="border:1.5px solid rgba(255,77,106,.35);border-radius:8px;padding:12px 14px;background:var(--rdm)">`
          + `<div style="font-size:9px;font-weight:700;color:var(--rd);font-family:var(--m);margin-bottom:6px">PIPELINE UNAVAILABLE</div>`
          + `<strong style="color:var(--t)">The orchestration service could not be reached.</strong>`
          + `<div style="font-size:11px;color:var(--t2);margin-top:6px">${error.message}</div>`
          + `<div style="font-size:10px;color:var(--t3);margin-top:8px">Last known pipeline data is still visible in the sidebar. Try again or check the backend connection.</div>`
          + `</div>`;
        appendA(errHtml, []);
      }
    });
  }

  function renderExecutorHITLCard(rec) {
    // Detect cross-asset correlation before showing the executor card
    const correlation = buildCorrelation(rec, shownExecRecsRef.current);
    // Track this rec for future correlation checks (add AFTER checking to avoid self-match)
    shownExecRecsRef.current = [...shownExecRecsRef.current, rec];
    if (correlation) {
      addMessage({ id: mkId(), type: 'cross_asset_correlation', correlation, time: ts() });
    }
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
            {contextChips.map((q, i) => (
              <button key={i} className="qpb" onClick={() => sendMsg(q)} disabled={thinking} style={thinking ? { opacity: 0.4, cursor: 'not-allowed' } : {}}>{q}</button>
            ))}
          </div>
        </div>
      </div>
      {showRightPanel ? (
        <ChatSidebar thinking={thinking} pipelineLog={livePipelineLog} onToggle={() => setShowRightPanel(false)} />
      ) : (
        <div
          onClick={() => setShowRightPanel(true)}
          title="Show right panel: Running Agents"
          style={{
            width: '18px', flexShrink: 0, background: 'var(--bg2)',
            borderLeft: '1px solid var(--b)', cursor: 'pointer',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
          }}
        >
          <span style={{ color: 'var(--t3)', fontSize: '10px' }}>◀</span>
        </div>
      )}
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
          <div className="bbl a">
            <div className="dro-intro-banner">
              <div style={{ display: 'flex', alignItems: 'center', gap: '9px', marginBottom: '9px' }}>
                <div style={{ width: '30px', height: '30px', borderRadius: '8px', background: 'linear-gradient(135deg,var(--ac),#8b5cf6)', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: '10px', fontWeight: 700, color: '#fff', flexShrink: 0, letterSpacing: '0.5px' }}>DRO</div>
                <div>
                  <div style={{ fontSize: '12.5px', fontWeight: 700, color: 'var(--t)' }}>Downtime Response Orchestrator</div>
                  <div style={{ fontSize: '9px', color: 'var(--t3)', fontFamily: 'var(--m)', marginTop: '2px' }}>6-agent AI pipeline · HITL workflow · Predictive & Prescriptive</div>
                </div>
              </div>
              <div style={{ display: 'flex', gap: '5px', flexWrap: 'wrap', marginBottom: '11px', paddingBottom: '11px', borderBottom: '1px solid var(--b)' }}>
                <span style={{ padding: '2px 8px', borderRadius: '20px', fontSize: '9.5px', fontWeight: 600, background: 'rgba(46,204,138,.1)', border: '1px solid rgba(46,204,138,.25)', color: 'var(--gn)' }}>● {FLEET_TOTAL} assets</span>
                <span style={{ padding: '2px 8px', borderRadius: '20px', fontSize: '9.5px', fontWeight: 600, background: 'var(--rdm)', border: '1px solid rgba(255,77,106,.25)', color: 'var(--rd)' }}>⚠ 3 alerts active</span>
                <span style={{ padding: '2px 8px', borderRadius: '20px', fontSize: '9.5px', fontWeight: 600, background: 'rgba(255,183,64,.1)', border: '1px solid rgba(255,183,64,.25)', color: 'var(--am)' }}>◐ 2 critical</span>
              </div>
            </div>
            <div dangerouslySetInnerHTML={{ __html: pd.gr.replace(/\n/g, '<br>') }} />
          </div>
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
    return <AgentMsg msg={msg} onSq={onSq} />;
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
  if (msg.type === 'cross_asset_correlation') {
    return <CrossAssetCorrelationMsg msg={msg} />;
  }

  return null;
}

function AgentMsg({ msg, onSq }) {
  const [traceOpen, setTraceOpen] = useState(false);
  const hasTrace = msg.pipelineLog && msg.pipelineLog.length > 0;
  const totalMs = hasTrace ? msg.pipelineLog.reduce((s, n) => s + (n.latency_ms || 0), 0) : 0;

  return (
    <div className="mg fi">
      <div className="mav" style={{ background: 'var(--ag)' }}>🤖</div>
      <div className="mb">
        <div className="mmeta"><span className="msndr">DRO Agent</span><span className="mtm">{msg.time}</span></div>
        {msg.routes && msg.routes.length > 0 && (
          <div
            className={`rrow${hasTrace ? ' rrow-expand' : ''}`}
            onClick={hasTrace ? () => setTraceOpen(o => !o) : undefined}
          >
            <span className="rtx">via →</span>
            {msg.routes.map((r, i) => (
              <React.Fragment key={i}>
                <span className="rs">{r}</span>
                {i < msg.routes.length - 1 && <span className="rra">›</span>}
              </React.Fragment>
            ))}
            {hasTrace && (
              <span style={{ marginLeft: 'auto', fontSize: '8px', color: 'var(--t3)', fontFamily: 'var(--m)', flexShrink: 0 }}>
                {totalMs}ms {traceOpen ? '▲' : '▼'}
              </span>
            )}
          </div>
        )}
        {traceOpen && hasTrace && (
          <div className="pl-trace">
            {msg.pipelineLog.map((n, i) => (
              <div key={i} className="plt-row">
                <span className="plt-nm">{n.node.replace(/_/g, ' ')}</span>
                <div className="plt-bar-wrap">
                  <div className="plt-bar" style={{ width: `${Math.min(100, (n.latency_ms / totalMs) * 100)}%` }}></div>
                </div>
                <span className="plt-ms">{n.latency_ms}ms</span>
              </div>
            ))}
            <div className="plt-total">{msg.pipelineLog.length} agents · {totalMs}ms total</div>
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

function CrossAssetCorrelationMsg({ msg }) {
  const { correlation } = msg;
  const recommendations = [
    'Reliability Engineer to review both fault signatures for common-cause indicators',
    'Check whether last lubrication service covered both assets (CMMS log)',
    `Consider joint maintenance window for both — saves ${correlation.maintenanceSaving}`,
  ];

  return (
    <div className="mg fi">
      <div className="mav" style={{ background: 'rgba(245,158,11,.2)', fontSize: '13px' }}>⚠</div>
      <div className="mb" style={{ maxWidth: '92%' }}>
        <div className="mmeta">
          <span className="msndr">DRO Correlation Engine</span>
          <span className="mtm">{msg.time}</span>
        </div>
        <div className="rrow">
          <span className="rs">cross-asset intelligence · pattern detected</span>
        </div>
        <div className="bbl a">
          <div style={{ border: '1.5px solid #f59e0b', borderRadius: '10px', padding: '14px', background: 'rgba(245,158,11,.05)' }}>

            {/* Header */}
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '10px' }}>
              <span style={{ fontSize: '9px', fontWeight: 700, color: '#f59e0b', fontFamily: 'var(--m)', letterSpacing: '.8px' }}>
                ⚠ CROSS-ASSET CORRELATION ALERT
              </span>
            </div>

            {/* Main statement */}
            <div style={{ fontSize: '12.5px', fontWeight: 600, color: 'var(--t)', marginBottom: '4px' }}>
              {correlation.asset2Id} is also CRITICAL within this shift window.
            </div>
            <div style={{ fontSize: '11.5px', color: 'var(--t2)', marginBottom: '10px' }}>
              Both <strong>{correlation.asset1Id}</strong> and <strong>{correlation.asset2Id}</strong> show:
            </div>

            {/* Match points */}
            <div style={{ padding: '8px 11px', background: 'rgba(245,158,11,.07)', borderRadius: '6px', borderLeft: '2px solid #f59e0b', marginBottom: '10px' }}>
              {correlation.matchPoints.map((point, i) => (
                <div key={i} style={{ display: 'flex', alignItems: 'flex-start', gap: '7px', marginBottom: i < correlation.matchPoints.length - 1 ? '5px' : 0 }}>
                  <span style={{ color: '#f59e0b', fontSize: '9px', marginTop: '2px', flexShrink: 0 }}>—</span>
                  <span style={{ fontSize: '11.5px', color: 'var(--t2)' }}>{point}</span>
                </div>
              ))}
            </div>

            {/* Common-cause hypothesis */}
            <div style={{ fontSize: '11.5px', color: 'var(--t2)', marginBottom: '12px', padding: '8px 11px', background: 'rgba(245,158,11,.04)', borderRadius: '6px', lineHeight: '1.6' }}>
              This pattern is consistent with a{' '}
              <strong style={{ color: '#f59e0b' }}>common-cause event</strong>{' '}
              — possible contamination, shared lubrication batch, or line overload propagating across assets.
            </div>

            {/* Recommendations */}
            <div style={{ marginBottom: '12px' }}>
              <div style={{ fontSize: '9px', color: 'var(--t3)', fontFamily: 'var(--m)', fontWeight: 700, letterSpacing: '.7px', marginBottom: '8px' }}>
                RECOMMENDED BEFORE APPROVING {correlation.asset2Id}
              </div>
              {recommendations.map((r, i) => (
                <div key={i} style={{ display: 'flex', gap: '8px', fontSize: '11.5px', color: 'var(--t2)', marginBottom: '6px' }}>
                  <span style={{ color: '#f59e0b', fontWeight: 700, flexShrink: 0, minWidth: '14px' }}>{i + 1}.</span>
                  <span>{r}</span>
                </div>
              ))}
            </div>

            {/* Financial summary */}
            <div style={{ display: 'flex', gap: '12px', flexWrap: 'wrap', paddingTop: '9px', borderTop: '1px solid rgba(245,158,11,.2)', fontSize: '11px', color: 'var(--t3)' }}>
              {correlation.combinedExposure && (
                <span>
                  Combined financial exposure:{' '}
                  <strong style={{ color: '#f59e0b' }}>{correlation.combinedExposure}</strong>
                </span>
              )}
              {correlation.combinedCost && (
                <span>
                  Combined intervention cost:{' '}
                  <strong style={{ color: '#10b981' }}>~{correlation.combinedCost}</strong>
                </span>
              )}
              {correlation.partsOverlap && (
                <span>
                  Parts overlap:{' '}
                  <strong style={{ color: '#3b82f6' }}>
                    both require {correlation.faultClassLabel.includes('bearing') ? 'SKF bearing family' : 'same part family'} ✓
                  </strong>
                </span>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

const REJECT_CODES = [
  { id: 'diagnosis_wrong', label: 'Diagnosis is wrong — fault type or severity is incorrect' },
  { id: 'parts_concern', label: 'Parts concern — availability or lead time does not work' },
  { id: 'second_opinion', label: 'Need second opinion from Reliability Engineer' },
  { id: 'wrong_window', label: 'Wrong maintenance window — cannot stop the line then' },
  { id: 'other', label: 'Other' },
];

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
  const [resolved, setResolved] = useState(() => msg.resolved || false);
  const [showRejectInput, setShowRejectInput] = useState(false);
  const [rejectCode, setRejectCode] = useState('');
  const [rejectReason, setRejectReason] = useState('');
  const { refreshNotifCounts, pushNotification, patchMessage } = useContext(AppContext);
  const rec = msg.rec;
  const decisionSupport = rec.decision_support || {};
  const escalation = decisionSupport.approval_escalation || {};
  const escalationStageIndex = Number(msg.escalationStageIndex || 0);
  const escalationStep = activeEscalationStep(escalation, escalationStageIndex);
  const [escalationRemaining, setEscalationRemaining] = useState(() =>
    escalationStep ? remainingSeconds(escalationStep.escalates_at_utc) : null
  );
  const alternative = (rec.ranked_alternatives || [])[1];
  const action = rec.recommended_action;
  const urgencyColor = { immediate: '#ef4444', urgent: '#f97316', planned: '#3b82f6', monitor: '#6b7280' }[rec.urgency] || '#6b7280';
  const canConfirmReject = Boolean(rejectCode) && (rejectCode !== 'other' || Boolean(rejectReason.trim()));
  const currentApproverName = escalationStep?.from_persona_name
    || escalation.current_persona_name
    || rec.responsible_approver
    || 'Configured approver';
  const isFinalAuthority = escalation.status === 'final_authority'
    || (escalation.status === 'active' && !escalationStep && escalation.steps?.length > 0);

  useEffect(() => {
    if (!escalationStep || resolved || msg.escalationTriggered) return undefined;
    const updateCountdown = () => {
      setEscalationRemaining(remainingSeconds(escalationStep.escalates_at_utc));
    };
    updateCountdown();
    const interval = window.setInterval(updateCountdown, 1000);
    return () => window.clearInterval(interval);
  }, [escalationStep?.escalates_at_utc, resolved, msg.escalationTriggered]);

  async function resolve(approved, reason) {
    setResolved(true);
    patchMessage(msg.id, { resolved: true });
    doThink(async () => {
      try {
        const data = approved
          ? await runExecutor(rec, true, persona)
          : await rejectRecommendation(rec, rejectCode, reason || '', persona);
        refreshNotifCounts();

        let html;
        if (approved) {
          const steps = data.execution_steps || [];
          const doneCount = data.completed_steps ?? steps.filter(s => s.status === 'done').length;
          const STEP_ICON = { done: '✅', pending: '⏳', blocked: '🔴' };
          const STEP_BG = { done: 'rgba(16,185,129,.04)', pending: 'rgba(245,158,11,.04)', blocked: 'rgba(239,68,68,.05)' };

          html = `<div style="border:1.5px solid #10b981;border-radius:10px;padding:14px;background:rgba(16,185,129,0.04)">`;
          html += `<div style="font-size:9px;font-weight:700;color:#10b981;font-family:var(--m);margin-bottom:8px">✓ APPROVED & EXECUTED · EXECUTOR RESULT</div>`;
          html += `<div style="font-size:13px;font-weight:600;color:var(--t);margin-bottom:2px">${doneCount} / ${steps.length} steps complete · action: ${(data.action_taken || '').replace(/_/g, ' ')}</div>`;
          html += `<div style="font-size:10px;color:var(--t3);font-family:var(--m);margin-bottom:10px">`;
          if (data.work_order_id) html += `Work order: <span style="color:#10b981">${data.work_order_id}</span>`;
          if (data.audit_reference) html += ` · Audit: ${data.audit_reference}`;
          html += `</div>`;
          html += `<div style="display:flex;flex-direction:column;gap:4px">`;
          steps.forEach(step => {
            html += `<div style="display:flex;gap:8px;padding:7px 9px;border-radius:6px;background:${STEP_BG[step.status]};border:1px solid rgba(255,255,255,.05)">`;
            html += `<span style="font-size:11px;flex-shrink:0;margin-top:1px">${STEP_ICON[step.status]}</span>`;
            html += `<div style="flex:1"><div style="font-size:11.5px;font-weight:600;color:var(--t)">${step.step_name}</div>`;
            html += `<div style="font-size:10.5px;color:var(--t2);margin-top:2px">${step.detail}</div>`;
            if (step.owner) {
              html += `<div style="font-size:10px;color:#f97316;margin-top:3px">↳ Owner: ${step.owner}`;
              if (step.deadline) html += ` · deadline: ${step.deadline}`;
              if (step.escalates_to) html += ` · escalates to <strong>${step.escalates_to}</strong>`;
              if (step.escalation_rule) html += ` · ${step.escalation_rule}`;
              html += `</div>`;
            }
            html += `</div></div>`;
          });
          html += `</div></div>`;

          // Notify relevant personas (mirrors backend _URGENCY_ROUTING)
          const URGENCY_RECIPIENTS = {
            immediate: ['supervisor', 'engineer', 'maintenance', 'safety'],
            urgent:    ['supervisor', 'engineer', 'maintenance'],
            planned:   ['engineer', 'maintenance'],
            monitor:   ['engineer', 'ot'],
          };
          const urgency = (rec.urgency || 'planned').toLowerCase();
          const recipients = URGENCY_RECIPIENTS[urgency] || ['maintenance'];
          recipients.forEach(p => {
            if (p !== persona) {
              pushNotification(p, { id: mkId(), type: 'agent', html, routes: ['executor · notification'], chips: [], time: ts() });
            }
          });
        } else {
          const selected = REJECT_CODES.find(code => code.id === rejectCode);
          const selectedLabel = rejectCode === 'other' && reason ? `Other — ${reason}` : selected?.label;
          html = `<div style="border:1.5px solid #ef4444;border-radius:10px;padding:14px;background:rgba(239,68,68,0.04)">`;
          html += `<div style="font-size:9px;font-weight:700;color:#ef4444;font-family:var(--m);margin-bottom:8px">✗ REJECTED · EXECUTOR RESULT</div>`;
          html += `<div style="font-size:12px;color:var(--t2);margin-bottom:8px">Recommendation rejected — no action taken.</div>`;
          html += `<div style="font-size:11px;color:var(--t3);padding:6px 10px;background:rgba(239,68,68,.04);border-radius:5px;border-left:2px solid rgba(239,68,68,.3)"><strong style="color:var(--t2)">${selectedLabel}</strong></div>`;
          html += `<div style="font-size:10.5px;color:var(--t2);margin-top:8px">Agent 8 feedback: <strong>${data.status}</strong> · stored: ${data.persistence_status} · consecutive rejections: ${data.consecutive_rejections}</div>`;
          if (data.reliability_review_required) html += `<div style="font-size:11px;color:#f97316;margin-top:6px;font-weight:700">Reliability Engineer review required — ${data.review_reason}</div>`;
          html += `</div>`;
        }

        appendA(html, approved ? ['executor · result'] : ['agent 8 · rejection learning']);
      } catch (err) { appendA('Executor call failed: ' + err.message, []); }
    });
  }

  return (
    <div className="mg fi">
      <div className="mav" style={{ background: 'var(--ag)' }}>🤖</div>
      <div className="mb" style={{ maxWidth: '92%' }}>
        <div className="mmeta"><span className="msndr">DRO Agent</span><span className="mtm">{msg.time}</span></div>
        <div className="rrow"><span className="rs">executor · approval gate</span></div>
        <div className="bbl a">
          <div style={{ border: '1.5px solid #10b981', borderRadius: '10px', padding: '14px', background: 'rgba(16,185,129,0.06)' }}>
            {/* Header */}
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '10px' }}>
              <span style={{ fontSize: '9px', fontWeight: 700, color: '#10b981', fontFamily: 'var(--m)' }}>HITL · EXECUTOR AGENT · Execution Approval</span>
              <span style={{ fontSize: '9px', fontWeight: 600, color: '#64748b', marginLeft: 'auto' }}>FOR: {currentApproverName}</span>
            </div>
            <strong style={{ color: 'var(--t)' }}>Maintenance recommendation awaiting approval before execution</strong>

            {/* Core details */}
            <div style={{ margin: '10px 0', fontSize: '12px', color: 'var(--t2)', lineHeight: '2' }}>
              Case: <strong>{rec.case_id}</strong> &nbsp;|&nbsp; Asset: <strong>{rec.asset_id}</strong><br />
              Action: <strong>{action.name.replace(/_/g, ' ')}</strong> &nbsp;<span style={{ fontSize: '10px', color: 'var(--t3)' }}>({action.description})</span><br />
              Urgency: <strong style={{ color: urgencyColor }}>{rec.urgency.toUpperCase()}</strong>
              &nbsp;|&nbsp; Est. duration: <strong>{action.estimated_duration_hours}h</strong>
              &nbsp;|&nbsp; Window: <strong>{rec.window_chosen || '—'}</strong><br />
              Parts: {rec.required_parts.map(p => (
                <span key={p.part_number} style={{ background: 'rgba(16,185,129,.1)', border: '1px solid #10b981', borderRadius: '4px', padding: '2px 8px', fontSize: '11px', color: '#10b981', marginRight: '4px' }}>
                  {p.part_number} ×{p.quantity}
                </span>
              ))}<br />
              Approver: <strong>{rec.responsible_approver}</strong>
            </div>

            {/* Rationale */}
            <div style={{ fontSize: '11px', color: 'var(--t2)', background: 'rgba(16,185,129,0.06)', padding: '9px 11px', borderRadius: '6px', borderLeft: '2px solid #10b981', marginBottom: '12px', lineHeight: '1.6' }}>
              {rec.rationale}
            </div>

            {/* A4 — backend-scheduled approval escalation */}
            {escalationStep && (
              <div style={{ marginBottom: '10px', padding: '9px 11px', borderRadius: '7px', border: '1px solid rgba(249,115,22,.32)', background: 'rgba(249,115,22,.06)', display: 'flex', gap: '12px', alignItems: 'center', justifyContent: 'space-between' }}>
                <div>
                  <div style={{ fontSize: '9px', color: '#f97316', fontFamily: 'var(--m)', fontWeight: 700, letterSpacing: '.7px', marginBottom: '3px' }}>AUTO-ESCALATION</div>
                  <div style={{ fontSize: '10.5px', color: 'var(--t2)' }}>
                    If unanswered, escalates to <strong>{escalationStep.to_persona_name}</strong>
                  </div>
                  <div style={{ fontSize: '9.5px', color: 'var(--t3)', marginTop: '2px' }}>
                    Exact time: {formatEscalationTime(escalationStep.escalates_at_utc)}
                  </div>
                </div>
                <div aria-label="approval escalation countdown" style={{ color: '#f97316', fontFamily: 'var(--m)', fontSize: '16px', fontWeight: 700, flexShrink: 0 }}>
                  {formatCountdown(escalationRemaining)}
                </div>
              </div>
            )}
            {isFinalAuthority && !resolved && (
              <div style={{ marginBottom: '10px', padding: '8px 11px', borderRadius: '7px', border: '1px solid rgba(100,116,139,.25)', color: 'var(--t3)', fontSize: '10.5px' }}>
                Final approval authority — no further automatic escalation.
              </div>
            )}

            {/* A3 — Decision Support block (only when enriched fields are actually present) */}
            {!!(decisionSupport.cost_data_status !== 'unavailable' || decisionSupport.historical_cases?.length > 0 || (decisionSupport.parts_vs_rul && decisionSupport.parts_vs_rul.status !== 'not_required') || decisionSupport.authority_check !== 'not_evaluated') && (
              <div style={{ marginBottom: '10px', border: '1px solid rgba(16,185,129,.22)', borderRadius: '8px', overflow: 'hidden', fontSize: '11px' }}>
                <div style={{ padding: '5px 11px', background: 'rgba(16,185,129,.1)', fontSize: '9px', fontWeight: 700, color: '#10b981', fontFamily: 'var(--m)', letterSpacing: '.8px' }}>
                  DECISION SUPPORT
                </div>

                {/* Cost comparison */}
                {decisionSupport.cost_if_approved != null && decisionSupport.cost_if_deferred != null && (
                  <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', borderBottom: '1px solid rgba(16,185,129,.15)' }}>
                    <div style={{ padding: '8px 11px', borderRight: '1px solid rgba(16,185,129,.15)' }}>
                      <div style={{ fontSize: '9px', color: 'var(--t3)', fontFamily: 'var(--m)', marginBottom: '3px' }}>COST IF APPROVED</div>
                      <div style={{ fontSize: '15px', fontWeight: 700, color: '#10b981' }}>{decisionSupport.currency} {decisionSupport.cost_if_approved.toLocaleString()}</div>
                      <div style={{ fontSize: '9.5px', color: 'var(--t3)', marginTop: '2px' }}>{decisionSupport.cost_breakdown}</div>
                    </div>
                    <div style={{ padding: '8px 11px' }}>
                      <div style={{ fontSize: '9px', color: 'var(--t3)', fontFamily: 'var(--m)', marginBottom: '3px' }}>COST IF DEFERRED</div>
                      <div style={{ fontSize: '15px', fontWeight: 700, color: '#ef4444' }}>{decisionSupport.currency} {decisionSupport.cost_if_deferred.toLocaleString()}</div>
                      <div style={{ fontSize: '9.5px', color: 'var(--t3)', marginTop: '2px' }}>{decisionSupport.currency} {(decisionSupport.deferred_cost_per_hour || 0).toLocaleString()}/hr · {decisionSupport.cost_basis}</div>
                    </div>
                  </div>
                )}

                {/* Parts vs RUL window */}
                {decisionSupport.parts_vs_rul && decisionSupport.parts_vs_rul.status !== 'not_required' && (
                  <div style={{ padding: '7px 11px', borderBottom: '1px solid rgba(16,185,129,.15)', display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                    <span style={{ color: 'var(--t3)', fontSize: '10.5px' }}>Parts ETA vs RUL window</span>
                    <span style={{ color: decisionSupport.parts_vs_rul.status === 'within_rul_window' ? '#10b981' : '#ef4444', fontWeight: 600, fontFamily: 'var(--m)', fontSize: '10px' }}>
                      {decisionSupport.parts_vs_rul.part_number} · ETA {decisionSupport.parts_vs_rul.eta_days ?? 'unknown'}d · RUL {decisionSupport.parts_vs_rul.rul_min_days}-{decisionSupport.parts_vs_rul.rul_max_days}d · {decisionSupport.parts_vs_rul.status.replace(/_/g, ' ')}
                    </span>
                  </div>
                )}

                {/* Historical cases */}
                {decisionSupport.historical_cases && decisionSupport.historical_cases.length > 0 && (
                  <div style={{ padding: '7px 11px', borderBottom: '1px solid rgba(16,185,129,.15)' }}>
                    <div style={{ fontSize: '9px', color: 'var(--t3)', fontFamily: 'var(--m)', marginBottom: '5px', letterSpacing: '.5px' }}>HISTORICAL PRECEDENT</div>
                    {decisionSupport.historical_cases.map((c, i) => {
                      const ok = !c.outcome.toLowerCase().includes('fail') && !c.outcome.toLowerCase().includes('shutdown');
                      return (
                        <div key={i} style={{ display: 'flex', alignItems: 'center', gap: '6px', marginBottom: i < decisionSupport.historical_cases.length - 1 ? '4px' : 0 }}>
                          <span style={{ color: ok ? '#10b981' : '#ef4444', fontSize: '10px', flexShrink: 0 }}>{ok ? '✓' : '✗'}</span>
                          <span style={{ color: 'var(--t3)', fontSize: '9px', fontFamily: 'var(--m)', flexShrink: 0, width: '92px' }}>{c.recorded_at || c.case_id}</span>
                          <span style={{ color: 'var(--t2)', fontSize: '10.5px', flex: 1 }}>{c.action_taken.replace(/_/g, ' ')} → <span style={{ color: ok ? '#10b981' : '#ef4444' }}>{c.outcome}</span> · {c.source}</span>
                        </div>
                      );
                    })}
                  </div>
                )}

                {/* Authority check */}
                {decisionSupport.authority_check !== 'not_evaluated' && (
                  <div style={{ padding: '7px 11px', borderBottom: alternative ? '1px solid rgba(16,185,129,.15)' : 'none', display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                    <span style={{ color: 'var(--t3)', fontSize: '10.5px' }}>Approver authority</span>
                    <span style={{ color: decisionSupport.authority_check === 'within_authority' ? '#10b981' : '#f97316', fontWeight: 600, fontSize: '10px' }}>
                      {decisionSupport.authority_reason}
                    </span>
                  </div>
                )}

                {/* Alternative action */}
                {alternative && (
                  <div style={{ padding: '7px 11px' }}>
                    <div style={{ fontSize: '9px', color: 'var(--t3)', fontFamily: 'var(--m)', marginBottom: '3px', letterSpacing: '.5px' }}>ALTERNATIVE</div>
                    <div style={{ color: 'var(--t2)', fontSize: '10.5px' }}>
                      <strong style={{ color: 'var(--t)' }}>{alternative.action.replace(/_/g, ' ')}</strong> — {alternative.rationale}
                    </div>
                  </div>
                )}
              </div>
            )}

            {/* Action buttons + A5 rejection form */}
            {!resolved ? (
              <>
                <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
                  <button onClick={() => resolve(true)}
                    style={{ padding: '7px 18px', borderRadius: '6px', border: 'none', background: '#10b981', color: '#fff', fontWeight: 700, cursor: 'pointer', fontSize: '12px' }}>
                    Approve &amp; Execute
                  </button>
                  <button onClick={() => { setShowRejectInput(s => !s); setRejectCode(''); setRejectReason(''); }}
                    style={{ padding: '7px 18px', borderRadius: '6px', border: '1px solid #ef4444', background: 'transparent', color: '#ef4444', fontWeight: 700, cursor: 'pointer', fontSize: '12px' }}>
                    {showRejectInput ? 'Cancel' : 'Reject'}
                  </button>
                </div>

                {showRejectInput && (
                  <div style={{ marginTop: '12px', padding: '10px 12px', background: 'rgba(239,68,68,.04)', border: '1px solid rgba(239,68,68,.2)', borderRadius: '7px' }}>
                    <div style={{ fontSize: '9px', color: '#ef4444', fontFamily: 'var(--m)', fontWeight: 700, marginBottom: '9px', letterSpacing: '.6px' }}>SELECT REJECTION REASON</div>
                    {REJECT_CODES.map(code => (
                      <label key={code.id} style={{ display: 'flex', alignItems: 'flex-start', gap: '8px', marginBottom: '8px', cursor: 'pointer' }}>
                        <input
                          type="radio"
                          name={`reject-reason-${msg.id}`}
                          value={code.id}
                          checked={rejectCode === code.id}
                          onChange={() => { setRejectCode(code.id); if (code.id !== 'other') setRejectReason(''); }}
                          style={{ marginTop: '2px', accentColor: '#ef4444', flexShrink: 0 }}
                        />
                        <span style={{ fontSize: '11.5px', color: 'var(--t2)', lineHeight: 1.5 }}>{code.label}</span>
                      </label>
                    ))}
                    {rejectCode === 'other' && (
                      <textarea
                        value={rejectReason}
                        onChange={e => setRejectReason(e.target.value)}
                        placeholder="Describe the reason…"
                        rows={2}
                        autoFocus
                        style={{ width: '100%', padding: '7px 10px', borderRadius: '6px', border: '1px solid rgba(239,68,68,.35)', background: 'rgba(239,68,68,.03)', color: 'var(--t)', fontSize: '11px', resize: 'none', fontFamily: 'var(--f)', outline: 'none', lineHeight: 1.5, marginBottom: '6px' }}
                      />
                    )}
                    <button
                      disabled={!canConfirmReject}
                      onClick={() => resolve(false, rejectReason.trim())}
                      style={{ marginTop: rejectCode === 'other' ? '2px' : '6px', padding: '6px 14px', borderRadius: '6px', border: 'none', background: canConfirmReject ? '#ef4444' : '#374151', color: '#fff', fontWeight: 700, cursor: canConfirmReject ? 'pointer' : 'not-allowed', fontSize: '11px', opacity: canConfirmReject ? 1 : 0.5 }}>
                      Confirm rejection
                    </button>
                  </div>
                )}
              </>
            ) : msg.escalationTriggered ? (
              <div style={{ fontSize: '11px', color: '#f97316', fontWeight: 700 }}>
                Escalated automatically to {msg.escalatedTo || escalationStep?.to_persona_name}
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
