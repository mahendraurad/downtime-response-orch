// Use VITE_API_BASE_URL when the API is hosted separately. By default the
// browser origin is used: Vite proxies /api and /ws locally, while a deployed
// build can be served behind the same reverse proxy as FastAPI.
export const API = (import.meta.env.VITE_API_BASE_URL || window.location.origin)
  .replace(/\/$/, '');
