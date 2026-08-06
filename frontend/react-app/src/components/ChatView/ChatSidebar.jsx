import React, { useContext, useEffect, useRef, useState } from 'react';
import { AppContext } from '../../context/AppContext';

const PIPELINE_NODES = [
  { key: 'data_foundation',    nm: 'Data Foundation',   src: 'Telemetry contract' },
  { key: 'monitoring',         nm: 'Monitoring',         src: 'Validated sensor channels' },
  { key: 'failure_intelligence',nm: 'Failure Intel',     src: 'Diagnostic evidence' },
  { key: 'predictive_risk',    nm: 'Predictive Risk',    src: 'Configured RUL model' },
  { key: 'knowledge',          nm: 'Knowledge',          src: 'Configured retriever' },
  { key: 'prescriptive',       nm: 'Prescriptive Opt.',  src: 'Configured decision rules' },
  { key: 'executor',           nm: 'Executor',           src: 'Configured execution adapters' },
];

const URGENCY_DOT = { immediate: 'var(--rd)', urgent: 'var(--am)', planned: '#3b82f6', monitor: 'var(--t3)' };

export default function ChatSidebar({ thinking, pipelineLog, onToggle }) {
  const [pipelineOpen, setPipelineOpen] = useState(false);
  const [escalationsOpen, setEscalationsOpen] = useState(true);
  const [animIdx, setAnimIdx] = useState(-1);
  const animIntervalRef = useRef(null);
  const { messages } = useContext(AppContext);

  useEffect(() => {
    clearInterval(animIntervalRef.current);
    if (thinking) {
      setAnimIdx(0);
      animIntervalRef.current = setInterval(() => {
        setAnimIdx(i => (i < PIPELINE_NODES.length - 1 ? i + 1 : i));
      }, 400);
    } else {
      setAnimIdx(-1);
    }
    return () => clearInterval(animIntervalRef.current);
  }, [thinking]);

  function getPipelineRows() {
    if (thinking) {
      return PIPELINE_NODES.map((n, i) => ({
        nm: n.nm, src: n.src,
        tag: i < animIdx ? 'DONE' : i === animIdx ? 'RUNNING' : 'WAITING',
        cls: i < animIdx ? 'don' : i === animIdx ? 'drn' : 'dof',
      }));
    }
    if (pipelineLog && pipelineLog.length > 0) {
      const logMap = {};
      pipelineLog.forEach(n => { logMap[n.node] = n; });
      return PIPELINE_NODES.map(n => {
        const entry = logMap[n.key];
        return entry
          ? { nm: n.nm, src: entry.data_sources || n.src, tag: `${entry.latency_ms}ms`, cls: 'don' }
          : { nm: n.nm, src: n.src, tag: '—', cls: 'dsk' };
      });
    }
    return PIPELINE_NODES.map(n => ({ nm: n.nm, src: n.src, tag: 'READY', cls: 'don' }));
  }

  const pipelineRows = getPipelineRows();
  const pipelineTotalMs = pipelineLog && pipelineLog.length > 0
    ? pipelineLog.reduce((s, n) => s + (n.latency_ms || 0), 0)
    : null;
  const pipelineLabel = thinking
    ? ' · Running…'
    : pipelineLog && pipelineLog.length > 0
      ? ` · ${pipelineTotalMs}ms`
      : '';

  return (
    <div className="cctx">
      <div style={{ padding: '6px 8px 4px', borderBottom: '1px solid var(--b)', display: 'flex', justifyContent: 'flex-start' }}>
        <button
          onClick={onToggle}
          title="Hide right panel"
          style={{
            width: '20px', height: '20px', borderRadius: '4px', border: '1px solid var(--b)',
            background: 'none', color: 'var(--t3)', cursor: 'pointer',
            display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: '9px',
          }}
        >▶</button>
      </div>
      <div className="cxs">
        <div
          className="sttl"
          style={{ display: 'flex', alignItems: 'center', gap: '4px', cursor: 'pointer', userSelect: 'none' }}
          onClick={() => setPipelineOpen(o => !o)}
        >
          <span style={{ fontSize: '8px', color: 'var(--t3)', flexShrink: 0 }}>{pipelineOpen ? '▼' : '▶'}</span>
          <span>Agent Pipeline</span>
          {thinking && (
            <span style={{
              width: '5px', height: '5px', borderRadius: '50%', background: 'var(--am)',
              display: 'inline-block', animation: 'pulse 1s infinite', flexShrink: 0,
            }} />
          )}
          <span style={{ color: 'var(--t3)', fontWeight: 400, textTransform: 'none', letterSpacing: 0 }}>
            {pipelineLabel}
          </span>
        </div>
        {pipelineOpen && (
          <div className="aglst" style={{ marginTop: '7px' }}>
            {pipelineRows.map((row, i) => (
              <div key={i} className={`agrow${row.cls === 'dsk' ? ' agrow-sk' : ''}`}>
                <div className={`adc ${row.cls}`}></div>
                <div className="anm-wrap">
                  <div className="anm">{row.nm}</div>
                  {row.src && <div className="asrc">{row.src}</div>}
                </div>
                <div className="atg">{row.tag}</div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* ── Escalations ── */}
      <EscalationsSection messages={messages} open={escalationsOpen} onToggle={() => setEscalationsOpen(o => !o)} />
    </div>
  );
}

function EscalationsSection({ messages, open, onToggle }) {
  const active = (messages || []).filter(
    m => m.type === 'hitl_executor' && !m.resolved && !m.escalationTriggered && m.escalatedFrom
  );

  return (
    <div className="cxs" style={{ borderBottom: 'none' }}>
      <div
        className="sttl"
        style={{ display: 'flex', alignItems: 'center', gap: '4px', cursor: 'pointer', userSelect: 'none' }}
        onClick={onToggle}
      >
        <span style={{ fontSize: '8px', color: 'var(--t3)', flexShrink: 0 }}>{open ? '▼' : '▶'}</span>
        <span>Escalations</span>
        {active.length > 0 && (
          <span style={{
            fontSize: '8px', fontWeight: 700, color: 'var(--rd)', background: 'rgba(239,68,68,.12)',
            border: '1px solid rgba(239,68,68,.25)', borderRadius: '4px',
            padding: '0px 4px', fontFamily: 'var(--m)', marginLeft: '2px',
          }}>
            {active.length}
          </span>
        )}
      </div>
      {open && (
        <div style={{ marginTop: '7px', display: 'flex', flexDirection: 'column', gap: '5px' }}>
          {active.length === 0 ? (
            <div style={{ fontSize: '10px', color: 'var(--t3)', fontStyle: 'italic', padding: '4px 2px' }}>
              No active escalations
            </div>
          ) : active.map(msg => {
            const rec = msg.rec || {};
            const action = rec.recommended_action || {};
            const urgency = (rec.urgency || 'monitor').toLowerCase();
            const dotColor = URGENCY_DOT[urgency] || 'var(--t3)';
            return (
              <div key={msg.id} className="agrow" style={{ flexDirection: 'column', alignItems: 'flex-start', gap: '4px', padding: '7px 8px' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: '5px', width: '100%' }}>
                  <div style={{ width: '5px', height: '5px', borderRadius: '50%', background: dotColor, flexShrink: 0 }} />
                  <span style={{ fontWeight: 700, fontSize: '10px', color: 'var(--t)', fontFamily: 'var(--m)', flex: 1 }}>
                    {rec.asset_id || '—'}
                  </span>
                  <span style={{ fontSize: '8px', color: dotColor, fontFamily: 'var(--m)', fontWeight: 700, textTransform: 'uppercase' }}>
                    {urgency}
                  </span>
                </div>
                <div style={{ fontSize: '10px', color: 'var(--t2)', paddingLeft: '10px', lineHeight: '1.4' }}>
                  {(action.name || action.description || 'Maintenance action').replace(/_/g, ' ')}
                </div>
                <div style={{ fontSize: '8.5px', color: 'var(--t3)', fontFamily: 'var(--m)', paddingLeft: '10px' }}>
                  ESCALATED FROM: {msg.escalatedFrom}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
