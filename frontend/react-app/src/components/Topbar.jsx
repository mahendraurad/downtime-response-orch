import React, { useContext } from 'react';
import { AppContext } from '../context/AppContext';

export default function Topbar() {
  const {
    currentView, setCurrentView, agentAsset, setPipelineRunning, pipelineRunning,
    theme, setTheme,
  } = useContext(AppContext);

  const navItems = [
    { id: 'chat', label: 'Agent Chat' },
    { id: 'agents', label: 'Agent Graph' },
    { id: 'assets', label: 'Asset Registry' },
    { id: 'workorders', label: 'Work Orders' },
  ];

  async function handleLivePipeline() {
    if (pipelineRunning) return;
    setPipelineRunning(true);
    setCurrentView('chat');
    window.dispatchEvent(new CustomEvent('dro-sq', {
      detail: `Run the complete orchestrated analysis for ${agentAsset}`,
    }));
    setTimeout(() => setPipelineRunning(false), 5000);
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
          onClick={handleLivePipeline}
          disabled={pipelineRunning}
          style={{ background: 'rgba(79,142,255,.15)', borderColor: 'var(--ac)', color: 'var(--ac2)', fontSize: '10px' }}
          title="Run actual Python agents on the current asset scenario"
        >
          {pipelineRunning ? '⏳ Running…' : '⚡ Live Pipeline'}
        </button>
        <div className="ldot"></div>
        <div className="ltx">LIVE</div>
        <div className="abdg">⚠ 3 ALERTS</div>
      </div>
    </div>
  );
}
