import React from 'react';
import { AppProvider, AppContext } from './context/AppContext';
import { AuthProvider, useAuth } from './context/AuthContext';
import LoginScreen from './components/LoginScreen';
import Topbar from './components/Topbar';
import PersonaPanel from './components/PersonaPanel';
import ChatView from './components/ChatView/ChatView';
import AgentGraphView from './components/AgentGraphView/AgentGraphView';
import AssetRegistryView from './components/AssetRegistryView/AssetRegistryView';
import WorkOrdersView from './components/WorkOrdersView/WorkOrdersView';

function AppInner() {
  const { currentView, theme, showLeftPanel, setShowLeftPanel } = React.useContext(AppContext);

  React.useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme);
  }, [theme]);

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100vh', overflow: 'hidden' }}>
      <Topbar />
      <div className="main">
        {showLeftPanel ? (
          <PersonaPanel />
        ) : (
          <div
            onClick={() => setShowLeftPanel(true)}
            title="Show left panel: Different personas"
            style={{
              width: '18px', flexShrink: 0, background: 'var(--bg2)',
              borderRight: '1px solid var(--b)', cursor: 'pointer',
              display: 'flex', alignItems: 'center', justifyContent: 'center',
            }}
          >
            <span style={{ color: 'var(--t3)', fontSize: '10px' }}>▶</span>
          </div>
        )}
        <div className="cnt">
          <div className={`view${currentView === 'chat' ? ' on' : ''}`} id="view-chat">
            <ChatView />
          </div>
          <div className={`view${currentView === 'agents' ? ' on' : ''}`} id="view-agents">
            <AgentGraphView />
          </div>
          <div className={`view${currentView === 'assets' ? ' on' : ''}`} id="view-assets">
            <AssetRegistryView />
          </div>
          <div className={`view${currentView === 'workorders' ? ' on' : ''}`} id="view-workorders">
            <WorkOrdersView />
          </div>
        </div>
      </div>
    </div>
  );
}

function AuthGate() {
  const { user, loading } = useAuth();

  // Apply saved theme even before the user logs in
  React.useEffect(() => {
    const theme = localStorage.getItem('dro-theme') || 'light';
    document.documentElement.setAttribute('data-theme', theme);
  }, []);

  if (loading) {
    return (
      <div style={{
        alignItems: 'center', background: 'var(--bg)', display: 'flex',
        height: '100vh', justifyContent: 'center',
      }}>
        <div style={{ color: 'var(--t2)', fontFamily: 'var(--f)', fontSize: '13px' }}>
          Loading…
        </div>
      </div>
    );
  }

  if (!user) return <LoginScreen />;

  return (
    <AppProvider>
      <AppInner />
    </AppProvider>
  );
}

export default function App() {
  return (
    <AuthProvider>
      <AuthGate />
    </AuthProvider>
  );
}
