/**
 * Shared fetch wrapper that injects the Authorization header on every request.
 * All API modules should import `authedFetch` instead of calling `fetch` directly.
 *
 * Token lifecycle:
 *   setToken(t)  — called by AuthContext after a successful login
 *   clearToken() — called on logout or when a 401 is received
 *   getToken()   — read by authedFetch before every request
 *
 * A 401 response dispatches 'dro-auth-expired' so AuthContext can reset state.
 */

const TOKEN_KEY = 'dro_access_token';

export function getToken() {
  return localStorage.getItem(TOKEN_KEY);
}

export function setToken(token) {
  localStorage.setItem(TOKEN_KEY, token);
}

export function clearToken() {
  localStorage.removeItem(TOKEN_KEY);
}

function authHeaders() {
  const token = getToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

export async function authedFetch(url, options = {}) {
  const resp = await fetch(url, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      ...authHeaders(),
      ...(options.headers || {}),
    },
  });

  if (resp.status === 401) {
    clearToken();
    window.dispatchEvent(new CustomEvent('dro-auth-expired'));
  }

  return resp;
}
