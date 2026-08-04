import React, { useEffect, useState, useRef } from 'react';
import { AG_NODES } from '../../data/agentNodes';
import { ASSET_AG_STATE } from '../../data/assetAgState';

// Maps pipeline_log node names (backend) to AG_NODES indices (frontend)
const NODE_KEYS = [
  'data_foundation',
  'monitoring',
  'failure_intelligence',
  'predictive_risk',
  'knowledge',
  'prescriptive',
  'executor',
  'learning',
];

function buildLiveStates(pipelineResult) {
  if (!pipelineResult) return null;
  const log = pipelineResult.pipeline_log || [];
  return NODE_KEYS.map(key => {
    const entry = log.find(e => e.node === key);
    if (!entry) return { s: 'ni', latency_ms: null };
    const st = entry.status || '';
    if (st === 'running') return { s: 'nr', latency_ms: null };
    if (st === 'skipped' || st === 'pending') return { s: 'ni', latency_ms: null };
    // done / success / completed / any other status → done
    return { s: 'nd', latency_ms: entry.latency_ms ?? null };
  });
}

function buildNodeSummary(idx, pipelineResult) {
  if (!pipelineResult) return null;
  const log = pipelineResult.pipeline_log || [];
  const entry = log.find(e => e.node === NODE_KEYS[idx]);
  if (!entry || entry.status === 'skipped') return null;

  const ms = entry.latency_ms;
  const latStr = ms != null ? `${(ms / 1000).toFixed(2)}s` : null;

  switch (idx) {
    case 0: { // Data Foundation
      const srcs = entry.data_sources?.length ?? 0;
      return [srcs ? `${srcs} sources validated` : 'Data validated', latStr].filter(Boolean).join(' · ');
    }
    case 1: // Monitoring
      return ['Anomaly scan complete', latStr].filter(Boolean).join(' · ');
    case 2: { // Failure Intelligence
      const fd = pipelineResult.fault_diagnosis;
      if (!fd) return ['Analysis complete', latStr].filter(Boolean).join(' · ');
      const conf = fd.confidence != null ? `${Math.round(fd.confidence * 100)}%` : null;
      const parts = [fd.fault_type, conf ? `conf ${conf}` : null, fd.severity_stage ? `Stage ${fd.severity_stage}` : null, latStr];
      return parts.filter(Boolean).join(' · ');
    }
    case 3: { // Predictive Risk
      const ra = pipelineResult.risk_assessment;
      if (!ra) return ['Risk model complete', latStr].filter(Boolean).join(' · ');
      const fp = ra.failure_probability != null ? `${Math.round(ra.failure_probability * 100)}% fail prob` : null;
      const rul = ra.rul_days_estimated != null ? `RUL ${ra.rul_days_estimated}d` : null;
      return [fp, rul, latStr].filter(Boolean).join(' · ');
    }
    case 4: { // Knowledge
      const kg = pipelineResult.knowledge_guidance;
      if (!kg) return ['Knowledge retrieved', latStr].filter(Boolean).join(' · ');
      const sop = kg.sop_id ? `SOP ${kg.sop_id}` : null;
      const cases = kg.matching_cases?.length ? `${kg.matching_cases.length} cases` : null;
      return [sop, cases, latStr].filter(Boolean).join(' · ');
    }
    case 5: { // Prescriptive
      const rec = pipelineResult.recommendation;
      if (!rec) return ['Recommendation ready', latStr].filter(Boolean).join(' · ');
      const act = rec.recommended_action?.name || rec.recommended_action?.description;
      const urg = rec.urgency ? rec.urgency.toUpperCase() : null;
      return [act, urg, latStr].filter(Boolean).join(' · ');
    }
    case 6: { // Executor
      const ex = pipelineResult.execution_result;
      if (!ex) return ['Execution complete', latStr].filter(Boolean).join(' · ');
      const wo = ex.work_order_id ? `WO ${ex.work_order_id}` : null;
      return [wo || 'WO created', ex.status, latStr].filter(Boolean).join(' · ');
    }
    case 7: { // Learning
      const lc = pipelineResult.learned_case;
      return [lc?.case_id ? `Case ${lc.case_id} logged` : 'Event logged', latStr].filter(Boolean).join(' · ');
    }
    default: return latStr;
  }
}

