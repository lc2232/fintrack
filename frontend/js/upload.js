/**
 * Upload view — add a fund by uploading a PDF factsheet.
 *
 * Flow: POST /upload returns a jobId + presigned S3 URL, then the file is PUT
 * directly to S3 (no auth header on the S3 call). Processing is asynchronous.
 */
import { api } from './api.js';
import { refreshJobs } from './store.js';
import { toast } from './ui.js';

const MAX_BYTES = 10 * 1024 * 1024;

let selectedFile = null;

function setFile(file) {
  if (file.type !== 'application/pdf') {
    toast('Only PDF files are supported.', true);
    return;
  }
  if (file.size > MAX_BYTES) {
    toast('File must be under 10 MB.', true);
    return;
  }
  selectedFile = file;
  const nameEl = document.getElementById('upload-file-name');
  nameEl.textContent = `${file.name} · ${(file.size / 1024).toFixed(0)} KB`;
  nameEl.style.display = 'block';
  document.getElementById('upload-btn').disabled = false;
  document.getElementById('upload-status').textContent = '';
}

function resetFile() {
  selectedFile = null;
  document.getElementById('upload-file-name').style.display = 'none';
  document.getElementById('file-input').value = '';
  const btn = document.getElementById('upload-btn');
  btn.textContent = 'Upload';
  btn.disabled = true;
}

async function doUpload() {
  if (!selectedFile) return;
  const btn = document.getElementById('upload-btn');
  const status = document.getElementById('upload-status');
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span> Creating job…';
  status.textContent = '';

  try {
    // 1. Create a job and get a presigned upload URL.
    const { jobId, uploadUrl } = await api('POST', '/upload');
    status.textContent = `Job ${jobId.slice(0, 8)}… created. Uploading to S3…`;

    // 2. PUT the file directly to S3 (presigned URL — no auth header).
    const s3Res = await fetch(uploadUrl, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/pdf' },
      body: selectedFile,
    });
    if (!s3Res.ok) throw new Error(`S3 upload failed (${s3Res.status})`);

    toast('Factsheet uploaded. Processing may take a moment.');
    status.textContent = '';
    resetFile();

    // Refresh the shared jobs cache so the new (pending) job appears elsewhere.
    await refreshJobs();
    document.dispatchEvent(new CustomEvent('jobs:changed'));
  } catch (e) {
    toast(e.message, true);
    status.textContent = '';
    btn.disabled = false;
    btn.textContent = 'Upload';
  }
}

/** Wire up the drag-and-drop zone, file picker, and upload button. */
export function initUpload() {
  const dropZone = document.getElementById('drop-zone');
  const fileInput = document.getElementById('file-input');

  dropZone.addEventListener('click', () => fileInput.click());
  dropZone.addEventListener('dragover', (e) => {
    e.preventDefault();
    dropZone.classList.add('dragover');
  });
  dropZone.addEventListener('dragleave', () => dropZone.classList.remove('dragover'));
  dropZone.addEventListener('drop', (e) => {
    e.preventDefault();
    dropZone.classList.remove('dragover');
    const file = e.dataTransfer.files[0];
    if (file) setFile(file);
  });

  fileInput.addEventListener('change', () => {
    if (fileInput.files[0]) setFile(fileInput.files[0]);
  });

  document.getElementById('upload-btn').addEventListener('click', doUpload);
}
