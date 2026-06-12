/**
 * Shared client-side cache of the user's portfolio jobs.
 *
 * Several views (Jobs, Weights, Upload, Funds) read and refresh the same list,
 * so it lives in one place rather than being threaded through each module.
 */
import { api } from './api.js';

let allJobs = [];

export const getJobs = () => allJobs;

export const setJobs = (jobs) => {
  allJobs = jobs || [];
};

/** Re-fetch the job list from the backend and update the cache. */
export async function refreshJobs() {
  allJobs = await api('GET', '/upload');
  return allJobs;
}

export const getCompletedJobs = () => allJobs.filter((j) => j.status === 'completed');
