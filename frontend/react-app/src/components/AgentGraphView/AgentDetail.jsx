import React from 'react';
import { AG_NODES } from '../../data/agentNodes';
import { PERSONA_TASKS } from '../../data/personaTasks';
import { ASSET_AG_STATE } from '../../data/assetAgState';

const TABS = [
  { id: 'overview', lbl: 'Overview' },
  { id: 'node', lbl: 'Node Detail' },
  { id: 'tasks', lbl: 'Persona Tasks' },
  { id: 'actions', lbl: 'Actions' },
];

export default function AgentDetail({ asset, persona, selectedNode, currentTab, onTabChange }) {
  const node = AG_NODES[selectedNode] || AG_NODES[0];
  const states = ASSET_AG_STATE[asset] || ASSET_AG_STATE['M-104'];
  const state = states[selectedNode] || { s: 'ni' };

  return (
    <div className="ag-right">
      <div className="ag-tabs">
        {TABS.map(t => (
          <div
            key={t.id}
            className={`agtab${currentTab === t.id ? ' on' : ''}`}
            onClick={() => onTabChange(t.id)}
          >
            {t.lbl}
          </div>
        ))}
      </div>
      <div className="ag-tab-body">
        {currentTab === 'overview' && (
          <OverviewPanel asset={asset} persona={persona} states={states} selectedNode={selectedNode} />
        )}
        {currentTab === 'node' && (
          <NodeDetailPanel node={node} state={state} asset={asset} persona={persona} />
        )}
        {currentTab === 'tasks' && (
          <PersonaTasksPanel persona={persona} asset={asset} selectedNode={selectedNode} />
        )}
        {currentTab === 'actions' && (
          <ActionsPanel asset={asset} persona={persona} selectedNode={selectedNode} />
        )}
      </div>
    </div>
  );
}

