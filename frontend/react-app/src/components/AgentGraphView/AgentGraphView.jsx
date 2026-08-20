import React, { useContext, useState, useEffect, useRef, useCallback } from 'react';
import { AppContext } from '../../context/AppContext';
import AgentPipeline from './AgentPipeline';
import AgentDetail from './AgentDetail';
import { ASSET_SCENARIO } from '../../data/scenarios';
import { runRealPipeline } from '../../api/pipeline';
import { connectSensorWS } from '../../api/sensors';


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
    persona, setPersona,
    selectedNode, setSelectedNode,
    currentAgTab, setCurrentAgTab,
    assets,
    assetPipelineState,
    updateAssetSensorData,
  } = useContext(AppContext);

  const assetOptions = (assets || []).map(a => ({
    val: a.id,
    lbl: `${a.id} — ${a.nm} (${(a.st || 'unknown').toUpperCase()})`,
  }));

  // Backend uses asset_id (e.g. 'AST_MTR_001'); frontend uses id (e.g. 'M-104').
  // Try backend key first, fall back to frontend id.
  const selectedAssetData = assets.find(a => a.id === agentAsset);
  const backendAssetId = selectedAssetData?.asset_id;
  const storedState = (backendAssetId && assetPipelineState[backendAssetId])
    || assetPipelineState[agentAsset]
    || null;

  // Once assets load from backend, default to the first available asset if current selection is invalid
  useEffect(() => {
    if (assets.length > 0 && !assets.find(a => a.id === agentAsset)) {
      setAgentAsset(assets[0].id);
    }
  }, [assets, agentAsset, setAgentAsset]);

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

  // Trigger pipeline whenever asset or persona changes (skip if no assets loaded yet)
  useEffect(() => {
    if (!agentAsset) return;
    triggerPipeline(agentAsset, persona);
  }, [agentAsset, persona, replayKey, triggerPipeline]);

  // Connect sensor WebSocket whenever asset changes; reset EWMA ref on asset switch
  const sensorSmoothedRef = useRef(null);
  useEffect(() => {
    setSensorData(null);
    sensorSmoothedRef.current = null;
    if (agentAsset) connectSensorWS(agentAsset, setSensorData);
  }, [agentAsset]);

  // Pipe smoothed sensor readings back into the global assets array so the fleet
  // ribbon and asset registry stay current. Throttled to once per 5 s to prevent
  // visible flickering in the overview tiles.
  const lastGlobalUpdateRef = useRef(0);
  useEffect(() => {
    if (!sensorData || !agentAsset) return;
    const ALPHA = 0.25;
    const prev = sensorSmoothedRef.current;
    const smooth = (key) =>
      prev ? ALPHA * (sensorData[key] ?? prev[key]) + (1 - ALPHA) * (prev[key] ?? sensorData[key])
           : sensorData[key];
    const smoothed = {
      vib_rms_mms: smooth('vib_rms_mms'),
      temp_c:      smooth('temp_c'),
      bpfo_ratio:  smooth('bpfo_ratio'),
    };
    sensorSmoothedRef.current = smoothed;
    const now = Date.now();
    if (now - lastGlobalUpdateRef.current >= 5000) {
      lastGlobalUpdateRef.current = now;
      updateAssetSensorData(agentAsset, smoothed);
    }
  }, [sensorData, agentAsset, updateAssetSensorData]);

  function handleReplay() {
    setReplayKey(k => k + 1);
    setSelectedNode(0);
  }

  function handleAssetChange(val) {
    setAgentAsset(val);
    setSelectedNode(0);
  }

  function handlePersonaChange(val) {
    setPersona(val);
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
            {assetOptions.length === 0 && <option disabled>Loading assets…</option>}
            {assetOptions.map(o => (
              <option key={o.val} value={o.val}>{o.lbl}</option>
            ))}
          </select>
          <span className="ag-sel-lbl" style={{ marginLeft: '8px' }}>View as:</span>
          <select
            className="agsel"
            value={persona}
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
          storedState={storedState}
        />
        <AgentDetail
          asset={agentAsset}
          persona={persona}
          selectedNode={selectedNode}
          currentTab={currentAgTab}
          onTabChange={setCurrentAgTab}
          replayKey={replayKey}
          pipelineResult={agPipelineResult}
          pipelineRunning={agPipelineRunning}
          sensorData={sensorData}
          storedState={storedState}
        />
      </div>
    </div>
  );
}
