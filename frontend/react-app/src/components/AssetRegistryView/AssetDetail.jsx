import React, { useState, useContext, useRef } from 'react';
import { AppContext } from '../../context/AppContext';

const STATUS_COLOR = { critical: 'var(--rd)', warning: 'var(--am)', healthy: 'var(--gn)' };
const STATUS_LABEL = { critical: 'CRITICAL', warning: 'WARNING', healthy: 'HEALTHY' };

function buildStaticSummary(asset) {
  const vibLevel = asset.vib > 10 ? 'critically high' : asset.vib > 4 ? 'elevated' : 'normal';
  const vibExplain = asset.vib > 10
    ? `At ${asset.vib} mm/s this is well above safe operating limits (ISO Zone D). This level of vibration indicates severe bearing damage — the machine is shaking significantly more than it should be.`
    : asset.vib > 4
    ? `At ${asset.vib} mm/s this is above the normal range. Think of it like a car that is starting to shake — it still runs but something is wearing down and needs attention.`
    : `At ${asset.vib} mm/s this is within safe limits. The machine is running smoothly with no abnormal shaking.`;

  const tmpExplain = asset.tmp > 80
    ? `At ${asset.tmp}°C this is critically high. High temperature usually means increased friction — the bearing is rubbing harder than it should, often because it is damaged or poorly lubricated.`
    : asset.tmp > 60
    ? `At ${asset.tmp}°C this is slightly warm. A little extra heat is normal under load, but sustained warmth can accelerate wear over time.`
    : `At ${asset.tmp}°C this is normal operating temperature. No thermal concerns.`;

  const bpfoExplain = asset.bpfo > 3
    ? `The BPFO ratio of ${asset.bpfo}× confirms an outer race fault. In simple terms: the ball bearings are repeatedly hitting a damaged spot on the outer ring as they spin. This is a specific, diagnosable fault — not just general wear.`
    : asset.bpfo > 1
    ? `The BPFO ratio of ${asset.bpfo}× is elevated. This is an early warning sign that the bearing may be developing a fault on its outer ring. It is not critical yet, but worth monitoring closely.`
    : `The BPFO ratio of ${asset.bpfo}× is normal. There is no detectable bearing fault signature at this time.`;

  const rulExplain = asset.st === 'critical'
    ? `The system estimates this asset has about ${asset.rul} of useful life remaining before failure. This is the window to act — waiting beyond it risks an unexpected breakdown, which is typically 10–30× more expensive than a planned repair.`
    : asset.st === 'warning'
    ? `The system estimates ${asset.rul} of remaining useful life. You have time to plan, but scheduling maintenance soon is advisable to stay within the safe window.`
    : `The estimated remaining useful life is ${asset.rul}. The asset is healthy and no maintenance is needed in the near term.`;

  const actionExplain = asset.st === 'critical'
    ? 'Raise a work order immediately. Every day of delay increases the risk of unplanned failure and secondary damage to connected equipment.'
    : asset.st === 'warning'
    ? 'Plan a maintenance inspection within the estimated RUL window. Check lubrication and schedule a bearing inspection.'
    : 'Continue routine monitoring. The next scheduled maintenance is sufficient.';

  return [
    `<strong></strong>This page displays the real-time health status of <strong>${asset.nm}</strong> (${asset.id}), a ${asset.tp.toLowerCase()} on ${asset.ln}. The readings below come from vibration sensors and temperature probes mounted on the machine.`,
    `<strong>Vibration RMS — ${vibLevel} (${asset.vib} mm/s)</strong><br>${vibExplain}`,
    `<strong>Temperature — ${asset.tmp}°C</strong><br>${tmpExplain}`,
    `<strong>BPFO Ratio — ${asset.bpfo}×</strong><br>${bpfoExplain}`,
    `<strong>Remaining Useful Life (RUL) — ${asset.rul}</strong><br>${rulExplain}`,
    `<strong>What should you do?</strong><br>${actionExplain}`,
  ].join('<br>');
}