function OverviewPanel({ asset, persona, states, selectedNode }) {
  const stateLabel = { nd: 'Complete', nr: 'Running', ni: 'Standby' };
  const stateColor = { nd: 'var(--gn)', nr: 'var(--am)', ni: 'var(--t3)' };

  return (
    <div className="ag-tab-panel on" style={{ padding: '16px', overflow: 'auto' }}>
      <div className="sttl" style={{ marginBottom: '12px' }}>Pipeline Overview &middot; {asset}</div>
      {AG_NODES.map((n, i) => {
        const s = states[i] || { s: 'ni' };
        const out = n.assetOut && n.assetOut[asset] && n.assetOut[asset][persona]
          ? n.assetOut[asset][persona]
          : null;
        return (
          <div
            key={i}
            style={{
              padding: '10px 12px',
              marginBottom: '8px',
              borderRadius: '8px',
              border: `1px solid ${selectedNode === i ? 'var(--ac)' : 'var(--b)'}`,
              background: selectedNode === i ? 'rgba(79,142,255,0.07)' : 'var(--sf)',
              cursor: 'pointer',
              transition: 'all 0.2s',
            }}
          >
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: out ? '6px' : 0 }}>
              <span style={{ fontSize: '16px' }}>{n.ico}</span>
              <span style={{ fontWeight: 600, fontSize: '12px', color: 'var(--t)', flex: 1 }}>{n.nm}</span>
              <span style={{
                fontSize: '9px', fontWeight: 700, color: stateColor[s.s],
                fontFamily: 'var(--m)', padding: '2px 6px', borderRadius: '4px',
                background: `${stateColor[s.s]}20`,
              }}>
                {stateLabel[s.s]}
              </span>
            </div>
            {out && (
              <div style={{ fontSize: '11px', color: 'var(--t2)', lineHeight: '1.5', paddingLeft: '24px' }}>
                {out}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

function NodeDetailPanel({ node, state, asset, persona }) {
  const stateColor = { nd: 'var(--gn)', nr: 'var(--am)', ni: 'var(--t3)' };
  const stateLabel = { nd: 'Complete', nr: 'Running', ni: 'Standby' };
  const out = node.assetOut && node.assetOut[asset] && node.assetOut[asset][persona]
    ? node.assetOut[asset][persona]
    : null;

  return (
    <div className="ag-tab-panel on" style={{ padding: '16px', overflow: 'auto' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: '10px', marginBottom: '14px' }}>
        <span style={{ fontSize: '22px' }}>{node.ico}</span>
        <div>
          <div style={{ fontWeight: 700, fontSize: '14px', color: 'var(--t)' }}>{node.nm}</div>
          <div style={{ display: 'flex', alignItems: 'center', gap: '6px', marginTop: '2px' }}>
            <span style={{
              fontSize: '9px', fontWeight: 700, color: stateColor[state.s],
              fontFamily: 'var(--m)', padding: '2px 7px', borderRadius: '4px',
              background: `${stateColor[state.s]}20`,
            }}>
              {stateLabel[state.s]}
            </span>
          </div>
        </div>
      </div>

      <div style={{ fontSize: '12px', color: 'var(--t2)', lineHeight: '1.6', marginBottom: '16px', padding: '10px', background: 'var(--sf)', borderRadius: '6px' }}>
        {node.role}
      </div>

      <div className="sttl" style={{ marginBottom: '8px' }}>KPIs</div>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '6px', marginBottom: '16px' }}>
        {Object.entries(node.kpis).map(([k, v]) => (
          <div key={k} className="dc">
            <div className="dcl">{k}</div>
            <div className="dcv" style={{ fontSize: '12px' }}>{v}</div>
          </div>
        ))}
      </div>

      {out && (
        <>
          <div className="sttl" style={{ marginBottom: '8px' }}>Output for {asset} &middot; {persona}</div>
          <div style={{ fontSize: '12px', color: 'var(--t2)', lineHeight: '1.6', padding: '10px 12px', background: 'var(--sf)', borderRadius: '6px', border: '1px solid var(--b)', borderLeft: '3px solid var(--ac)' }}>
            {out}
          </div>
        </>
      )}
    </div>
  );
}

function PersonaTasksPanel({ persona, asset, selectedNode }) {
  const tasks = (PERSONA_TASKS[persona] && PERSONA_TASKS[persona][asset]) || [];

  return (
    <div className="ag-tab-panel on" style={{ padding: '16px', overflow: 'auto' }}>
      <div className="sttl" style={{ marginBottom: '4px' }}>
        {persona.charAt(0).toUpperCase() + persona.slice(1)} Tasks &middot; {asset}
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

function ActionsPanel({ asset, persona, selectedNode }) {
  const node = AG_NODES[selectedNode] || AG_NODES[0];
  const out = node.assetOut && node.assetOut[asset] && node.assetOut[asset][persona]
    ? node.assetOut[asset][persona]
    : null;

  const allAssets = Object.keys(node.assetOut || {});

  return (
    <div className="ag-tab-panel on" style={{ padding: '16px', overflow: 'auto' }}>
      <div className="sttl" style={{ marginBottom: '12px' }}>Agent Actions &middot; {node.nm}</div>

      <div style={{ marginBottom: '16px' }}>
        <div style={{ fontSize: '11px', fontWeight: 600, color: 'var(--t3)', fontFamily: 'var(--m)', marginBottom: '6px' }}>ROLE</div>
        <div style={{ fontSize: '12px', color: 'var(--t2)', lineHeight: '1.6', padding: '10px 12px', background: 'var(--sf)', borderRadius: '6px', border: '1px solid var(--b)' }}>
          {node.role}
        </div>
      </div>

      {out && (
        <div style={{ marginBottom: '16px' }}>
          <div style={{ fontSize: '11px', fontWeight: 600, color: 'var(--t3)', fontFamily: 'var(--m)', marginBottom: '6px' }}>
            OUTPUT FOR {asset.toUpperCase()} &middot; {persona.toUpperCase()}
          </div>
          <div style={{ fontSize: '12px', color: 'var(--t2)', lineHeight: '1.6', padding: '10px 12px', background: 'var(--sf)', borderRadius: '6px', border: '1px solid var(--b)', borderLeft: '3px solid var(--ac)' }}>
            {out}
          </div>
        </div>
      )}

      <div>
        <div style={{ fontSize: '11px', fontWeight: 600, color: 'var(--t3)', fontFamily: 'var(--m)', marginBottom: '6px' }}>
          ALL ASSET OUTPUTS
        </div>
        {allAssets.map(aId => {
          const personas = Object.keys(node.assetOut[aId] || {});
          return (
            <div key={aId} style={{ marginBottom: '10px', padding: '8px 10px', background: 'var(--sf)', borderRadius: '6px', border: '1px solid var(--b)' }}>
              <div style={{ fontWeight: 600, fontSize: '11px', color: 'var(--ac2)', marginBottom: '4px', fontFamily: 'var(--m)' }}>
                {aId}
              </div>
              {personas.map(pid => (
                <div key={pid} style={{ fontSize: '11px', color: 'var(--t2)', lineHeight: '1.5', marginBottom: '3px' }}>
                  <span style={{ color: 'var(--t3)', fontFamily: 'var(--m)', fontSize: '9px' }}>
                    {pid.toUpperCase()}:{' '}
                  </span>
                  {node.assetOut[aId][pid]}
                </div>
              ))}
            </div>
          );
        })}
      </div>
    </div>
  );
}
