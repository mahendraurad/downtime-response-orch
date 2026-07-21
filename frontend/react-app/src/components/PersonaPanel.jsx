import React, { useContext } from 'react';
import { AppContext } from '../context/AppContext';
import { PD } from '../data/personas';
import { markNotifRead } from '../api/pipeline';

const PERSONA_SECTIONS = [
  {
    label: 'Operations',
    items: [
      { id: 'supervisor', nm: 'Plant Supervisor', rl: 'Line 4 · Day Shift' },
      { id: 'engineer',   nm: 'Reliability Engr.', rl: 'Rotating Equipment' },
      { id: 'maintenance', nm: 'Maint. Planner', rl: 'MRO · Scheduling' },
    ]
  },
  {
    label: 'Management',
    items: [
      { id: 'manager',   nm: 'Plant Manager', rl: 'Operations Lead' },
      { id: 'executive', nm: 'VP Operations', rl: 'Asset Leadership' },
    ]
  },
  {
    label: 'Technical',
    items: [
      { id: 'ot',     nm: 'OT / Controls', rl: 'Historian · SCADA' },
      { id: 'safety', nm: 'Safety Officer', rl: 'Compliance · HSE' },
    ]
  },
];

export default function PersonaPanel() {
  const { persona, setPersona, notifCounts, refreshNotifCounts } = useContext(AppContext);

  function handleSelect(pid) {
    setPersona(pid);
    window.dispatchEvent(new CustomEvent('dro-persona-change', { detail: pid }));
    if (notifCounts[pid] > 0) {
      markNotifRead(pid).then(refreshNotifCounts).catch(() => {});
    }
  }

  return (
    <div className="lp">
      <div className="lphdr">Active Persona</div>
      <div className="plist">
        {PERSONA_SECTIONS.map((section, si) => (
          <React.Fragment key={section.label}>
            <div className="slbl" style={si > 0 ? { marginTop: '4px' } : {}}>{section.label}</div>
            {section.items.map(item => {
              const pd = PD[item.id];
              const isActive = persona === item.id;
              const count = notifCounts[item.id] || 0;
              return (
                <div
                  key={item.id}
                  className={`pi${isActive ? ' on' : ''}`}
                  onClick={() => handleSelect(item.id)}
                  id={`pp-${item.id}`}
                >
                  <div className="pav" style={{ background: pd.bg }}>{pd.av}</div>
                  <div className="pin">
                    <div className="pnm">{item.nm}</div>
                    <div className="prl">{item.rl}</div>
                  </div>
                  <div className="pdot" style={{ background: pd.col }}></div>
                  {count > 0 && !isActive && (
                    <div className="pnotif">{count}</div>
                  )}
                </div>
              );
            })}
          </React.Fragment>
        ))}
      </div>
      <div className="fbox">
        <div className="fttl">Fleet Snapshot</div>
        <div className="fs"><span className="fsl">Critical</span><span className="fsv" style={{ color: 'var(--rd)' }}>2 assets</span></div>
        <div className="fs"><span className="fsl">Warning</span><span className="fsv" style={{ color: 'var(--am)' }}>5 assets</span></div>
        <div className="fs"><span className="fsl">Healthy</span><span className="fsv" style={{ color: 'var(--gn)' }}>40 assets</span></div>
        <div className="fs" style={{ marginTop: '3px', paddingTop: '3px', borderTop: '1px solid var(--b)' }}>
          <span className="fsl">Avg RUL</span>
          <span className="fsv" style={{ color: 'var(--am)', fontFamily: 'var(--m)' }}>14.2 days</span>
        </div>
      </div>
    </div>
  );
}
