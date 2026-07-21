import React from 'react';

const STATUS_COLOR = { critical: 'var(--rd)', warning: 'var(--am)', healthy: 'var(--gn)' };
const STATUS_LABEL = { critical: 'CRITICAL', warning: 'WARNING', healthy: 'HEALTHY' };

export default function AssetDetail({ asset }) {
  if (!asset) return <div className="adet"><div style={{ padding: '24px', color: 'var(--t3)' }}>Select an asset</div></div>;

  const stColor = STATUS_COLOR[asset.st];
  const stLabel = STATUS_LABEL[asset.st];

  // Build SVG trend chart from asset.trend
  const trend = asset.trend || [];
  const maxT = Math.max(...trend, 1);
  const minT = Math.min(...trend, 0);
  const range = maxT - minT || 1;
  const w = 320;
  const h = 60;
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
          <svg
            style={{ width: '100%', height: '60px', display: 'block' }}
            viewBox={`0 0 ${w} ${h}`}
            preserveAspectRatio="none"
          >
            <defs>
              <linearGradient id="trendGrad" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor={stColor} stopOpacity="0.3" />
                <stop offset="100%" stopColor={stColor} stopOpacity="0.02" />
              </linearGradient>
            </defs>
            <polyline
              points={pts + ` ${w},${h} 0,${h}`}
              fill="url(#trendGrad)"
              stroke="none"
            />
            <polyline
              points={pts}
              fill="none"
              stroke={stColor}
              strokeWidth="1.5"
              opacity="0.85"
            />
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
              border: '1px solid var(--b)',
              borderLeft: `3px solid ${f.sc}`,
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
