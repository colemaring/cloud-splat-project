import { API_BASE } from './config.js';

async function jsonFetch(path, options = {}) {
  const res = await fetch(`${API_BASE}${path}`, {
    headers: { 'content-type': 'application/json' },
    ...options,
  });
  if (!res.ok) {
    let detail = '';
    try { detail = (await res.json()).error || ''; } catch {}
    throw new Error(detail || `${options.method || 'GET'} ${path} failed (${res.status})`);
  }
  return res.json();
}

export const api = {
  createUploadUrls: (files) =>
    jsonFetch('/uploads', { method: 'POST', body: JSON.stringify({ files }) }),

  completeUpload: (files) =>
    jsonFetch('/uploads/complete', { method: 'POST', body: JSON.stringify({ files }) }),

  createJob: (payload) =>
    jsonFetch('/jobs', { method: 'POST', body: JSON.stringify(payload) }),

  getJob: (id) => jsonFetch(`/jobs/${encodeURIComponent(id)}`),

  listJobs: () => jsonFetch('/jobs'),
};
