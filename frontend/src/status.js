import { api } from './api.js';

// Ordered UI checklist. Keep in sync with worker/stages.py STAGES (minus the
// terminal done/failed markers, which drive overall state instead).
const STAGES = [
  ['provisioning', 'Provisioning GPU'],
  ['booting', 'Booting worker'],
  ['downloading', 'Downloading footage'],
  ['stitching', 'Stitching 360° video'],
  ['extracting_frames', 'Extracting frames'],
  ['masking', 'Masking people & ground'],
  ['extracting_cubemaps', 'Extracting cubemap faces'],
  ['running_sfm', 'Running structure-from-motion'],
  ['converting', 'Preparing training dataset'],
  ['training', 'Training Gaussian splats'],
  ['uploading_result', 'Uploading result'],
];

const jobId = decodeURIComponent(location.pathname.split('/').filter(Boolean).pop() || '');
document.getElementById('jobid').textContent = jobId;

const stagesEl = document.getElementById('stages');
const actionsEl = document.getElementById('actions');
const errorEl = document.getElementById('error');
const elapsedEl = document.getElementById('elapsed');

let createdAt = null;

function render(job) {
  const masking = job.masking !== false;
  const currentIdx = STAGES.findIndex(([s]) => s === job.stage);
  const succeeded = job.status === 'SUCCEEDED';
  const failed = job.status === 'FAILED';

  stagesEl.innerHTML = '';
  STAGES.forEach(([stage, label], i) => {
    const li = document.createElement('li');
    let cls = '';
    if (stage === 'masking' && !masking) {
      cls = 'skipped';
    } else if (succeeded) {
      cls = 'done';
    } else if (failed && i === currentIdx) {
      cls = 'failed';
    } else if (currentIdx === -1) {
      cls = '';
    } else if (i < currentIdx) {
      cls = 'done';
    } else if (i === currentIdx) {
      cls = 'current';
    }
    li.className = cls;
    li.innerHTML = `<span class="dot"></span><span>${label}</span>`;
    stagesEl.appendChild(li);
  });

  if (succeeded) {
    actionsEl.innerHTML = `<a class="primary" style="display:inline-block;text-decoration:none;padding:0.75rem 1.3rem;border-radius:8px;background:var(--accent);color:#05222b;font-weight:600" href="/viewer/${jobId}">View in viewer →</a>`;
  } else {
    actionsEl.innerHTML = '';
  }
  errorEl.textContent = failed ? (job.error || 'Job failed.') : '';
}

function updateElapsed() {
  if (!createdAt) return;
  const secs = Math.floor((Date.now() - createdAt) / 1000);
  const m = Math.floor(secs / 60);
  const s = secs % 60;
  elapsedEl.textContent = `${m}m ${s.toString().padStart(2, '0')}s elapsed`;
}

async function poll() {
  try {
    const job = await api.getJob(jobId);
    if (!createdAt && job.createdAt) createdAt = Number(job.createdAt);
    render(job);
    if (job.status === 'SUCCEEDED' || job.status === 'FAILED') return; // stop polling
  } catch (err) {
    errorEl.textContent = err.message;
  }
  setTimeout(poll, 3000);
}

if (!jobId) {
  errorEl.textContent = 'No job id in URL.';
} else {
  setInterval(updateElapsed, 1000);
  poll();
}
