import { API } from '../config/api';

export async function fetchWorkOrders() {
  const resp = await fetch(`${API}/api/workorders`);
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
  return resp.json();
}

export async function patchWorkOrder(id, data) {
  await fetch(`${API}/api/workorders/${id}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
}
