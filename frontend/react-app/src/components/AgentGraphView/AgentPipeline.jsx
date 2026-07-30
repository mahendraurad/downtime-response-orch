import React, { useEffect, useState } from 'react';
import { AG_NODES } from '../../data/agentNodes';
import { ASSET_AG_STATE } from '../../data/assetAgState';

export default function AgentPipeline({ asset, selectedNode, onNodeSelect, replayKey }) {
  const [animatedNodes, setAnimatedNodes] = useState([]);
  const states = ASSET_AG_STATE[asset] || ASSET_AG_STATE['M-104'];

  // On mount or replay, animate nodes appearing one by one
  useEffect(() => {
    setAnimatedNodes([]);
    let i = 0;
    const interval = setInterval(() => {
      const captured = i;
      setAnimatedNodes(prev => prev.includes(captured) ? prev : [...prev, captured]);
      i++;
      if (i >= AG_NODES.length) clearInterval(interval);
    }, 220);
    return () => clearInterval(interval);
  }, [replayKey, asset]);

  function getNodeCls(state, idx) {
    // Original CSS classes: pnode, pnode.nd, pnode.nr, pnode.ni, pnode.sel
    const stateCls = state.s === 'nd' ? ' nd' : state.s === 'nr' ? ' nr' : ' ni';
    const selCls = selectedNode === idx ? ' sel' : '';
    return 'pnode' + stateCls + selCls;
  }

  function getStatusCls(state) {
    return state.s === 'nd' ? 'pnst std' : state.s === 'nr' ? 'pnst str2' : 'pnst sti';
  }

  function getStatusLabel(state) {
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
                <div className={getStatusCls(state)}>{getStatusLabel(state)}</div>
              </div>
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
