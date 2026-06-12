/**
 * View routing — toggles the login/app shells and the sidebar sections.
 */
import { loadJobs } from './jobs.js';
import { loadWeights } from './weights.js';
import { loadAnalytics } from './analytics.js';

const SECTIONS = ['jobs', 'upload', 'weights', 'analytics'];

/** Lazy-loaders run when a section becomes visible. */
const ON_SHOW = {
  weights: loadWeights,
  analytics: loadAnalytics,
};

export function showSection(name) {
  SECTIONS.forEach((s) => {
    document.getElementById(`section-${s}`).classList.toggle('active', s === name);
    document.getElementById(`nav-${s}`).classList.toggle('active', s === name);
  });
  ON_SHOW[name]?.();
}

/** Switch from the login screen to the app and load the default view. */
export function showApp() {
  document.getElementById('login-page').style.display = 'none';
  document.getElementById('app-shell').style.display = 'flex';
  showSection('jobs');
  loadJobs();
}

/** Return to the login screen. */
export function showLogin() {
  document.getElementById('app-shell').style.display = 'none';
  document.getElementById('login-page').style.display = 'flex';
}

/** Wire up the sidebar navigation buttons. */
export function initNavigation() {
  SECTIONS.forEach((s) => {
    document.getElementById(`nav-${s}`).addEventListener('click', () => showSection(s));
  });
}
