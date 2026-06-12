/**
 * Application entry point. Wires up every view's event listeners once the DOM
 * is ready. Individual views fetch their own data when first shown.
 */
import { initAuth } from './auth.js';
import { initNavigation } from './navigation.js';
import { initJobs } from './jobs.js';
import { initUpload } from './upload.js';
import { initFunds } from './funds.js';

function init() {
  initAuth();
  initNavigation();
  initJobs();
  initUpload();
  initFunds();
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', init);
} else {
  init();
}
