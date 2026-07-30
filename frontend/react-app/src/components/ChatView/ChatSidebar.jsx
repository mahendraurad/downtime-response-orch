import React, { useEffect, useRef, useState } from 'react';

const PIPELINE_NODES = [
  { key: 'data_foundation', nm: 'Data Foundation' },
  { key: 'monitoring', nm: 'Monitoring' },
  { key: 'failure_intelligence', nm: 'Failure Intel' },
  { key: 'predictive_risk', nm: 'Predictive Risk' },
  { key: 'knowledge', nm: 'Knowledge' },
  { key: 'prescriptive', nm: 'Prescriptive Opt.' },
  { key: 'executor', nm: 'Executor' },
];

const FLEET_ASSETS = [
  { nm: 'M-104', tp: 'Motor', status: 'critical', col: 'var(--rd)', q: 'Full risk briefing and recommended action for M-104' },
  { nm: 'P-207', tp: 'Pump', status: 'alert', col: 'var(--am)', q: 'P-207 bearing condition and urgency — should I act before Wednesday?' },
  { nm: 'C-301', tp: 'Conveyor', status: 'monitor', col: 'var(--am)', q: 'C-301 cage fault — how urgent and what is the right action?' },
  { nm: 'M-089', tp: 'Motor', status: 'healthy', col: 'var(--gn)', q: 'M-089 health after recent bearing replacement — any run-in concerns?' },
  { nm: 'G-112', tp: 'Gearbox', status: 'healthy', col: 'var(--gn)', q: 'G-112 gearbox post-repair performance — is it settling normally?' },
];

const RUL_ROWS = [
  { nm: 'M-104', tp: 'Motor', w: 12, col: 'var(--rd)', d: '~6d', q: 'Full risk briefing and recommended action for M-104' },
  { nm: 'P-207', tp: 'Pump', w: 38, col: 'var(--am)', d: '~18d', q: 'P-207 bearing condition and urgency — should I act before Wednesday?' },
  { nm: 'C-301', tp: 'Conveyor', w: 55, col: 'var(--am)', d: '~26d', q: 'C-301 cage fault — how urgent and what is the right action?' },
  { nm: 'M-089', tp: 'Motor', w: 80, col: 'var(--gn)', d: '~37d', q: 'M-089 health after recent bearing replacement — any run-in concerns?' },
  { nm: 'G-112', tp: 'Gearbox', w: 90, col: 'var(--gn)', d: '~43d', q: 'G-112 gearbox post-repair performance — is it settling normally?' },
];

