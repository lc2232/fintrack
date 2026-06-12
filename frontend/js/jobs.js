/**
 * Jobs view — lists every portfolio item (PDF uploads and ISIN-added funds).
 */
import { getJobs, refreshJobs } from './store.js';
import { renderError, loadingHTML } from './ui.js';

function sourceBadge(source) {
  if (source === 'justetf') return '<span class="badge badge-source">JustETF</span>';
  if (source === 'pdf') return '<span class="badge badge-source">PDF</span>';
  return '<span class="dash">—</span>';
}

function renderJobs(jobs, el) {
  if (!jobs || jobs.length === 0) {
    el.innerHTML =
      '<p class="empty">No jobs yet. Head to “Add” to upload a factsheet or add a fund by ISIN.</p>';
    return;
  }

  const rows = jobs
    .map(
      (j) => `
      <tr>
        <td class="mono">${j.jobId.slice(0, 8)}…</td>
        <td>${j.name || '<span class="dash">—</span>'}</td>
        <td>${sourceBadge(j.source)}</td>
        <td><span class="badge badge-${j.status}">${j.status}</span></td>
        <td class="num">${j.weighting != null ? (parseFloat(j.weighting) * 100).toFixed(1) + '%' : '—'}</td>
      </tr>`
    )
    .join('');

  el.innerHTML = `
    <table>
      <thead>
        <tr>
          <th>Job ID</th>
          <th>Fund Name</th>
          <th>Source</th>
          <th>Status</th>
          <th>Weight</th>
        </tr>
      </thead>
      <tbody>${rows}</tbody>
    </table>`;
}

/** Fetch and render the jobs table. */
export async function loadJobs() {
  const el = document.getElementById('jobs-content');
  el.innerHTML = loadingHTML();
  try {
    const jobs = await refreshJobs();
    renderJobs(jobs, el);
  } catch (e) {
    renderError(el, e.message);
  }
}

/** Wire up the Jobs view (refresh button). */
export function initJobs() {
  document.getElementById('jobs-refresh').addEventListener('click', loadJobs);
  // Re-render from cache without a network call (used after other views update jobs).
  document.addEventListener('jobs:changed', () => {
    const el = document.getElementById('jobs-content');
    if (el) renderJobs(getJobs(), el);
  });
}
