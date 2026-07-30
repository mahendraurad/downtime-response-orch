import React, { createContext, useState, useCallback } from 'react';
import { ASSETS } from '../data/assets';
import { WOS } from '../data/workOrders';
import { getNotifCounts } from '../api/pipeline';
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
  const [selectedAsset, setSelectedAsset] = useState(ASSETS[0]);
  const [selectedWO, setSelectedWO] = useState(WOS[0]);
  const [selectedNode, setSelectedNode] = useState(0);
  const [currentAgTab, setCurrentAgTab] = useState('overview');
  const [pipelineRunning, setPipelineRunning] = useState(false);
  const [theme, setThemeState] = useState(() => localStorage.getItem('dro-theme') || 'light');
  const [notifCounts, setNotifCounts] = useState({});

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
      selectedAsset, setSelectedAsset,
      selectedWO, setSelectedWO,
      selectedNode, setSelectedNode,
      currentAgTab, setCurrentAgTab,
      messages, setMessages, addMessage, clearMessages, patchMessage, pushNotification,
      pipelineRunning, setPipelineRunning,
      theme, setTheme,
      notifCounts, refreshNotifCounts,
    }}>
      {children}
    </AppContext.Provider>
  );
}
