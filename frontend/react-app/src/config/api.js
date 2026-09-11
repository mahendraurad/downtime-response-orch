// Use VITE_API_BASE_URL when the API is hosted separately. By default the
// browser origin is used: Vite proxies /api and /ws locally, while a deployed
// build can be served behind the same reverse proxy as FastAPI.
// `import.meta.env` is injected by Vite and is absent when the API modules are
// exercised directly by Node contract tests. Optional access keeps both
// runtimes deterministic while preserving the browser-origin fallback.
export const API = (import.meta.env?.VITE_API_BASE_URL || window.location.origin)
  .replace(/\/$/, '');
