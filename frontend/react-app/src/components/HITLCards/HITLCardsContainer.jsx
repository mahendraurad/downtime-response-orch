import React from 'react';

/**
 * HITLCardsContainer — placeholder for standalone HITL card rendering outside chat.
 * The actual HITL card components live inside ChatView.jsx (HITLRemediationMsg,
 * HITLAdvisoryMsg, HITLMonitoringMsg, HITLDiagnosisMsg, HITLKnowledgeMsg, HITLExecutorMsg).
 */
export default function HITLCardsContainer({ type, data, onResolve }) {
  return (
    <div style={{ padding: '12px', color: 'var(--t2)', fontSize: '12px' }}>
      HITL card type &ldquo;{type}&rdquo; — rendered inline in chat stream.
    </div>
  );
}