export default function ChatSidebar({ onSq, thinking, pipelineLog }) {
  const [sv1, setSv1] = useState(14.7);
  const [sv2, setSv2] = useState(87.4);
  const [animIdx, setAnimIdx] = useState(-1);
  const sv1Ref = useRef(14.7);
  const sv2Ref = useRef(87.4);
  const animIntervalRef = useRef(null);

  useEffect(() => {
    const sensorInterval = setInterval(() => {
      sv1Ref.current = parseFloat((sv1Ref.current + (Math.random() * 0.2 - 0.05)).toFixed(1));
      sv2Ref.current = parseFloat((sv2Ref.current + (Math.random() * 0.4 - 0.1)).toFixed(1));
      setSv1(sv1Ref.current);
      setSv2(sv2Ref.current);
    }, 4200);
    return () => clearInterval(sensorInterval);
  }, []);

  // Animate pipeline nodes sequentially while an agent request is in-flight
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

  // Build the pipeline rows to display
  function getPipelineRows() {
    if (thinking) {
      return PIPELINE_NODES.map((n, i) => ({
        nm: n.nm,
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
          ? { nm: n.nm, tag: `${entry.latency_ms}ms`, cls: 'don' }
          : { nm: n.nm, tag: '—', cls: 'dsk' };
      });
    }
    // Idle — all agents healthy and ready
    return PIPELINE_NODES.map(n => ({ nm: n.nm, tag: 'READY', cls: 'don' }));
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
      {/* Fleet asset health ribbon */}
      <div className="cxs">
        <div className="sttl">Fleet Health</div>
        <div className="fleet-ribbon">
          {FLEET_ASSETS.map((a, i) => (
            <div key={i} className="fr-item" onClick={() => onSq(a.q)} title={`${a.nm} — ${a.status}`}>
              <div className="fr-dot" style={{ background: a.col, boxShadow: `0 0 5px ${a.col}` }}></div>
              <div className="fr-nm">{a.nm}</div>
              <div className="fr-tp">{a.tp}</div>
              <div className="fr-st" style={{ color: a.col }}>{a.status}</div>
            </div>
          ))}
        </div>
      </div>

      {/* Live sensors */}
      <div className="cxs">
        <div className="sttl">Live Sensors · M-104</div>
        <div className="sgrd">
          <div className="sc">
            <div className="sl">Vibration RMS</div>
            <div className="sv" style={{ color: 'var(--rd)' }}>{sv1.toFixed(1)}</div>
            <div className="su">mm/s</div>
            <div className="str stup">↑ +0.3/hr</div>
          </div>
          <div className="sc">
            <div className="sl">Temperature</div>
            <div className="sv" style={{ color: 'var(--am)' }}>{sv2.toFixed(1)}</div>
            <div className="su">°C</div>
            <div className="str stup">↑ +1.2/hr</div>
          </div>
          <div className="sc">
            <div className="sl">BPFO Ratio</div>
            <div className="sv" style={{ color: 'var(--rd)' }}>4.02</div>
            <div className="su">× base</div>
            <div className="str stst">— stable</div>
          </div>
          <div className="sc">
            <div className="sl">Speed</div>
            <div className="sv">1474</div>
            <div className="su">RPM</div>
            <div className="str stst">— nominal</div>
          </div>
        </div>
        <svg style={{ width: '100%', height: '22px', marginTop: '6px' }} viewBox="0 0 230 22" preserveAspectRatio="none">
          <polyline
            points="0,20 19,18 38,17 57,15 76,12 95,10 114,9 133,7 152,5 171,4 190,3 209,2 230,1"
            fill="none"
            stroke="var(--rd)"
            strokeWidth="1.4"
            opacity=".65"
          />
        </svg>
        <div style={{ fontSize: '8.5px', color: 'var(--t3)', fontFamily: 'var(--m)', marginTop: '1px' }}>
          Vibration · last 12h
        </div>
      </div>

      {/* Agent pipeline status */}
      <div className="cxs">
        <div className="sttl" style={{ display: 'flex', alignItems: 'center', gap: '4px' }}>
          <span>Agent Pipeline</span>
          {thinking && <span style={{ width: '5px', height: '5px', borderRadius: '50%', background: 'var(--am)', display: 'inline-block', animation: 'pulse 1s infinite', flexShrink: 0 }}></span>}
          <span style={{ color: 'var(--t3)', fontWeight: 400, textTransform: 'none', letterSpacing: 0 }}>{pipelineLabel}</span>
        </div>
        <div className="aglst">
          {pipelineRows.map((row, i) => (
            <div key={i} className={`agrow${row.cls === 'dsk' ? ' agrow-sk' : ''}`}>
              <div className={`adc ${row.cls}`}></div>
              <div className="anm">{row.nm}</div>
              <div className="atg">{row.tag}</div>
            </div>
          ))}
        </div>
      </div>

      {/* Fleet RUL watch */}
      <div className="cxs" style={{ borderBottom: 'none' }}>
        <div className="sttl">Fleet RUL Watch</div>
        <div className="rlfl">
          {RUL_ROWS.map((r, i) => (
            <div key={i} className="rlr" onClick={() => onSq(r.q)}>
              <div className="rlnm">{r.nm}</div>
              <div className="rltp">{r.tp}</div>
              <div className="rlmb">
                <div className="rlmf" style={{ width: r.w + '%', background: r.col }}></div>
              </div>
              <div className="rld" style={{ color: r.col }}>{r.d}</div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
