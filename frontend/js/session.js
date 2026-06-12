/**
 * In-memory session token store.
 *
 * The Cognito IdToken is deliberately kept in memory only (never persisted to
 * localStorage / cookies), so it is cleared on refresh or sign-out.
 */
let idToken = null;

export const getToken = () => idToken;

export const setToken = (token) => {
  idToken = token;
};

export const clearToken = () => {
  idToken = null;
};
