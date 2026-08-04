import { API } from '../config/api';
import { authedFetch } from './http';

export async function fetchWorkOrders() {
  const resp = await authedFetch(`${API}/api/workorders`);
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
  return resp.json();
}

export async function patchWorkOrder(id, data) {
  await authedFetch(`${API}/api/workorders/${id}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
}
