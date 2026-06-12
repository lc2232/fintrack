/**
 * Weights view — set the portfolio weighting of each completed factsheet.
 * Weights must sum to ~1.0 (tolerance [0.98, 1.02]).
 */
import { api } from './api.js';
import { getCompletedJobs, getJobs, refreshJobs } from './store.js';
import { renderError, loadingHTML, toast } from './ui.js';

const MIN_SUM = 0.98;
const MAX_SUM = 1.02;

function currentSum() {
  return getCompletedJobs().reduce((sum, j) => {
    const val = parseFloat(document.getElementById(`w-${j.jobId}`)?.value);
    return sum + (Number.isNaN(val) ? 0 : val);
  }, 0);
}

function updateWeightSum() {
  const el = document.getElementById('weight-sum-display');
  if (!el) return;
  const sum = currentSum();
  const ok = sum >= MIN_SUM && sum <= MAX_SUM;
  el.textContent = `Sum: ${sum.toFixed(3)}${ok ? '  ✓' : '  (must equal 1.0)'}`;
  el.className = `weight-sum ${ok ? 'ok' : 'bad'}`;
}

async function saveWeights() {
  const weights = getCompletedJobs().map((j) => ({
    jobId: j.jobId,
    weight: parseFloat(document.getElementById(`w-${j.jobId}`)?.value || 0),
  }));
  const sum = weights.reduce((a, b) => a + b.weight, 0);
  if (sum < MIN_SUM || sum > MAX_SUM) {
    toast('Weights must sum to 1.0', true);
    return;
  }

  try {
    await api('PATCH', '/upload/weights', { weights });
    toast('Weights saved.');
    await refreshJobs();
    document.dispatchEvent(new CustomEvent('jobs:changed'));
  } catch (e) {
    toast(e.message, true);
  }
}

function renderWeights(el) {
  const completed = getCompletedJobs();
  if (completed.length === 0) {
    el.innerHTML =
      '<p class="empty">No completed jobs yet. Weights can only be set for completed factsheets.</p>';
    return;
  }

  const rows = completed
    .map(
      (j) => `
      <div class="weight-row">
        <div class="job-name">${j.name || j.jobId.slice(0, 8) + '…'}</div>
        <input type="number" id="w-${j.jobId}" value="${parseFloat(j.weighting || 0).toFixed(2)}"
               min="0" max="1" step="0.01" />
      </div>`
    )
    .join('');

  el.innerHTML = `
    ${rows}
    <div id="weight-sum-display" class="weight-sum">Sum: —</div>
    <button id="save-weights-btn" class="btn btn-primary">Save weights</button>`;

  // Wire freshly-rendered controls.
  completed.forEach((j) => {
    document.getElementById(`w-${j.jobId}`).addEventListener('input', updateWeightSum);
  });
  document.getElementById('save-weights-btn').addEventListener('click', saveWeights);
  updateWeightSum();
}

/** Fetch (if needed) and render the weights editor. */
export async function loadWeights() {
  const el = document.getElementById('weights-content');
  el.innerHTML = loadingHTML();
  try {
    if (getJobs().length === 0) await refreshJobs();
    renderWeights(el);
  } catch (e) {
    renderError(el, e.message);
  }
}
