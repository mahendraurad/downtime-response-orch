import React, { useState, useContext, useRef } from 'react';
import { AG_NODES } from '../../data/agentNodes';
import { PERSONA_TASKS } from '../../data/personaTasks';
import { ASSET_AG_STATE } from '../../data/assetAgState';
import { ASSETS } from '../../data/assets';
import { ASSET_SCENARIO } from '../../data/scenarios';
import { askChat } from '../../api/chat';
import { AppContext } from '../../context/AppContext';

const TABS = [
  { id: 'overview', lbl: 'Overview' },
  { id: 'tasks', lbl: 'Persona Tasks' },
];

// Maps pipeline_log node names to AG_NODES indices
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

function getLogEntry(pipelineResult, idx) {
  if (!pipelineResult) return null;
  return (pipelineResult.pipeline_log || []).find(e => e.node === NODE_KEYS[idx]) || null;
}

function buildLiveOutput(idx, pipelineResult, asset, persona) {
  if (!pipelineResult) return null;
  const entry = getLogEntry(pipelineResult, idx);
  if (!entry || entry.status === 'skipped') return null;

  switch (idx) {
    case 2: { // Failure Intelligence
      const fd = pipelineResult.fault_diagnosis;
      if (!fd) return null;
      const conf = fd.confidence != null ? `${Math.round(fd.confidence * 100)}%` : 'N/A';
      return `${fd.fault_type || 'Unknown fault'} · Confidence: ${conf} · Stage ${fd.severity_stage ?? 'N/A'} · ${fd.root_cause || ''}`.replace(/ · $/, '');
    }
    case 3: { // Predictive Risk
      const ra = pipelineResult.risk_assessment;
      if (!ra) return null;
      const fp = ra.failure_probability != null ? `${Math.round(ra.failure_probability * 100)}%` : 'N/A';
      const rul = ra.rul_days_estimated != null ? `${ra.rul_days_estimated} days` : 'N/A';
      return `Failure probability: ${fp} · RUL: ${rul} · Confidence: ${ra.confidence ? Math.round(ra.confidence * 100) + '%' : 'N/A'}`;
    }
    case 4: { // Knowledge
      const kg = pipelineResult.knowledge_guidance;
      if (!kg) return null;
      const cases = kg.matching_cases?.length ?? 0;
      return `SOP: ${kg.sop_id || 'N/A'} · ${cases} matching case${cases !== 1 ? 's' : ''} · ${kg.parts_availability || ''}`.replace(/ · $/, '');
    }
    case 5: { // Prescriptive
      const rec = pipelineResult.recommendation;
      if (!rec) return null;
      const act = rec.recommended_action?.name || rec.recommended_action?.description || 'N/A';
      const urg = rec.urgency ? rec.urgency.toUpperCase() : '';
      return `Action: ${act} · Urgency: ${urg} · ${rec.rationale ? rec.rationale.substring(0, 80) + '…' : ''}`.replace(/ · $/, '');
    }
    case 6: { // Executor
      const ex = pipelineResult.execution_result;
      if (!ex) return null;
      return `WO: ${ex.work_order_id || 'N/A'} · Status: ${ex.status || 'N/A'}`;
    }
    case 7: { // Learning
      const lc = pipelineResult.learned_case;
      if (!lc) return null;
      return `Case ${lc.case_id || 'logged'} · Pattern recorded · Retraining queued`;
    }
    default: {
      const entry2 = getLogEntry(pipelineResult, idx);
      if (!entry2) return null;
      const srcs = entry2.data_sources?.length;
      return srcs ? `${srcs} data source${srcs !== 1 ? 's' : ''} processed` : 'Complete';
    }
  }
}

