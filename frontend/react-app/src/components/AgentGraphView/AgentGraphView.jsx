import React, { useContext, useState, useEffect, useCallback } from 'react';
import { AppContext } from '../../context/AppContext';
import AgentPipeline from './AgentPipeline';
import AgentDetail from './AgentDetail';

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
        <div className="agttl">Agent Pipeline</div>
        <div className="ag-sel-grp">
          <span className="ag-sel-lbl">Asset:</span>
          <select
            className="agsel"
            value={agentAsset}
            onChange={e => handleAssetChange(e.target.value)}
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
          >
            {PERSONA_OPTIONS.map(o => (
              <option key={o.val} value={o.val}>{o.lbl}</option>
            ))}
          </select>
          <button
            className="chip"
            onClick={handleReplay}
            style={{ marginLeft: '8px' }}
          >
            ▶ Replay
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
        />
        <AgentDetail
          asset={agentAsset}
          persona={agPersona}
          selectedNode={selectedNode}
          currentTab={currentAgTab}
          onTabChange={setCurrentAgTab}
          replayKey={replayKey}
        />
      </div>
    </div>
  );
}
