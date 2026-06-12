/**
 * Thin wrapper around `fetch` for authenticated calls to the Fintrack API.
 * Injects the bearer token, serialises JSON bodies, and normalises errors.
 */
import { CONFIG } from './config.js';
import { getToken } from './session.js';

export async function api(method, path, body) {
  const opts = {
    method,
    headers: {
      Authorization: `Bearer ${getToken()}`,
      'Content-Type': 'application/json',
    },
  };
  if (body !== undefined) opts.body = JSON.stringify(body);

  const res = await fetch(CONFIG.apiBase + path, opts);
  if (res.status === 204) return null;

  const data = await res.json();
  if (!res.ok) throw new Error(data.message || `HTTP ${res.status}`);
  return data;
}
