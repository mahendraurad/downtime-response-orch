/**
 * HITLCards — Human-in-the-Loop card components for the DRO pipeline.
 *
 * All HITL card types are implemented inline in ChatView.jsx as sub-components
 * of <MessageBubble> to keep state local to each message and avoid prop drilling.
 *
 * This file re-exports named stubs for external use in case they are ever needed
 * outside of the chat stream (e.g. a standalone HITL review modal).
 *
 * Gate types:
 *   - Remediation    — Data Foundation Agent: missing/low-quality signal data
 *   - Advisory       — Predictive Risk Agent: LLM advisory review
 *   - Monitoring     — Monitoring Agent: borderline EWMA anomaly
 *   - Diagnosis      — Failure Intelligence Agent: low-confidence classification
 *   - Knowledge      — Knowledge Agent: no SOP found
 *   - Executor       — Executor Agent: execution approval
 */

export { default as HITLCardsContainer } from './HITLCardsContainer';
