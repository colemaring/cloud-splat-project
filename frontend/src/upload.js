import { api } from './api.js';

const PART_SIZE = 64 * 1024 * 1024; // 64 MB multipart chunks

const PRESETS = [
  { id: 'Draft',  desc: '¼ res · 5k iters · 300k splats · fastest' },
  { id: 'Low',    desc: '½ res · 10k iters · 500k splats' },
  { id: 'Medium', desc: 'full res · 15k iters · 750k splats' },
  { id: 'High',   desc: 'full res · 30k iters · 2M splats · slowest' },
];

let selectedPreset = 'Draft';

const presetsEl = document.getElementById('presets');
PRESETS.forEach((p, i) => {
  const el = document.createElement('div');
  el.className = 'preset' + (i === 0 ? ' selected' : '');
  el.innerHTML = `<b>${p.id}</b><small>${p.desc}</small>`;
  el.addEventListener('click', () => {
    selectedPreset = p.id;
    [...presetsEl.children].forEach((c) => c.classList.remove('selected'));
    el.classList.add('selected');
  });
  presetsEl.appendChild(el);
});

const form = document.getElementById('form');
const msg = document.getElementById('msg');
const submitBtn = document.getElementById('submit');
const bar = document.getElementById('progressbar');
const barFill = bar.firstElementChild;

function setMsg(text, isError = false) {
  msg.textContent = text;
  msg.className = 'msg' + (isError ? ' error' : '');
}

function validateNames(frontName, backName) {
  if (frontName.includes('_11_') || backName.includes('_11_'))
    throw new Error('Do not upload the _11_ (LRV) file.');
  if (!frontName.includes('_00_'))
    throw new Error(`Front file must contain "_00_" (got ${frontName}).`);
  if (!backName.includes('_10_'))
    throw new Error(`Back file must contain "_10_" (got ${backName}).`);
}

// Upload one file's parts directly to S3, returning the part ETags. Reports
// uploaded bytes through onProgress for an aggregate progress bar.
async function uploadParts(file, presigned, onProgress) {
  const etags = [];
  for (const part of presigned.parts) {
    const start = (part.partNumber - 1) * PART_SIZE;
    const blob = file.slice(start, Math.min(start + PART_SIZE, file.size));
    const etag = await putPart(part.url, blob, onProgress);
    etags.push({ partNumber: part.partNumber, etag });
  }
  return etags;
}

function putPart(url, blob, onProgress) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open('PUT', url);
    let lastLoaded = 0;
    xhr.upload.onprogress = (e) => {
      onProgress(e.loaded - lastLoaded);
      lastLoaded = e.loaded;
    };
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        onProgress(blob.size - lastLoaded); // account for any remainder
        const etag = xhr.getResponseHeader('ETag');
        if (!etag) return reject(new Error('Missing ETag — check bucket CORS ExposeHeaders.'));
        resolve(etag);
      } else {
        reject(new Error(`Part upload failed (${xhr.status})`));
      }
    };
    xhr.onerror = () => reject(new Error('Network error during upload'));
    xhr.send(blob);
  });
}

form.addEventListener('submit', async (e) => {
  e.preventDefault();
  const front = document.getElementById('front').files[0];
  const back = document.getElementById('back').files[0];
  const masking = document.getElementById('masking').checked;
  if (!front || !back) return;

  submitBtn.disabled = true;
  bar.hidden = false;
  barFill.style.width = '0%';

  try {
    validateNames(front.name, back.name);
    setMsg('Requesting upload URLs…');

    const partsFor = (f) => Math.max(1, Math.ceil(f.size / PART_SIZE));
    const presign = await api.createUploadUrls([
      { role: 'front', name: front.name, parts: partsFor(front) },
      { role: 'back', name: back.name, parts: partsFor(back) },
    ]);

    const byRole = Object.fromEntries(presign.files.map((f) => [f.role, f]));
    const totalBytes = front.size + back.size;
    let uploaded = 0;
    const onProgress = (delta) => {
      uploaded += delta;
      barFill.style.width = `${Math.min(100, (uploaded / totalBytes) * 100).toFixed(1)}%`;
    };

    setMsg('Uploading… (large captures can take a while)');
    const frontEtags = await uploadParts(front, byRole.front, onProgress);
    const backEtags = await uploadParts(back, byRole.back, onProgress);

    setMsg('Finalizing upload…');
    await api.completeUpload([
      { key: byRole.front.key, uploadId: byRole.front.uploadId, parts: frontEtags },
      { key: byRole.back.key, uploadId: byRole.back.uploadId, parts: backEtags },
    ]);

    setMsg('Launching GPU…');
    const { jobId } = await api.createJob({
      frontKey: byRole.front.key,
      backKey: byRole.back.key,
      preset: selectedPreset,
      masking,
    });

    window.location.href = `/status/${jobId}`;
  } catch (err) {
    setMsg(err.message, true);
    submitBtn.disabled = false;
  }
});