function buildLiveKPIs(idx, pipelineResult) {
  if (!pipelineResult) return null;
  const entry = getLogEntry(pipelineResult, idx);
  const latStr = entry?.latency_ms != null ? `${(entry.latency_ms / 1000).toFixed(2)}s` : '—';

  switch (idx) {
    case 2: { // Failure Intelligence
      const fd = pipelineResult.fault_diagnosis;
      if (!fd) return null;
      return {
        Latency: latStr,
        'Fault class': fd.fault_type || '—',
        Confidence: fd.confidence != null ? `${Math.round(fd.confidence * 100)}%` : '—',
        'Severity stage': fd.severity_stage != null ? `${fd.severity_stage} of 4` : '—',
        'Similar cases': fd.similar_cases?.length != null ? String(fd.similar_cases.length) : '—',
      };
    }
    case 3: { // Predictive Risk
      const ra = pipelineResult.risk_assessment;
      if (!ra) return null;
      return {
        Latency: latStr,
        'Failure prob': ra.failure_probability != null ? `${Math.round(ra.failure_probability * 100)}%` : '—',
        'RUL estimate': ra.rul_days_estimated != null ? `${ra.rul_days_estimated}d` : '—',
        Confidence: ra.confidence != null ? `${Math.round(ra.confidence * 100)}%` : '—',
        Model: ra.model || '—',
      };
    }
    case 4: { // Knowledge
      const kg = pipelineResult.knowledge_guidance;
      if (!kg) return null;
      return {
        Latency: latStr,
        'SOP version': kg.sop_id || '—',
        'Cases matched': String(kg.matching_cases?.length ?? '—'),
        'Parts status': kg.parts_availability || '—',
        Coverage: kg.kb_coverage ? `${Math.round(kg.kb_coverage * 100)}%` : '—',
      };
    }
    case 5: { // Prescriptive
      const rec = pipelineResult.recommendation;
      if (!rec) return null;
      return {
        Latency: latStr,
        'Recommended action': rec.recommended_action?.name || rec.recommended_action?.description || '—',
        Urgency: rec.urgency || '—',
        Window: rec.window_chosen || '—',
        'Net avoidance': rec.net_avoidance_value ? `$${rec.net_avoidance_value.toLocaleString()}` : '—',
      };
    }
    case 6: { // Executor
      const ex = pipelineResult.execution_result;
      if (!ex) return null;
      return {
        Latency: latStr,
        'WO created': ex.work_order_id || '—',
        'SAP PM status': ex.status || '—',
        'Parts reserved': ex.parts_reserved || '—',
        'Crew notified': ex.crew_notified ? 'Yes' : 'Pending',
      };
    }
    default:
      return entry ? { Latency: latStr } : null;
  }
}

export default function AgentDetail({ asset, persona, selectedNode, currentTab, onTabChange, pipelineResult, pipelineRunning, sensorData }) {
  const node = AG_NODES[selectedNode] || AG_NODES[0];
  const fallbackStates = ASSET_AG_STATE[asset] || ASSET_AG_STATE['M-104'];

  let liveStates = null;
  if (pipelineResult?.pipeline_log) {
    liveStates = NODE_KEYS.map(key => {
      const entry = (pipelineResult.pipeline_log || []).find(e => e.node === key);
      if (!entry) return { s: 'ni' };
      const st = entry.status || '';
      if (st === 'running') return { s: 'nr' };
      if (st === 'skipped' || st === 'pending') return { s: 'ni' };
      return { s: 'nd' };
    });
  }

  const states = liveStates || fallbackStates;
  const state = states[selectedNode] || { s: 'ni' };
  // Normalize any stale 'node' tab value to 'overview' after tab removal
  const activeTab = currentTab === 'node' ? 'overview' : currentTab;

  return (
    <div className="ag-right">
      <div className="ag-tabs">
        {TABS.map(t => (
          <div
            key={t.id}
            className={`agtab${activeTab === t.id ? ' on' : ''}`}
            onClick={() => onTabChange(t.id)}
          >
            {t.lbl}
          </div>
        ))}
      </div>
      <div className="ag-tab-body">
        {activeTab === 'overview' && (
          <OverviewPanel
            asset={asset}
            persona={persona}
            states={states}
            selectedNode={selectedNode}
            node={node}
            state={state}
            pipelineResult={pipelineResult}
            pipelineRunning={pipelineRunning}
            sensorData={sensorData}
          />
        )}
        {activeTab === 'tasks' && (
          <PersonaTasksPanel persona={persona} asset={asset} selectedNode={selectedNode} />
        )}
      </div>
    </div>
  );
}

// ─── Overview Panel (merged with Node Detail) ────────────────────────────────

