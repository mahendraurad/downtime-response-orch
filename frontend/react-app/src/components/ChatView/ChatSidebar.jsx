import React, { useEffect, useRef, useState } from 'react';

const AG_STATUS_ROWS = [
  { cls: 'don', nm: 'Data Foundation', tg: 'OK' },
  { cls: 'don', nm: 'Monitoring', tg: 'TRIGGERED' },
  { cls: 'drn', nm: 'Predictive Risk', tg: 'RUNNING', id: 'rptag' },
  { cls: 'don', nm: 'Failure Intel', tg: 'COMPLETE' },
  { cls: 'don', nm: 'Knowledge', tg: 'READY' },
  { cls: 'dof', nm: 'Prescriptive Opt.', tg: 'STANDBY' },
  { cls: 'dof', nm: 'Executor', tg: 'AWAITING' },
];

const RP_TAGS = ['RUNNING', 'ANALYSING', 'COMPLETE', 'READY'];

const RUL_ROWS = [
  { nm: 'M-104', tp: 'Motor', w: 12, col: 'var(--rd)', d: '~6d', q: 'Full risk briefing and recommended action for M-104' },
  { nm: 'P-207', tp: 'Pump', w: 38, col: 'var(--am)', d: '~18d', q: 'P-207 bearing condition and urgency — should I act before Wednesday?' },
  { nm: 'C-301', tp: 'Conveyor', w: 55, col: 'var(--am)', d: '~26d', q: 'C-301 cage fault — how urgent and what is the right action?' },
  { nm: 'M-089', tp: 'Motor', w: 80, col: 'var(--gn)', d: '~37d', q: 'M-089 health after recent bearing replacement — any run-in concerns?' },
  { nm: 'G-112', tp: 'Gearbox', w: 90, col: 'var(--gn)', d: '~43d', q: 'G-112 gearbox post-repair performance — is it settling normally?' },
];

export default function ChatSidebar({ onSq }) {
  const [sv1, setSv1] = useState(14.7);
  const [sv2, setSv2] = useState(87.4);
  const [rpTagIdx, setRpTagIdx] = useState(0);
  const sv1Ref = useRef(14.7);
  const sv2Ref = useRef(87.4);

  useEffect(() => {
    const sensorInterval = setInterval(() => {
      sv1Ref.current = parseFloat((sv1Ref.current + (Math.random() * 0.2 - 0.05)).toFixed(1));
      sv2Ref.current = parseFloat((sv2Ref.current + (Math.random() * 0.4 - 0.1)).toFixed(1));
      setSv1(sv1Ref.current);
      setSv2(sv2Ref.current);
    }, 4200);

    const rpInterval = setInterval(() => {
      setRpTagIdx(i => (i + 1) % RP_TAGS.length);
    }, 2800);

    return () => {
      clearInterval(sensorInterval);
      clearInterval(rpInterval);
    };
  }, []);

  return (
    <div className="cctx">
      {/* Live sensors */}
      <div className="cxs">
        <div className="sttl">Live Sensors · M-104</div>
        <div className="sgrd">
          <div className="sc">
            <div className="sl">Vibration RMS</div>
            <div className="sv" style={{ color: 'var(--rd)' }}>{sv1.toFixed(1)}</div>
            <div className="su">mm/s</div>
            <div className="str stup">↑ +0.3/hr</div>
          </div>
          <div className="sc">
            <div className="sl">Temperature</div>
            <div className="sv" style={{ color: 'var(--am)' }}>{sv2.toFixed(1)}</div>
            <div className="su">°C</div>
            <div className="str stup">↑ +1.2/hr</div>
          </div>
          <div className="sc">
            <div className="sl">BPFO Ratio</div>
            <div className="sv" style={{ color: 'var(--rd)' }}>4.02</div>
            <div className="su">× base</div>
            <div className="str stst">— stable</div>
          </div>
          <div className="sc">
            <div className="sl">Speed</div>
            <div className="sv">1474</div>
            <div className="su">RPM</div>
            <div className="str stst">— nominal</div>
          </div>
        </div>
        <svg style={{ width: '100%', height: '22px', marginTop: '6px' }} viewBox="0 0 230 22" preserveAspectRatio="none">
          <polyline
            points="0,20 19,18 38,17 57,15 76,12 95,10 114,9 133,7 152,5 171,4 190,3 209,2 230,1"
            fill="none"
            stroke="var(--rd)"
            strokeWidth="1.4"
            opacity=".65"
          />
        </svg>
        <div style={{ fontSize: '8.5px', color: 'var(--t3)', fontFamily: 'var(--m)', marginTop: '1px' }}>
          Vibration · last 12h
        </div>
      </div>

      {/* Agent pipeline status */}
      <div className="cxs">
        <div className="sttl">Agent Pipeline Status</div>
        <div className="aglst">
          {AG_STATUS_ROWS.map((row, i) => {
            const tag = row.id === 'rptag' ? RP_TAGS[rpTagIdx] : row.tg;
            const dotCls = row.id === 'rptag'
              ? (rpTagIdx === 2 ? 'adc don' : 'adc drn')
              : `adc ${row.cls}`;
            return (
              <div key={i} className="agrow">
                <div className={dotCls}></div>
                <div className="anm">{row.nm}</div>
                <div className="atg">{tag}</div>
              </div>
            );
          })}
        </div>
      </div>

      {/* Fleet RUL watch */}
      <div className="cxs" style={{ borderBottom: 'none' }}>
        <div className="sttl">Fleet RUL Watch</div>
        <div className="rlfl">
          {RUL_ROWS.map((r, i) => (
            <div key={i} className="rlr" onClick={() => onSq(r.q)}>
              <div className="rlnm">{r.nm}</div>
              <div className="rltp">{r.tp}</div>
              <div className="rlmb">
                <div className="rlmf" style={{ width: r.w + '%', background: r.col }}></div>
              </div>
              <div className="rld" style={{ color: r.col }}>{r.d}</div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
