import { api } from './api.js';

const grid = document.getElementById('grid');
const msg = document.getElementById('msg');

function fmtDate(ms) {
  if (!ms) return '';
  return new Date(Number(ms)).toLocaleString();
}

function statusLabel(job) {
  if (job.status === 'SUCCEEDED') return { text: 'ready', cls: 'ok' };
  if (job.status === 'FAILED') return { text: 'failed', cls: '' };
  return { text: (job.stage || job.status || 'running').replace(/_/g, ' '), cls: '' };
}

async function main() {
  try {
    const { jobs } = await api.listJobs();
    if (!jobs.length) {
      msg.textContent = 'No captures yet. Start one from "New capture".';
      return;
    }
    grid.innerHTML = '';
    for (const job of jobs) {
      const ready = job.status === 'SUCCEEDED';
      const href = ready ? `/viewer/${job.jobId}` : `/status/${job.jobId}`;
      const { text, cls } = statusLabel(job);
      const tile = document.createElement('a');
      tile.className = 'tile';
      tile.href = href;
      tile.innerHTML = `
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:0.5rem">
          <span class="badge ${cls}">${text}</span>
          <span style="color:var(--muted);font-size:0.75rem">${job.preset || ''}</span>
        </div>
        <div class="id">${job.jobId.slice(0, 12)}…</div>
        <div style="color:var(--muted);font-size:0.72rem;margin-top:0.4rem">${fmtDate(job.createdAt)}</div>`;
      grid.appendChild(tile);
    }
  } catch (err) {
    msg.textContent = err.message;
    msg.className = 'msg error';
  }
}

main();
