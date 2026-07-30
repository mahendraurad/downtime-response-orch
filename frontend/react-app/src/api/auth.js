import { API } from '../config/api';
import { authedFetch } from './http';

/**
 * POST /api/auth/login
 * Returns { access_token, username, display_name, role, allowed_personas, expires_in_hours }
 * Throws on bad credentials or network error.
 */
export async function loginUser(username, password) {
  const resp = await fetch(`${API}/api/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password }),
  });
  if (!resp.ok) {
    const body = await resp.json().catch(() => ({}));
    throw new Error(body.detail || `Login failed (HTTP ${resp.status})`);
  }
  return resp.json();
}

/**
 * GET /api/auth/me
 * Decodes the stored token server-side. Used on app load to rehydrate the session.
 * Throws if the token is missing or expired (401).
 */
export async function getMe() {
  const resp = await authedFetch(`${API}/api/auth/me`);
  if (!resp.ok) throw new Error('Session expired or invalid.');
  return resp.json();
}
