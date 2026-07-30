import React, { useContext, useState } from 'react';
import { AppContext } from '../context/AppContext';
import { SAMPLE_REC } from '../data/scenarios';

export default function Topbar() {
  const { currentView, setCurrentView, agentAsset, setPipelineRunning, pipelineRunning, theme, setTheme } = useContext(AppContext);
  const [execRunning, setExecRunning] = useState(false);

  const navItems = [
    { id: 'chat', label: 'Agent Chat' },
    { id: 'agents', label: 'Agent Graph' },
    { id: 'assets', label: 'Asset Registry' },
    { id: 'workorders', label: 'Work Orders' },
  ];

  function handleAlertsClick() {
    setCurrentView('chat');
    // Fire a custom event to send a query to chat
    window.dispatchEvent(new CustomEvent('dro-sq', {
      detail: 'List all 3 active alerts with severity, RUL and recommended action for each'
    }));
  }

  async function handleLivePipeline() {
    if (pipelineRunning) return;
    setPipelineRunning(true);
    setCurrentView('chat');
    window.dispatchEvent(new CustomEvent('dro-sq', {
      detail: `Run the complete orchestrated analysis for ${agentAsset}`,
    }));
    // Reset after a delay
    setTimeout(() => setPipelineRunning(false), 5000);
  }

  function handleTestExecutor() {
    setCurrentView('chat');
    window.dispatchEvent(new CustomEvent('dro-executor-hitl', { detail: SAMPLE_REC }));
  }

  return (
    <div className="tb">
      <div className="tb-l">
        <div className="logo">DRO</div>
        <div>
          <div className="pn">Downtime Response Orchestrator</div>
          <div className="ps">Bearing Failure · Predictive &amp; Prescriptive · Automotive &amp; Industrial AI</div>
        </div>
      </div>
      <div className="tb-c">
        {navItems.map(item => (
          <button
            key={item.id}
            className={`nt${currentView === item.id ? ' on' : ''}`}
            onClick={() => setCurrentView(item.id)}
          >
            {item.label}
          </button>
        ))}
      </div>
      <div className="tb-r">
        <button
          onClick={() => setTheme(theme === 'light' ? 'dark' : 'light')}
          title={theme === 'light' ? 'Switch to dark theme' : 'Switch to light theme'}
          style={{
            width: '28px', height: '28px', borderRadius: '6px', border: '1px solid var(--b2)',
            background: 'var(--sf)', color: 'var(--t2)', cursor: 'pointer',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            fontSize: '14px', flexShrink: 0, transition: 'all .15s',
          }}
        >
          {theme === 'light' ? '🌙' : '☀️'}
        </button>
        <button
          className="chip"
          onClick={handleTestExecutor}
          style={{ background: 'rgba(16,185,129,.12)', borderColor: '#10b981', color: '#10b981', fontSize: '10px' }}
          title="Phase 9 — simulate executor approval HITL with sample recommendation"
        >
          ⚙ Test Executor
        </button>
        <button
          className="chip"
          onClick={handleLivePipeline}
          disabled={pipelineRunning}
          style={{ background: 'rgba(79,142,255,.15)', borderColor: 'var(--ac)', color: 'var(--ac2)', fontSize: '10px' }}
          title="Run actual Python agents on the current asset scenario"
        >
          {pipelineRunning ? '⏳ Running…' : '⚡ Live Pipeline'}
        </button>
        <div className="ldot"></div>
        <div className="ltx">LIVE</div>
        <div className="abdg" onClick={handleAlertsClick}>⚠ 3 ALERTS</div>
      </div>
    </div>
  );
}
