/**
 * Small shared UI helpers: toast notifications, inline errors, and spinners.
 */

/** Reusable inline spinner markup. */
export const spinnerHTML = '<span class="spinner"></span>';

/** Loading row with a spinner and a label. */
export const loadingHTML = (label = 'Loading…') =>
  `<div class="loading">${spinnerHTML}<span>${label}</span></div>`;

/** Render an error message into a container element. */
export function renderError(el, message) {
  el.innerHTML = `<p class="error-msg">${message}</p>`;
}

/** Show an inline form error element. */
export function showFieldError(el, message) {
  el.textContent = message;
  el.style.display = 'block';
}

export function hideFieldError(el) {
  el.style.display = 'none';
}

/** Transient toast notification, bottom-right. */
let toastTimer;
export function toast(message, isError = false) {
  const el = document.getElementById('toast');
  el.textContent = message;
  el.classList.toggle('error', isError);
  el.classList.add('show');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove('show'), 3500);
}