function OverviewPanel({ asset, persona, states, selectedNode, node, state, pipelineResult, pipelineRunning, sensorData }) {
  const stateLabel = { nd: 'Complete', nr: 'Running', ni: 'Standby' };
  const stateColor = { nd: 'var(--gn)', nr: 'var(--am)', ni: 'var(--t3)' };

  const fd = pipelineResult?.fault_diagnosis;
  const ra = pipelineResult?.risk_assessment;
  const rec = pipelineResult?.recommendation;

  const liveOut = buildLiveOutput(selectedNode, pipelineResult, asset, persona);
  const staticOut = node.assetOut?.[asset]?.[persona] || null;
  const nodeOut = liveOut || staticOut;

  const liveKPIs = buildLiveKPIs(selectedNode, pipelineResult);
  const kpis = liveKPIs || node.kpis;
  const nodeEntry = getLogEntry(pipelineResult, selectedNode);

  return (
    <div className="ag-tab-panel on" style={{ padding: '16px', overflow: 'auto' }}>

      {/* Live sensor tiles */}
      {sensorData && (
        <div style={{ marginBottom: '14px' }}>
          <div className="sttl" style={{ marginBottom: '6px' }}>Live Sensors · {asset}</div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: '6px' }}>
            {[
              { lbl: 'VIB', val: sensorData.vib_rms_mms?.toFixed(2), unit: 'mm/s' },
              { lbl: 'TEMP', val: sensorData.temp_c?.toFixed(1), unit: '°C' },
              { lbl: 'BPFO', val: sensorData.bpfo_ratio?.toFixed(2), unit: '×' },
              { lbl: 'RPM', val: sensorData.rpm != null ? Math.round(sensorData.rpm) : null, unit: '' },
            ].map(({ lbl, val, unit }) => (
              <div key={lbl} className="dc" style={{ textAlign: 'center', padding: '8px 4px' }}>
                <div className="dcl" style={{ marginBottom: '4px' }}>{lbl}</div>
                <div className="dcv" style={{ fontSize: '13px', fontWeight: 700 }}>
                  {val != null ? `${val}${unit}` : '—'}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Pipeline-level summary metrics */}
      {pipelineResult && (fd || ra || rec) && (
        <div style={{ marginBottom: '14px' }}>
          <div className="sttl" style={{ marginBottom: '6px' }}>Pipeline Summary</div>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '6px' }}>
            {fd && <div className="dc"><div className="dcl">Fault Type</div><div className="dcv" style={{ fontSize: '11px' }}>{fd.fault_type || '—'}</div></div>}
            {fd?.confidence != null && (
              <div className="dc">
                <div className="dcl">FI Confidence</div>
                <div className="dcv" style={{ fontSize: '12px', color: fd.confidence >= 0.8 ? 'var(--gn)' : 'var(--am)' }}>
                  {Math.round(fd.confidence * 100)}%
                </div>
              </div>
            )}
            {ra?.failure_probability != null && (
              <div className="dc">
                <div className="dcl">Failure Prob</div>
                <div className="dcv" style={{ fontSize: '12px', color: ra.failure_probability >= 0.6 ? 'var(--rd)' : ra.failure_probability >= 0.3 ? 'var(--am)' : 'var(--gn)' }}>
                  {Math.round(ra.failure_probability * 100)}%
                </div>
              </div>
            )}
            {ra?.rul_days_estimated != null && <div className="dc"><div className="dcl">RUL Estimate</div><div className="dcv" style={{ fontSize: '12px' }}>{ra.rul_days_estimated} days</div></div>}
            {rec?.urgency && (
              <div className="dc">
                <div className="dcl">Urgency</div>
                <div className="dcv" style={{ fontSize: '11px', textTransform: 'uppercase', color: rec.urgency === 'immediate' ? 'var(--rd)' : rec.urgency === 'urgent' ? 'var(--am)' : 'var(--t2)' }}>
                  {rec.urgency}
                </div>
              </div>
            )}
            {rec?.recommended_action && <div className="dc"><div className="dcl">Recommendation</div><div className="dcv" style={{ fontSize: '11px' }}>{rec.recommended_action.name || rec.recommended_action.description || '—'}</div></div>}
          </div>
        </div>
      )}

      {pipelineRunning && !pipelineResult && (
        <div style={{ padding: '12px', marginBottom: '14px', borderRadius: '8px', background: 'var(--sf)', border: '1px solid var(--b)', fontSize: '12px', color: 'var(--t3)', fontStyle: 'italic' }}>
          Running pipeline… results will appear shortly.
        </div>
      )}

      {/* ── Selected node detail (merged from Node Detail tab) ── */}
      <div style={{
        marginBottom: '14px', borderRadius: '8px',
        border: '1px solid var(--ac)', background: 'rgba(79,142,255,0.05)',
        padding: '12px 14px',
      }}>
        {/* Node header */}
        <div style={{ display: 'flex', alignItems: 'center', gap: '10px', marginBottom: '10px' }}>
          <span style={{ fontSize: '20px' }}>{node.ico}</span>
          <div style={{ flex: 1 }}>
            <div style={{ fontWeight: 700, fontSize: '13px', color: 'var(--t)' }}>{node.nm}</div>
            <div style={{ display: 'flex', alignItems: 'center', gap: '6px', marginTop: '2px' }}>
              <span style={{
                fontSize: '9px', fontWeight: 700, color: stateColor[state.s],
                fontFamily: 'var(--m)', padding: '2px 6px', borderRadius: '4px',
                background: `${stateColor[state.s]}20`,
              }}>
                {stateLabel[state.s]}
              </span>
              {nodeEntry?.latency_ms != null && (
                <span style={{ fontSize: '9px', fontFamily: 'var(--m)', color: 'var(--t3)' }}>
                  {(nodeEntry.latency_ms / 1000).toFixed(2)}s
                </span>
              )}
              {pipelineResult && <span style={{ fontSize: '9px', fontFamily: 'var(--m)', color: 'var(--gn)' }}>LIVE</span>}
            </div>
          </div>
        </div>

        {/* Role */}
        <div style={{ fontSize: '11px', color: 'var(--t2)', lineHeight: '1.6', marginBottom: '10px', padding: '8px 10px', background: 'var(--sf)', borderRadius: '6px' }}>
          {node.role}
        </div>

        {/* KPIs */}
        <div style={{ fontSize: '10px', fontWeight: 600, color: 'var(--t3)', fontFamily: 'var(--m)', marginBottom: '6px' }}>
          KPIs {pipelineResult && liveKPIs ? '· LIVE' : '· STATIC'}
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '5px', marginBottom: nodeOut ? '10px' : 0 }}>
          {Object.entries(kpis).map(([k, v]) => (
            <div key={k} className="dc">
              <div className="dcl">{k}</div>
              <div className="dcv" style={{ fontSize: '11px' }}>{v}</div>
            </div>
          ))}
        </div>

        {/* Node output */}
        {nodeOut && (
          <>
            <div style={{ fontSize: '10px', fontWeight: 600, color: 'var(--t3)', fontFamily: 'var(--m)', marginBottom: '5px' }}>
              OUTPUT · {asset} · {persona} {liveOut ? '· LIVE' : ''}
            </div>
            <div style={{
              fontSize: '11px', color: 'var(--t2)', lineHeight: '1.6',
              padding: '8px 10px', background: 'var(--sf)', borderRadius: '6px',
              borderLeft: `3px solid ${liveOut ? 'var(--gn)' : 'var(--ac)'}`,
            }}>
              {nodeOut}
            </div>
          </>
        )}
      </div>
    </div>
  );
}

