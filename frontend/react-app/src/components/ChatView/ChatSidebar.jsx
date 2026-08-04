import React, { useEffect, useRef, useState } from 'react';

const PIPELINE_NODES = [
  { key: 'data_foundation',    nm: 'Data Foundation',   src: 'Telemetry contract' },
  { key: 'monitoring',         nm: 'Monitoring',         src: 'Validated sensor channels' },
  { key: 'failure_intelligence',nm: 'Failure Intel',     src: 'Diagnostic evidence' },
  { key: 'predictive_risk',    nm: 'Predictive Risk',    src: 'Configured RUL model' },
  { key: 'knowledge',          nm: 'Knowledge',          src: 'Configured retriever' },
  { key: 'prescriptive',       nm: 'Prescriptive Opt.',  src: 'Configured decision rules' },
  { key: 'executor',           nm: 'Executor',           src: 'Configured execution adapters' },
];

export default function ChatSidebar({ thinking, pipelineLog, onToggle }) {
  const [pipelineOpen, setPipelineOpen] = useState(false);
  const [animIdx, setAnimIdx] = useState(-1);
  const animIntervalRef = useRef(null);

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
      <div className="cxs" style={{ borderBottom: 'none' }}>
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
    </div>
  );
}
