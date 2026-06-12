/**
 * Add-by-ISIN view — look up an ETF/fund on JustETF and add it to the portfolio.
 *
 * Flow: POST /funds/{isin}/portfolio scrapes JustETF (or serves a cached
 * snapshot) and adds the fund as a `completed` item with holdings + exposure.
 */
import { api } from './api.js';
import { refreshJobs } from './store.js';
import { toast } from './ui.js';

const ISIN_LENGTH = 12;

function getIsin() {
  return document.getElementById('isin-input').value.trim().toUpperCase();
}

async function doAddFund() {
  const input = document.getElementById('isin-input');
  const btn = document.getElementById('add-fund-btn');
  const status = document.getElementById('add-fund-status');
  const isin = getIsin();

  if (isin.length !== ISIN_LENGTH) {
    toast('Enter a valid 12-character ISIN.', true);
    return;
  }

  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span> Looking up…';
  status.textContent = '';

  try {
    const res = await api('POST', `/funds/${encodeURIComponent(isin)}/portfolio`);
    toast(`Added ${res.isin} to your portfolio${res.cached ? ' (from cache)' : ''}.`);
    input.value = '';
    btn.textContent = 'Add fund';
    btn.disabled = true;

    // The fund is already completed — refresh so it shows immediately.
    await refreshJobs();
    document.dispatchEvent(new CustomEvent('jobs:changed'));
  } catch (e) {
    toast(e.message, true);
    btn.disabled = false;
    btn.textContent = 'Add fund';
  }
}

/** Wire up the ISIN input and Add-fund button. */
export function initFunds() {
  const input = document.getElementById('isin-input');
  const btn = document.getElementById('add-fund-btn');

  input.addEventListener('input', () => {
    btn.disabled = getIsin().length !== ISIN_LENGTH;
  });
  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') doAddFund();
  });
  btn.addEventListener('click', doAddFund);
}