// ─── Persona Tasks Panel ─────────────────────────────────────────────────────

function PersonaTasksPanel({ persona, asset, selectedNode }) {
  const tasks = (PERSONA_TASKS[persona] && PERSONA_TASKS[persona][asset]) || [];

  return (
    <div className="ag-tab-panel on" style={{ padding: '16px', overflow: 'auto' }}>
      <div className="sttl" style={{ marginBottom: '4px' }}>
        {persona.charAt(0).toUpperCase() + persona.slice(1)} Tasks · {asset}
      </div>
      <div style={{ fontSize: '11px', color: 'var(--t3)', marginBottom: '14px' }}>
        Actions for this asset in your role
      </div>
      {tasks.length === 0 ? (
        <div style={{ fontSize: '12px', color: 'var(--t3)', fontStyle: 'italic' }}>
          No specific tasks defined for this persona / asset combination.
        </div>
      ) : (
        tasks.map(([label, desc], i) => (
          <div
            key={i}
            style={{
              padding: '10px 12px',
              marginBottom: '8px',
              borderRadius: '8px',
              background: 'var(--sf)',
              border: '1px solid var(--b)',
            }}
          >
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '4px' }}>
              <span style={{
                fontSize: '9px', fontWeight: 700, color: 'var(--ac2)', fontFamily: 'var(--m)',
                background: 'rgba(79,142,255,.15)', padding: '2px 6px', borderRadius: '4px',
              }}>
                {String(i + 1).padStart(2, '0')}
              </span>
              <span style={{ fontWeight: 600, fontSize: '12px', color: 'var(--t)' }}>{label}</span>
            </div>
            <div style={{ fontSize: '11px', color: 'var(--t2)', lineHeight: '1.55', paddingLeft: '28px' }}>
              {desc}
            </div>
          </div>
        ))
      )}
    </div>
  );
}

// ─── Actions Panel ───────────────────────────────────────────────────────────

const URGENCY_STYLE = {
  immediate: { bg: 'rgba(239,68,68,0.12)', border: 'var(--rd)', text: 'var(--rd)' },
  urgent:    { bg: 'rgba(249,115,22,0.12)', border: 'var(--am)', text: 'var(--am)' },
  planned:   { bg: 'rgba(59,130,246,0.12)', border: '#3b82f6', text: '#3b82f6' },
  monitor:   { bg: 'var(--sf)', border: 'var(--b)', text: 'var(--t3)' },
};

