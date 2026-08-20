import React, { useContext } from 'react';
import { AppContext } from '../context/AppContext';

export default function Topbar() {
  const {
    currentView, setCurrentView,
    theme, setTheme,
    assets, assetsLoaded, dataStatus,
  } = useContext(AppContext);

  const navItems = [
    { id: 'chat', label: 'Agent Chat' },
    { id: 'agents', label: 'Agent Graph' },
    { id: 'assets', label: 'Asset Registry' },
    { id: 'workorders', label: 'Work Orders' },
  ];

  const criticalAssets = assets.filter(a => a.st === 'critical');
  const warningAssets = assets.filter(a => a.st === 'warning');
  const healthyAssets = assets.filter(a => a.st === 'healthy');
  const alertCount = criticalAssets.length + warningAssets.length;

  function handleAssetClick(assetId) {
    setCurrentView('chat');
    window.dispatchEvent(new CustomEvent('dro-sq', {
      detail: `Analyse asset ${assetId} completely`,
    }));
  }

  return (
    <>
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
          <div className="ldot"></div>
          {assetsLoaded && (
            <div className="ltx" style={{ color: dataStatus.assets !== 'fallback' ? 'var(--gn)' : 'var(--t3)' }}>
              {dataStatus.assets !== 'fallback' ? 'BACKEND LIVE' : 'BACKEND OFFLINE'}
            </div>
          )}
          {!assetsLoaded && (
            <div className="ltx" style={{ color: 'var(--t3)' }}>Connecting…</div>
          )}
          {alertCount > 0 && (
            <div className="abdg">⚠ {alertCount} ALERTS</div>
          )}
        </div>
      </div>

      {/* Live Asset Health Ribbon */}
      <div className="ahr">
        <span className="ahr-label">FLEET HEALTH</span>
        <div className="ahr-divider" />
        <div className="ahr-pills">
          {assetsLoaded && assets.length === 0 && (
            <div className="ahr-pill" style={{ color: 'var(--t3)', fontSize: '11px' }}>
              No assets available — check backend connection
            </div>
          )}
          {criticalAssets.map(a => (
            <button
              key={a.id}
              className="ahr-pill ahr-crit"
              onClick={() => handleAssetClick(a.id)}
              title={`${a.nm} — Click to analyse`}
            >
              <span className="ahr-dot ahr-dot-crit" />
              <span className="ahr-pid">{a.id}</span>
              <span className="ahr-status">CRITICAL</span>
              <span className="ahr-rul">RUL {a.rul}</span>
              <span className="ahr-vib">{a.vib} mm/s</span>
            </button>
          ))}
          {warningAssets.map(a => (
            <button
              key={a.id}
              className="ahr-pill ahr-warn"
              onClick={() => handleAssetClick(a.id)}
              title={`${a.nm} — Click to analyse`}
            >
              <span className="ahr-dot ahr-dot-warn" />
              <span className="ahr-pid">{a.id}</span>
              <span className="ahr-status">WARNING</span>
              <span className="ahr-rul">RUL {a.rul}</span>
              <span className="ahr-vib">{a.vib} mm/s</span>
            </button>
          ))}
          {healthyAssets.length > 0 && (
            <div className="ahr-pill ahr-ok">
              <span className="ahr-dot ahr-dot-ok" />
              <span className="ahr-pid">{healthyAssets.length} healthy</span>
            </div>
          )}
        </div>
        <div style={{ marginLeft: 'auto', flexShrink: 0, display: 'flex', alignItems: 'center', gap: '5px' }}>
          <span className="ahr-ts-dot" />
          <span className="ahr-ts">
            {!assetsLoaded ? 'Connecting to backend…' : dataStatus.assets !== 'fallback' ? 'Live from agent pipeline' : 'Backend offline — awaiting reconnect'}
          </span>
        </div>
      </div>
    </>
  );
}