export default function AssetDetail({ asset }) {
  const { persona, setCurrentView } = useContext(AppContext);
  const [expanded, setExpanded] = useState(false);

  // Collapse when switching assets
  const prevAssetRef = useRef(null);
  if (asset?.id !== prevAssetRef.current) {
    prevAssetRef.current = asset?.id || null;
    if (expanded) setExpanded(false);
  }

  function handleToggle() {
    setExpanded(v => !v);
  }

  if (!asset) return <div className="adet"><div style={{ padding: '24px', color: 'var(--t3)' }}>Select an asset</div></div>;

  const stColor = STATUS_COLOR[asset.st];
  const stLabel = STATUS_LABEL[asset.st];

  const trend = asset.trend || [];
  const maxT = Math.max(...trend, 1);
  const minT = Math.min(...trend, 0);
  const range = maxT - minT || 1;
  const w = 320; const h = 60;
  const pts = trend.map((v, i) => {
    const x = (i / (trend.length - 1)) * w;
    const y = h - ((v - minT) / range) * (h - 4) - 2;
    return `${x},${y}`;
  }).join(' ');

  return (
    <div className="adet">
      <div className="adethdr">
        <div className="adetid">{asset.id}</div>
        <div className="adetnm">{asset.nm}</div>
        <div className="adetmt">{asset.br} · {asset.kw} kW · {asset.rpm} RPM · {asset.ln}</div>
        <div className="adetbdg">
          <span style={{ fontSize: '10px', fontWeight: 700, color: stColor, background: `${stColor}20`, padding: '3px 10px', borderRadius: '4px', border: `1px solid ${stColor}50`, fontFamily: 'var(--m)' }}>
            {stLabel}
          </span>
          <span style={{ fontSize: '10px', color: 'var(--t3)', marginLeft: '8px', fontFamily: 'var(--m)' }}>
            RUL: <strong style={{ color: stColor }}>{asset.rul}</strong>
          </span>
          <span style={{ fontSize: '10px', color: 'var(--t3)', marginLeft: '8px', fontFamily: 'var(--m)' }}>
            {asset.iso}
          </span>
        </div>
      </div>

      <div className="adetbody">

        {/* ── Collapsible AI Summary ── */}
        <div style={{ marginBottom: '14px', borderRadius: '8px', border: '1px solid var(--b)', overflow: 'auto', 'overflow-y':'auto' }}>
          {/* Toggle header */}
          <button
            onClick={handleToggle}
            style={{
              width: '100%', display: 'flex', alignItems: 'center', gap: '8px',
              padding: '10px 14px', background: expanded ? 'var(--sf)' : 'var(--bg)',
              border: 'none', cursor: 'pointer', textAlign: 'left',
              borderBottom: expanded ? '1px solid var(--b)' : 'none',
              transition: 'background 0.15s',
            }}
          >
            <span style={{ fontSize: '13px' }}>🤖</span>
            <span style={{ flex: 1, fontSize: '12px', fontWeight: 600, color: 'var(--t)' }}>
              Pointers on how to read this page
            </span>
            <span style={{ fontSize: '11px', color: 'var(--t3)', fontFamily: 'var(--m)', flexShrink: 0 }}>
              {expanded ? '▲' : '▼'}
            </span>
          </button>

          {/* Expandable body */}
          {expanded && (
            <div style={{ padding: '14px 16px', background: 'var(--sf)' }}>
              <div
                style={{ fontSize: '12px', color: 'var(--t2)', lineHeight: '1.8' }}
                dangerouslySetInnerHTML={{ __html: buildStaticSummary(asset) }}
              />
              <div style={{ marginTop: '12px', paddingTop: '10px', borderTop: '1px solid var(--b)', display: 'flex', justifyContent: 'flex-end' }}>
                <button
                  onClick={() => setCurrentView('chat')}
                  style={{
                    fontSize: '11px', color: 'var(--ac2)', background: 'none',
                    border: 'none', cursor: 'pointer', fontFamily: 'inherit',
                    padding: 0, textDecoration: 'underline',
                  }}
                >
                  Ask Agent Chat for more details →
                </button>
              </div>
            </div>
          )}
        </div>

        {/* KPI grid */}
        <div className="dg3" style={{ marginBottom: '14px' }}>
          <div className="dc">
            <div className="dcl">Vibration RMS</div>
            <div className="dcv" style={{ color: asset.vib > 10 ? 'var(--rd)' : asset.vib > 4 ? 'var(--am)' : 'var(--gn)' }}>
              {asset.vib}<span style={{ fontSize: '10px', color: 'var(--t3)' }}> mm/s</span>
            </div>
            <div className="dcs">{asset.vib > 8 ? '⚠ ISO Zone D' : asset.vib > 3 ? 'Zone C' : 'Zone A/B'}</div>
          </div>
          <div className="dc">
            <div className="dcl">Temperature</div>
            <div className="dcv" style={{ color: asset.tmp > 80 ? 'var(--rd)' : asset.tmp > 60 ? 'var(--am)' : 'var(--gn)' }}>
              {asset.tmp}<span style={{ fontSize: '10px', color: 'var(--t3)' }}> °C</span>
            </div>
            <div className="dcs">{asset.tmp > 80 ? '⚠ Elevated' : asset.tmp > 60 ? 'Warm' : 'Normal'}</div>
          </div>
          <div className="dc">
            <div className="dcl">BPFO Ratio</div>
            <div className="dcv" style={{ color: asset.bpfo > 3 ? 'var(--rd)' : asset.bpfo > 1 ? 'var(--am)' : 'var(--gn)' }}>
              {asset.bpfo}<span style={{ fontSize: '10px', color: 'var(--t3)' }}>×</span>
            </div>
            <div className="dcs">{asset.bpfo > 3 ? 'Fault confirmed' : asset.bpfo > 1 ? 'Elevated' : 'Normal'}</div>
          </div>
        </div>

        {/* Trend chart */}
        <div className="dsec" style={{ marginBottom: '14px' }}>
          <div className="sttl">RUL Trend</div>
          <svg style={{ width: '100%', height: '60px', display: 'block' }} viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none">
            <defs>
              <linearGradient id="trendGrad" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor={stColor} stopOpacity="0.3" />
                <stop offset="100%" stopColor={stColor} stopOpacity="0.02" />
              </linearGradient>
            </defs>
            <polyline points={pts + ` ${w},${h} 0,${h}`} fill="url(#trendGrad)" stroke="none" />
            <polyline points={pts} fill="none" stroke={stColor} strokeWidth="1.5" opacity="0.85" />
          </svg>
          <div style={{ fontSize: '8.5px', color: 'var(--t3)', fontFamily: 'var(--m)', marginTop: '2px' }}>
            Vibration trend · last 12 readings
          </div>
        </div>

        {/* Asset details */}
        <div className="dsec" style={{ marginBottom: '14px' }}>
          <div className="sttl">Asset Details</div>
          <div className="dg2">
            {[
              ['Type', asset.tp],
              ['Bearing Model', asset.br],
              ['Power', asset.kw + ' kW'],
              ['Speed', asset.rpm + ' RPM'],
              ['Line', asset.ln],
              ['Last Replaced', asset.last],
              ['ISO Zone', asset.iso],
            ].map(([l, v]) => (
              <div key={l} className="dc">
                <div className="dcl">{l}</div>
                <div className="dcv" style={{ fontSize: '13px' }}>{v}</div>
              </div>
            ))}
          </div>
        </div>

        {/* Fault history */}
        <div className="dsec">
          <div className="sttl">Fault History</div>
          {asset.faults.map((f, i) => (
            <div key={i} style={{
              padding: '7px 10px', marginBottom: '6px',
              background: 'var(--sf)', borderRadius: '6px',
              border: '1px solid var(--b)', borderLeft: `3px solid ${f.sc}`,
              display: 'flex', alignItems: 'center', gap: '10px',
            }}>
              <span style={{ fontFamily: 'var(--m)', fontSize: '10px', color: 'var(--t3)' }}>{f.dt}</span>
              <span style={{ fontSize: '12px', color: 'var(--t)', fontWeight: 600 }}>{f.ty}</span>
              <span style={{ marginLeft: 'auto', fontSize: '10px', color: f.sc }}>{f.st}</span>
            </div>
          ))}
        </div>

      </div>
    </div>
  );
}
