import React, { createContext, useState, useCallback, useEffect } from 'react';
import { ASSETS } from '../data/assets';
import { WOS } from '../data/workOrders';
import { getNotifCounts } from '../api/pipeline';
import { fetchDashboardAssets } from '../api/dashboard';
import { fetchWorkOrders } from '../api/workOrders';
import { useAuth } from './AuthContext';

export const AppContext = createContext(null);

const SESSION_KEY = 'dro-chat';
const MAX_MSGS_PER_PERSONA = 120;

function loadSession() {
  try {
    const raw = sessionStorage.getItem(SESSION_KEY);
    return raw ? JSON.parse(raw) : {};
  } catch {
    return {};
  }
}

function saveSession(map) {
  try {
    sessionStorage.setItem(SESSION_KEY, JSON.stringify(map));
  } catch {
    // sessionStorage full — silently skip
  }
}

export function AppProvider({ children }) {
  const { user } = useAuth();
  const defaultPersona = user?.allowed_personas?.[0] || 'supervisor';
  const [persona, setPersona] = useState(defaultPersona);
  const [currentView, setCurrentView] = useState('chat');
  const [agentAsset, setAgentAsset] = useState('M-104');
  const [assets, setAssets] = useState(ASSETS);
  const [workOrders, setWorkOrders] = useState(WOS);
  const [selectedAsset, setSelectedAsset] = useState(ASSETS[0]);
  const [selectedWO, setSelectedWO] = useState(WOS[0]);
  const [dataStatus, setDataStatus] = useState({ assets: 'fallback', workOrders: 'fallback' });
  const [selectedNode, setSelectedNode] = useState(0);
  const [currentAgTab, setCurrentAgTab] = useState('overview');
  const [pipelineRunning, setPipelineRunning] = useState(false);
  const [theme, setThemeState] = useState(() => localStorage.getItem('dro-theme') || 'light');
  const [notifCounts, setNotifCounts] = useState({});
  const [showLeftPanel, setShowLeftPanel] = useState(true);

  useEffect(() => {
    let active = true;
    const fallbackByAsset = Object.fromEntries(ASSETS.map(a => [a.id, a]));
    fetchDashboardAssets().then(payload => {
      if (!active) return;
      const rows = (payload.assets || []).map(a => {
        const fallback = fallbackByAsset[a.id] || {};
        return {
          ...fallback,
          id: a.id, asset_id: a.asset_id, nm: a.name, tp: a.type,
          br: a.bearing_model, kw: a.power_kw, rpm: a.rpm, ln: a.line,
          st: a.status, rul: a.rul, vib: a.vibration, tmp: a.temperature,
          bpfo: a.bpfo, iso: `Zone ${a.iso_zone}`, trend: a.trend || [],
          faults: (a.faults || []).map(f => ({
            dt: f.date, ty: f.type, st: `${f.stage} ${f.status}`,
            sc: a.status === 'critical' ? 'var(--rd)' : 'var(--am)',
          })),
          backend_source: a.source,
        };
      });
      if (rows.length) {
        setAssets(rows);
        setSelectedAsset(current => rows.find(a => a.id === current?.id) || rows[0]);
        setDataStatus(current => ({ ...current, assets: payload.source || 'backend' }));
      }
    }).catch(() => setDataStatus(current => ({ ...current, assets: 'fallback' })));

    const fallbackByWO = Object.fromEntries(WOS.map(wo => [wo.id, wo]));
    fetchWorkOrders().then(payload => {
      if (!active) return;
      const rows = (payload.workorders || []).map(wo => ({
        ...(fallbackByWO[wo.id] || {}),
        id: wo.id, pr: wo.priority || 'MEDIUM', st: wo.status || 'Pending',
        ti: wo.title || wo.description || wo.id, as: wo.asset_id || '',
        asgn: wo.assigned_to || 'Pending', due: wo.due || 'Not scheduled',
        by: wo.created_by || 'DRO Agent', tp: wo.type || 'Maintenance',
        est: wo.est_hours ? `${wo.est_hours}h` : '', parts: wo.parts || '',
        cl: (wo.checklist || []).map(item => ({
          ck: Boolean(item.done), tx: item.text, tg: item.tag || 'Task',
        })),
        backend_source: 'api/workorders',
      }));
      if (rows.length) {
        setWorkOrders(rows);
        setSelectedWO(current => rows.find(wo => wo.id === current?.id) || rows[0]);
        setDataStatus(current => ({ ...current, workOrders: 'backend' }));
      }
    }).catch(() => setDataStatus(current => ({ ...current, workOrders: 'fallback' })));
    return () => { active = false; };
  }, []);

  // Per-persona message store, backed by sessionStorage so history survives persona switches
  const [messagesMap, setMessagesMap] = useState(loadSession);

  const messages = messagesMap[persona] || [];

  const _update = useCallback((pid, fn) => {
    setMessagesMap(prev => {
      const next = fn(prev[pid] || []);
      // Cap per-persona history to avoid sessionStorage bloat
      const capped = next.length > MAX_MSGS_PER_PERSONA
        ? next.slice(next.length - MAX_MSGS_PER_PERSONA)
        : next;
      const updated = { ...prev, [pid]: capped };
      saveSession(updated);
      return updated;
    });
  }, []);

  const setMessages = useCallback((msgs) => {
    _update(persona, () => typeof msgs === 'function' ? msgs(messagesMap[persona] || []) : msgs);
  }, [persona, _update, messagesMap]);

  const addMessage = useCallback((msg) => {
    _update(persona, prev => [...prev, msg]);
  }, [persona, _update]);

  const clearMessages = useCallback(() => {
    _update(persona, () => []);
  }, [persona, _update]);

  // Patch a field on one message in the current persona's history (e.g. resolved: true)
  const patchMessage = useCallback((id, patch) => {
    _update(persona, prev => prev.map(m => m.id === id ? { ...m, ...patch } : m));
  }, [persona, _update]);

  // Push a message into a DIFFERENT persona's history (for cross-persona notifications)
  const pushNotification = useCallback((targetPersona, msg) => {
    _update(targetPersona, prev => [...prev, msg]);
  }, [_update]);

  const setTheme = useCallback((t) => {
    setThemeState(t);
    localStorage.setItem('dro-theme', t);
  }, []);

  const refreshNotifCounts = useCallback(() => {
    getNotifCounts().then(setNotifCounts).catch(() => {});
  }, []);

  return (
    <AppContext.Provider value={{
      persona, setPersona,
      currentView, setCurrentView,
      agentAsset, setAgentAsset,
      assets, workOrders, dataStatus,
      selectedAsset, setSelectedAsset,
      selectedWO, setSelectedWO,
      selectedNode, setSelectedNode,
      currentAgTab, setCurrentAgTab,
      messages, setMessages, addMessage, clearMessages, patchMessage, pushNotification,
      pipelineRunning, setPipelineRunning,
      theme, setTheme,
      notifCounts, refreshNotifCounts,
      showLeftPanel, setShowLeftPanel,
    }}>
      {children}
    </AppContext.Provider>
  );
}
