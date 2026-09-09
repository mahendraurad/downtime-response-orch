import React, { useState } from 'react';

const PR_COLOR = { URGENT: 'var(--rd)', MEDIUM: 'var(--am)', LOW: 'var(--gn)' };
const PR_BG = { URGENT: 'var(--rdm)', MEDIUM: 'var(--adm)', LOW: 'var(--gd)' };

// Personas who have real action buttons (approve / progress / complete) at each WO status.
// Derived from WO_PERSONA_ACTS in WorkOrderDetail — must stay in sync if that map changes.
const WO_ASSIGNEES = {
  'Pending':     'Plant Supervisor / Plant Manager / VP Operations',
  'Scheduled':   'Plant Supervisor',
  'In Progress': 'Plant Supervisor / Maintenance Planner',
  'Rejected':    'None (Rejected)',
};

export default function WorkOrderList({ workOrders = [], selectedWO, onSelect }) {
  const [filter, setFilter] = useState('all');

  const filtered = workOrders.filter(wo => {
    if (filter === 'all') return true;
    return wo.st.toLowerCase() === filter;
  });

  return (
    <div className="wol">
      <div className="wolhdr">
        <span className="woltt">Work Orders</span>
      </div>
      <div className="woflt">
        {[
          { id: 'all',         lbl: `All (${workOrders.length})` },
          { id: 'pending',     lbl: `Pending (${workOrders.filter(x => x.st === 'Pending').length})` },
          { id: 'scheduled',   lbl: `Scheduled (${workOrders.filter(x => x.st === 'Scheduled').length})` },
          { id: 'in progress', lbl: `In Progress (${workOrders.filter(x => x.st === 'In Progress').length})` },
          { id: 'closed',      lbl: `Closed (${workOrders.filter(x => x.st === 'Closed').length})` },
          { id: 'rejected',    lbl: `Rejected (${workOrders.filter(x => x.st === 'Rejected').length})` },
        ].map(f => (
          <button
            key={f.id}
            className={`wofb${filter === f.id ? ' on' : ''}`}
            onClick={() => setFilter(f.id)}
          >
            {f.lbl}
          </button>
        ))}
      </div>
      <div className="wocards">
        {filtered.map(wo => {
          const pc = PR_COLOR[wo.pr] || 'var(--t3)';
          const pb = PR_BG[wo.pr] || 'rgba(255,255,255,.04)';
          return (
            <div
              key={wo.id}
              className={`wcard${selectedWO && selectedWO.id === wo.id ? ' on' : ''}`}
              onClick={() => onSelect(wo)}
            >
              <div className="wctop">
                <div className="wcid">{wo.id}</div>
                <div className="wcpr" style={{ background: pb, color: pc }}>{wo.pr}</div>
              </div>
              <div className="wcti">{wo.ti}</div>
              <div className="wcas">{wo.as}</div>
              <div className="wcft">
                <div className="wcag">
                  {wo.st === 'Closed' ? '✓ Completed' : `👤 ${WO_ASSIGNEES[wo.st] || wo.asgn || '—'}`}
                </div>
                <div className="wcdu" style={{ color: wo.st === 'Closed' ? 'var(--t3)' : 'var(--am)' }}>
                  {wo.st === 'Closed' ? 'Closed' : wo.due}
                </div>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
