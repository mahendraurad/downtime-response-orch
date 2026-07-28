import { API } from '../config/api';

/** Natural-language questions always enter through the backend orchestrator. */
export async function askChat({
  message,
  persona,
  conversationId = null,
  assetId = null,
  context = null,
}) {
  const resp = await fetch(`${API}/api/chat`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      message,
      persona,
      conversation_id: conversationId,
      asset_id: assetId,
      context,
    }),
  });
  if (!resp.ok) {
    let detail = `Chat request failed (HTTP ${resp.status}).`;
    try {
      const body = await resp.json();
      detail = body.detail || body.error?.message || detail;
    } catch {
      // Keep the status-based message when the response body is not JSON.
    }
    throw new Error(detail);
  }
  return resp.json();
}
