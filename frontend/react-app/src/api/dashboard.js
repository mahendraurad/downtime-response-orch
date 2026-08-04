import { API } from '../config/api';
import { authedFetch } from './http';

export async function fetchDashboardAssets() {
  const response = await authedFetch(`${API}/api/dashboard/assets`);
  if (!response.ok) throw new Error(`Asset API HTTP ${response.status}`);
  return response.json();
}
