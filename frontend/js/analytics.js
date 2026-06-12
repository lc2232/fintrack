/**
 * Analytics view — aggregated, weighted exposure across all completed funds.
 * Renders three horizontal bar charts from GET /analytics/summary.
 */
import { api } from './api.js';
import { renderError, loadingHTML } from './ui.js';

const SECTIONS = [
  { key: 'portfolio_industry_exposure', title: 'Industry Exposure' },
  { key: 'portfolio_market_exposure', title: 'Market Exposure' },
  { key: 'portfolio_top_holdings', title: 'Top Holdings' },
];

function renderSection({ key, title }, data) {
  const entries = Object.entries(data[key] || {}).sort((a, b) => b[1] - a[1]);
  if (entries.length === 0) return '';

  const max = entries[0][1];
  const bars = entries
    .map(
      ([label, val]) => `
      <div class="bar-row">
        <div class="bar-label" title="${label}">${label}</div>
        <div class="bar-track">
          <div class="bar-fill" style="width:${((val / max) * 100).toFixed(1)}%"></div>
        </div>
        <div class="bar-value">${parseFloat(val).toFixed(1)}%</div>
      </div>`
    )
    .join('');

  return `
    <div class="card">
      <div class="card-title">${title}</div>
      ${bars}
    </div>`;
}

function renderAnalytics(data, el) {
  const html = SECTIONS.map((s) => renderSection(s, data)).join('');
  el.innerHTML =
    html ||
    '<p class="empty">No analytics data yet. Complete at least one job and set weights.</p>';
}

/** Fetch and render the analytics summary. */
export async function loadAnalytics() {
  const el = document.getElementById('analytics-content');
  el.innerHTML = loadingHTML();
  try {
    const data = await api('GET', '/analytics/summary');
    renderAnalytics(data, el);
  } catch (e) {
    renderError(el, e.message);
  }
}
