import React, { useContext, useState, useEffect, useRef, useCallback } from 'react';
import { AppContext } from '../../context/AppContext';
import AgentPipeline from './AgentPipeline';
import AgentDetail from './AgentDetail';
import { ASSET_SCENARIO } from '../../data/scenarios';
import { runRealPipeline } from '../../api/pipeline';
import { connectSensorWS } from '../../api/sensors';

const ASSET_OPTIONS = [
  { val: 'M-104', lbl: 'M-104 — Drive Motor Line 4 (CRITICAL)' },
  { val: 'P-207', lbl: 'P-207 — Feed Pump Station 2 (WARNING)' },
  { val: 'C-301', lbl: 'C-301 — Conveyor Drive Assembly (WARNING)' },
  { val: 'M-089', lbl: 'M-089 — Drive Motor Line 2 (HEALTHY)' },
  { val: 'G-112', lbl: 'G-112 — Gearbox Line 3 (HEALTHY)' },
];

const PERSONA_OPTIONS = [
  { val: 'supervisor', lbl: 'Plant Supervisor' },
  { val: 'engineer', lbl: 'Reliability Engineer' },
  { val: 'maintenance', lbl: 'Maint. Planner' },
  { val: 'manager', lbl: 'Plant Manager' },
  { val: 'executive', lbl: 'VP Operations' },
  { val: 'ot', lbl: 'OT / Controls' },
  { val: 'safety', lbl: 'Safety Officer' },
];

export default function AgentGraphView() {
  const {
    agentAsset, setAgentAsset,
    persona,
    selectedNode, setSelectedNode,
    currentAgTab, setCurrentAgTab,
  } = useContext(AppContext);

  const [agPersona, setAgPersona] = useState(persona);
  const [replayKey, setReplayKey] = useState(0);
  const [agPipelineResult, setAgPipelineResult] = useState(null);
  const [agPipelineRunning, setAgPipelineRunning] = useState(false);
  const [agPipelineError, setAgPipelineError] = useState(null);
  const [sensorData, setSensorData] = useState(null);
  const runningRef = useRef(false);
  const abortRef = useRef(null);

  const triggerPipeline = useCallback(async (asset, pers) => {
    if (runningRef.current) return;
    const scenario = ASSET_SCENARIO[asset] || 'outer_race_fault';
    runningRef.current = true;
    setAgPipelineRunning(true);
    setAgPipelineError(null);
    setAgPipelineResult(null);
    try {
      const result = await runRealPipeline(scenario, -1, pers);
      setAgPipelineResult(result);
    } catch (err) {
      setAgPipelineError(err.message || 'Pipeline run failed');
    } finally {
      setAgPipelineRunning(false);
      runningRef.current = false;
    }
  }, []);

  // Trigger pipeline whenever asset or persona changes
  useEffect(() => {
    triggerPipeline(agentAsset, agPersona);
  }, [agentAsset, agPersona, replayKey, triggerPipeline]);

  // Connect sensor WebSocket whenever asset changes
  useEffect(() => {
    setSensorData(null);
    connectSensorWS(agentAsset, setSensorData);
  }, [agentAsset]);

  function handleReplay() {
    setReplayKey(k => k + 1);
    setSelectedNode(0);
  }

  function handleAssetChange(val) {
    setAgentAsset(val);
    setSelectedNode(0);
  }

  function handlePersonaChange(val) {
    setAgPersona(val);
    setSelectedNode(0);
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', flex: 1, overflow: 'hidden' }}>
      {/* Header */}
      <div className="aghdr">
        <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
          <div className="agttl">Agent Pipeline</div>
          {agPipelineRunning && (
            <div style={{
              display: 'flex', alignItems: 'center', gap: '6px',
              fontSize: '11px', color: 'var(--ac2)', fontFamily: 'var(--m)',
            }}>
              <span style={{
                width: '8px', height: '8px', borderRadius: '50%',
                background: 'var(--ac2)', animation: 'pulse 1s infinite',
                display: 'inline-block',
              }} />
              RUNNING
            </div>
          )}
          {!agPipelineRunning && agPipelineResult && (
            <div style={{ fontSize: '10px', color: 'var(--gn)', fontFamily: 'var(--m)' }}>
              ✓ LIVE
            </div>
          )}
          {!agPipelineRunning && agPipelineError && (
            <div style={{ fontSize: '10px', color: 'var(--am)', fontFamily: 'var(--m)' }} title={agPipelineError}>
              ⚠ FALLBACK
            </div>
          )}
        </div>
        <div className="ag-sel-grp">
          <span className="ag-sel-lbl">Asset:</span>
          <select
            className="agsel"
            value={agentAsset}
            onChange={e => handleAssetChange(e.target.value)}
            disabled={agPipelineRunning}
          >
            {ASSET_OPTIONS.map(o => (
              <option key={o.val} value={o.val}>{o.lbl}</option>
            ))}
          </select>
          <span className="ag-sel-lbl" style={{ marginLeft: '8px' }}>View as:</span>
          <select
            className="agsel"
            value={agPersona}
            onChange={e => handlePersonaChange(e.target.value)}
            disabled={agPipelineRunning}
          >
            {PERSONA_OPTIONS.map(o => (
              <option key={o.val} value={o.val}>{o.lbl}</option>
            ))}
          </select>
          <button
            className="chip"
            onClick={handleReplay}
            style={{ marginLeft: '8px' }}
            disabled={agPipelineRunning}
          >
            {agPipelineRunning ? '⏳ Running…' : '▶ Replay'}
          </button>
        </div>
      </div>

      {/* Body */}
      <div className="agbody">
        <AgentPipeline
          asset={agentAsset}
          selectedNode={selectedNode}
          onNodeSelect={setSelectedNode}
          replayKey={replayKey}
          pipelineResult={agPipelineResult}
          pipelineRunning={agPipelineRunning}
        />
        <AgentDetail
          asset={agentAsset}
          persona={agPersona}
          selectedNode={selectedNode}
          currentTab={currentAgTab}
          onTabChange={setCurrentAgTab}
          replayKey={replayKey}
          pipelineResult={agPipelineResult}
          pipelineRunning={agPipelineRunning}
          sensorData={sensorData}
        />
      </div>
    </div>
  );
}
