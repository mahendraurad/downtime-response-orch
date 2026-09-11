import { API } from '../config/api.js';
import { authedFetch } from './http.js';

export async function fetchDashboardAssets() {
  const response = await authedFetch(`${API}/api/dashboard/assets`);
  if (!response.ok) throw new Error(`Asset API HTTP ${response.status}`);
  return response.json();
}