export default function AgentPipeline({ asset, selectedNode, onNodeSelect, replayKey, pipelineResult, pipelineRunning }) {
  const [animatedNodes, setAnimatedNodes] = useState([]);
  const timeoutsRef = useRef([]);

  const fallbackStates = ASSET_AG_STATE[asset] || ASSET_AG_STATE['M-104'];
  const liveStates = buildLiveStates(pipelineResult);
  const states = liveStates || fallbackStates;

  // Clear all pending timeouts
  function clearTimeouts() {
    timeoutsRef.current.forEach(clearTimeout);
    timeoutsRef.current = [];
  }

  // Animate using static 220ms intervals (while loading or for fallback)
  useEffect(() => {
    if (pipelineResult) return; // live result handles its own animation
    clearTimeouts();
    setAnimatedNodes([]);
    let i = 0;
    const interval = setInterval(() => {
      const captured = i;
      setAnimatedNodes(prev => prev.includes(captured) ? prev : [...prev, captured]);
      i++;
      if (i >= AG_NODES.length) clearInterval(interval);
    }, 220);
    return () => { clearInterval(interval); clearTimeouts(); };
  }, [replayKey, asset, pipelineResult]);

  // When pipeline result arrives, re-animate with real cumulative latencies
  useEffect(() => {
    if (!pipelineResult) return;
    clearTimeouts();
    setAnimatedNodes([]);

    const log = pipelineResult.pipeline_log || [];
    let cumDelay = 0;

    NODE_KEYS.forEach((key, idx) => {
      const entry = log.find(e => e.node === key);
      // Scale real latency for animation: min 180ms, max 700ms per node
      const nodeDelay = entry?.latency_ms
        ? Math.min(Math.max(entry.latency_ms * 0.6, 180), 700)
        : 220;

      const t = setTimeout(() => {
        setAnimatedNodes(prev => prev.includes(idx) ? prev : [...prev, idx]);
      }, cumDelay);
      timeoutsRef.current.push(t);
      cumDelay += nodeDelay;
    });

    return clearTimeouts;
  }, [pipelineResult]);

  function getNodeCls(state, idx) {
    const stateCls = state.s === 'nd' ? ' nd' : state.s === 'nr' ? ' nr' : ' ni';
    const selCls = selectedNode === idx ? ' sel' : '';
    return 'pnode' + stateCls + selCls;
  }

  function getStatusCls(state) {
    return state.s === 'nd' ? 'pnst std' : state.s === 'nr' ? 'pnst str2' : 'pnst sti';
  }

  function getStatusLabel(state, idx) {
    if (pipelineRunning && !animatedNodes.includes(idx)) return '…';
    return state.s === 'nd' ? 'COMPLETE' : state.s === 'nr' ? 'RUNNING' : 'STANDBY';
  }

  function getConnLineCls(state) {
    return state.s === 'nd' ? 'pcline pcl-d' : state.s === 'nr' ? 'pcline pcl-r' : 'pcline pcl-i';
  }

  return (
    <div className="ag-pipe" id="ag-pipe">
      {AG_NODES.map((node, i) => {
        const state = states[i] || { s: 'ni' };
        const isVisible = animatedNodes.includes(i);
        const summary = buildNodeSummary(i, pipelineResult);
        const latMs = liveStates?.[i]?.latency_ms;

        return (
          <React.Fragment key={i}>
            <div
              className={getNodeCls(state, i)}
              style={{
                opacity: isVisible ? 1 : 0,
                transform: isVisible ? 'translateY(0)' : 'translateY(10px)',
                transition: 'opacity 0.28s ease, transform 0.28s ease',
                cursor: 'pointer',
              }}
              onClick={() => onNodeSelect(i)}
            >
              <div className="pntop">
                <div className="pnico">{node.ico}</div>
                <div className="pnnm">{node.nm}</div>
                <div style={{ display: 'flex', alignItems: 'center', gap: '4px', flexShrink: 0 }}>
                  {latMs != null && (
                    <span style={{
                      fontSize: '8px', fontFamily: 'var(--m)', color: 'var(--t3)',
                      background: 'var(--b)', padding: '1px 4px', borderRadius: '3px',
                    }}>
                      {(latMs / 1000).toFixed(2)}s
                    </span>
                  )}
                  <div className={getStatusCls(state)}>{getStatusLabel(state, i)}</div>
                </div>
              </div>
              {summary && (
                <div style={{
                  fontSize: '10px', color: 'var(--t3)', lineHeight: '1.4',
                  paddingTop: '4px', paddingLeft: '2px',
                  fontFamily: 'var(--m)',
                  overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                }}>
                  {summary}
                </div>
              )}
            </div>
            {i < AG_NODES.length - 1 && (
              <div
                className="pconn"
                style={{
                  opacity: isVisible ? 1 : 0,
                  transition: 'opacity 0.28s ease 0.1s',
                }}
              >
                <div className={getConnLineCls(state)}></div>
              </div>
            )}
          </React.Fragment>
        );
      })}
    </div>
  );
}