function ActionsPanel({ asset, persona, selectedNode, pipelineResult, pipelineRunning }) {
  const { setCurrentView } = useContext(AppContext);
  const node = AG_NODES[selectedNode] || AG_NODES[0];

  // Per-asset insight questions from the ASSETS data
  const assetObj = ASSETS.find(a => a.id === asset);
  const insights = (assetObj?.insights || {})[persona] || [];

  const [activeQ, setActiveQ] = useState(null);
  const [chatResp, setChatResp] = useState(null);
  const [chatLoading, setChatLoading] = useState(false);
  const [chatError, setChatError] = useState(null);
  const conversationIdRef = useRef(null);

  const rec = pipelineResult?.recommendation;
  const urgency = rec?.urgency || 'monitor';
  const urg = URGENCY_STYLE[urgency] || URGENCY_STYLE.monitor;

  async function handleInsightClick(question, idx) {
    if (activeQ === idx) { setActiveQ(null); setChatResp(null); return; }
    setActiveQ(idx);
    setChatResp(null);
    setChatError(null);
    setChatLoading(true);
    try {
      const scenario = ASSET_SCENARIO[asset] || null;
      const resp = await askChat({
        message: question,
        persona,
        assetId: asset,
        conversationId: conversationIdRef.current,
        context: {
          scenario,
          ...(pipelineResult ? { pipeline_result: pipelineResult } : {}),
        },
      });
      conversationIdRef.current = resp.conversation_id || conversationIdRef.current;
      const answer = resp.response || resp.message || resp.answer;
      if (answer) {
        setChatResp(answer);
      } else {
        // Unexpected response shape — fall back to static
        const staticResp = assetObj?.chatResp?.[persona];
        setChatResp(staticResp || JSON.stringify(resp));
      }
    } catch (err) {
      // Fall back to static response if API fails
      const staticResp = assetObj?.chatResp?.[persona];
      if (staticResp) {
        setChatResp(staticResp);
      } else {
        setChatError('Could not fetch answer. Go to Agent Chat for a full response.');
      }
    } finally {
      setChatLoading(false);
    }
  }

  const liveOut = buildLiveOutput(selectedNode, pipelineResult, asset, persona);
  const staticOut = node.assetOut?.[asset]?.[persona];
  const out = liveOut || staticOut;

  return (
    <div className="ag-tab-panel on" style={{ padding: '16px', overflow: 'auto' }}>

      {/* Recommendation banner from live pipeline */}
      {rec && (
        <div style={{
          padding: '12px 14px', marginBottom: '16px', borderRadius: '8px',
          background: urg.bg, border: `1px solid ${urg.border}`,
        }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '6px' }}>
            <span style={{ fontSize: '9px', fontWeight: 700, fontFamily: 'var(--m)', color: urg.text, textTransform: 'uppercase' }}>
              {urgency} · Live Recommendation
            </span>
          </div>
          <div style={{ fontWeight: 600, fontSize: '12px', color: 'var(--t)', marginBottom: '4px' }}>
            {rec.recommended_action?.name || rec.recommended_action?.description || 'See recommendation'}
          </div>
          {rec.rationale && (
            <div style={{ fontSize: '11px', color: 'var(--t2)', lineHeight: '1.5' }}>
              {rec.rationale.length > 160 ? rec.rationale.substring(0, 160) + '…' : rec.rationale}
            </div>
          )}
        </div>
      )}

      {/* Selected node output */}
      {out && (
        <div style={{ marginBottom: '16px' }}>
          <div style={{ fontSize: '11px', fontWeight: 600, color: 'var(--t3)', fontFamily: 'var(--m)', marginBottom: '6px' }}>
            {node.nm.toUpperCase()} OUTPUT {liveOut ? '· LIVE' : ''}
          </div>
          <div style={{
            fontSize: '12px', color: 'var(--t2)', lineHeight: '1.6',
            padding: '10px 12px', background: 'var(--sf)', borderRadius: '6px',
            border: '1px solid var(--b)', borderLeft: `3px solid ${liveOut ? 'var(--gn)' : 'var(--ac)'}`,
          }}>
            {out}
          </div>
        </div>
      )}

      {pipelineRunning && !pipelineResult && (
        <div style={{ fontSize: '12px', color: 'var(--t3)', fontStyle: 'italic', marginBottom: '12px' }}>
          Running analysis…
        </div>
      )}

      {/* Persona insight questions */}
      

      {insights.length === 0 && !out && !pipelineRunning && (
        <div style={{ fontSize: '12px', color: 'var(--t3)', fontStyle: 'italic' }}>
          No specific actions for this node / persona combination.
        </div>
      )}
    </div>
  );
}
