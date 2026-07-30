import React, { useState } from 'react';
import { ASSETS, FLEET_TOTAL } from '../../data/assets';

const STATUS_COLOR = { critical: 'var(--rd)', warning: 'var(--am)', healthy: 'var(--gn)' };
const STATUS_LABEL = { critical: 'CRITICAL', warning: 'WARNING', healthy: 'HEALTHY' };

export default function AssetList({ selectedAsset, onSelect }) {
  const [search, setSearch] = useState('');
  const [filter, setFilter] = useState('all');

  const filtered = ASSETS.filter(a => {
    const matchFilter = filter === 'all' || a.st === filter;
    const matchSearch = search === '' ||
      a.id.toLowerCase().includes(search.toLowerCase()) ||
      a.nm.toLowerCase().includes(search.toLowerCase()) ||
      a.tp.toLowerCase().includes(search.toLowerCase());
    return matchFilter && matchSearch;
  });

  return (
    <div className="asl">
      <div className="aslhdr">
        <span style={{ fontSize: '12.5px', fontWeight: 600 }}>Asset Registry</span>
        <span style={{ fontSize: '10px', color: 'var(--t3)', fontFamily: 'var(--m)' }}>{FLEET_TOTAL} assets</span>
      </div>
      <div className="asrch">
        <input
          type="text"
          placeholder="Search assets…"
          value={search}
          onChange={e => setSearch(e.target.value)}
        />
      </div>
      <div className="asf">
        {['all', 'critical', 'warning', 'healthy'].map(f => (
          <button
            key={f}
            className={`asfb${filter === f ? ' on' : ''}`}
            onClick={() => setFilter(f)}
          >
            {f.charAt(0).toUpperCase() + f.slice(1)}
          </button>
        ))}
      </div>
      <div className="alist">
        {filtered.map(a => {
          const c = STATUS_COLOR[a.st];
          return (
            <div
              key={a.id}
              className={`ait${selectedAsset && selectedAsset.id === a.id ? ' on' : ''}`}
              onClick={() => onSelect(a)}
            >
              <div className="aih" style={{ background: c }}></div>
              <div className="aii">
                <div className="aiid">{a.id}</div>
                <div className="ainm">{a.nm}</div>
              </div>
              <div className="airl" style={{ color: c }}>{a.rul}</div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
