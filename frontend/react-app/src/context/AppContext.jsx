import React, { createContext, useState, useCallback } from 'react';
import { ASSETS } from '../data/assets';
import { WOS } from '../data/workOrders';
import { getNotifCounts } from '../api/pipeline';

export const AppContext = createContext(null);

export function AppProvider({ children }) {
  const [persona, setPersona] = useState('supervisor');
  const [currentView, setCurrentView] = useState('chat');
  const [agentAsset, setAgentAsset] = useState('M-104');
  const [selectedAsset, setSelectedAsset] = useState(ASSETS[0]);
  const [selectedWO, setSelectedWO] = useState(WOS[0]);
  const [selectedNode, setSelectedNode] = useState(0);
  const [currentAgTab, setCurrentAgTab] = useState('overview');
  const [messages, setMessages] = useState([]);
  const [pipelineRunning, setPipelineRunning] = useState(false);
  const [theme, setThemeState] = useState(() => localStorage.getItem('dro-theme') || 'light');
  const [notifCounts, setNotifCounts] = useState({});

  const setTheme = useCallback((t) => {
    setThemeState(t);
    localStorage.setItem('dro-theme', t);
  }, []);

  const addMessage = useCallback((msg) => {
    setMessages(prev => [...prev, msg]);
  }, []);

  const clearMessages = useCallback(() => {
    setMessages([]);
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
      messages, setMessages, addMessage, clearMessages,
      pipelineRunning, setPipelineRunning,
      theme, setTheme,
      notifCounts, refreshNotifCounts,
    }}>
      {children}
    </AppContext.Provider>
  );
}
