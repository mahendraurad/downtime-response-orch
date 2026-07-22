import React from 'react';
import { AppProvider, AppContext } from './context/AppContext';
import Topbar from './components/Topbar';
import PersonaPanel from './components/PersonaPanel';
import ChatView from './components/ChatView/ChatView';
import AgentGraphView from './components/AgentGraphView/AgentGraphView';
import AssetRegistryView from './components/AssetRegistryView/AssetRegistryView';
import WorkOrdersView from './components/WorkOrdersView/WorkOrdersView';

function AppInner() {
  const { currentView, theme } = React.useContext(AppContext);

  React.useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme);
  }, [theme]);
  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100vh', overflow: 'hidden' }}>
      <Topbar />
      <div className="main">
        <PersonaPanel />
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

export default function App() {
  return (
    <AppProvider>
      <AppInner />
    </AppProvider>
  );
}
