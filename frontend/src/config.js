// API base URL. Injected at build time via VITE_API_BASE (the api_endpoint
// Terraform output). Falls back to "/api" if you front the API on the same
// origin via a proxy.
export const API_BASE = (import.meta.env.VITE_API_BASE || '/api').replace(/\/$/, '');
