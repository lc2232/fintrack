/**
 * Authentication — Cognito USER_PASSWORD_AUTH via the REST endpoint.
 * On success the IdToken is held in memory (see session.js) and the app shown.
 */
import { CONFIG } from './config.js';
import { setToken, clearToken } from './session.js';
import { setJobs } from './store.js';
import { showApp, showLogin } from './navigation.js';
import { showFieldError, hideFieldError } from './ui.js';

async function login() {
  const email = document.getElementById('login-email').value.trim();
  const password = document.getElementById('login-password').value;
  const btn = document.getElementById('login-btn');
  const err = document.getElementById('login-error');

  hideFieldError(err);
  if (!email || !password) {
    showFieldError(err, 'Please enter your email and password.');
    return;
  }

  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span> Signing in…';

  try {
    const res = await fetch(`https://cognito-idp.${CONFIG.cognitoRegion}.amazonaws.com/`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/x-amz-json-1.1',
        'X-Amz-Target': 'AWSCognitoIdentityProviderService.InitiateAuth',
      },
      body: JSON.stringify({
        AuthFlow: 'USER_PASSWORD_AUTH',
        ClientId: CONFIG.clientId,
        AuthParameters: { USERNAME: email, PASSWORD: password },
      }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.message || data.__type || 'Login failed');

    setToken(data.AuthenticationResult.IdToken);
    showApp();
  } catch (e) {
    showFieldError(err, e.message);
    btn.disabled = false;
    btn.textContent = 'Sign in';
  }
}

function signOut() {
  clearToken();
  setJobs([]);
  showLogin();
  document.getElementById('login-password').value = '';
}

/** Wire up the login form and sign-out button. */
export function initAuth() {
  document.getElementById('login-btn').addEventListener('click', login);
  document.getElementById('signout-btn').addEventListener('click', signOut);

  // Submit on Enter from either credential field.
  ['login-email', 'login-password'].forEach((id) => {
    document.getElementById(id).addEventListener('keydown', (e) => {
      if (e.key === 'Enter') login();
    });
  });
}
