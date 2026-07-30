import React, { useContext, useState, useRef, useEffect } from 'react';
import { AppContext } from '../../context/AppContext';
import { ts } from '../../utils/helpers';
import { askChat } from '../../api/chat';

export default function AssetChat({ asset }) {
  const { persona } = useContext(AppContext);
  const [messages, setMessages] = useState([]);
  const [inputVal, setInputVal] = useState('');
  const [thinking, setThinking] = useState(false);
  const msgsRef = useRef(null);
  const inpRef = useRef(null);
  const conversationIdRef = useRef(null);

  useEffect(() => {
    if (msgsRef.current) msgsRef.current.scrollTop = msgsRef.current.scrollHeight;
  }, [messages, thinking]);

  // Reset chat on asset change
  useEffect(() => {
    setMessages([]);
    conversationIdRef.current = null;
  }, [asset && asset.id]);

  if (!asset) return <div className="achat"></div>;

  const insights = (asset.insights && asset.insights[persona]) || [];
  async function askA(txt) {
    const t = (txt || inputVal).trim();
    if (!t) return;
    setInputVal('');
    if (inpRef.current) inpRef.current.value = '';

    setMessages(prev => [...prev,
      { id: Date.now(), type: 'user', text: t, time: ts() }
    ]);

    setThinking(true);
    try {
      const data = await askChat({
        message: t,
        persona,
        assetId: asset.id,
        conversationId: conversationIdRef.current,
      });
      conversationIdRef.current = data.conversation_id || conversationIdRef.current;
      const details = data.details?.length
        ? `<br><br>${data.details.map(x => `• ${x}`).join('<br>')}` : '';
      const actions = data.actions?.length
        ? `<br><br><strong>Actions:</strong><br>${data.actions.map(x => `→ ${x}`).join('<br>')}` : '';
      const questions = data.clarification?.questions?.length
        ? `<br><br><strong>Needed:</strong><br>${data.clarification.questions.map(x => `? ${x}`).join('<br>')}`
        : '';
      setMessages(prev => [...prev,
        { id: Date.now() + 1, type: 'agent',
          html: `${data.response}${details}${actions}${questions}`, time: ts() }
      ]);
    } catch (error) {
      setMessages(prev => [...prev,
        { id: Date.now() + 1, type: 'agent',
          html: `The orchestrated chat service is currently unavailable. ${error.message}`, time: ts() }
      ]);
    } finally {
      setThinking(false);
    }
  }

  return (
    <div className="achat">
      <div className="achdr">
        <div className="achdr-ttl">DRO Agent — {asset.id} Intelligence</div>
        <div className="achdr-sub">Ask about condition, diagnostics, or risk</div>
      </div>

      <div className="amsgs" ref={msgsRef}>
        {messages.map(msg => {
          if (msg.type === 'user') {
            return (
              <div key={msg.id} className="amg u fi">
                <div className="ambu">{msg.text}</div>
                <div style={{ fontSize: '9px', color: 'var(--t3)', textAlign: 'right', marginTop: '3px' }}>{msg.time}</div>
              </div>
            );
          }
          return (
            <div key={msg.id} className="amg fi">
              <div className="amba" dangerouslySetInnerHTML={{ __html: msg.html.replace(/\n/g, '<br>') }} />
              <div style={{ fontSize: '9px', color: 'var(--t3)', marginTop: '3px' }}>{msg.time}</div>
            </div>
          );
        })}
        {thinking && (
          <div className="amg fi">
            <div className="tdts" style={{ padding: '8px 12px' }}>
              <span></span><span></span><span></span>
            </div>
          </div>
        )}
      </div>

      <div className="achips">
        <div className="achip-ttl">Quick questions</div>
        <div className="achip-row">
          {insights.map((ins, i) => (
            <button
              key={i}
              className={`chip${ins.c === 'r' ? ' red' : ins.c === 'g' ? ' grn' : ins.c === 'b' ? ' blu' : ''}`}
              style={{ fontSize: '10px', marginBottom: '4px' }}
              onClick={() => askA(ins.l)}
            >
              {ins.l}
            </button>
          ))}
        </div>
        <div className="ainrow">
          <input
            ref={inpRef}
            type="text"
            className="ainp"
            placeholder={`Ask about ${asset.id}…`}
            value={inputVal}
            onChange={e => setInputVal(e.target.value)}
            onKeyDown={e => {
              if (e.key === 'Enter') { askA(e.target.value); e.target.value = ''; setInputVal(''); }
            }}
          />
          <button className="sbtn" onClick={() => askA()}>
            <svg viewBox="0 0 24 24"><path d="M2.01 21L23 12 2.01 3 2 10l15 2-15 2z" /></svg>
          </button>
        </div>
      </div>
    </div>
  );
}
