import { getToken } from './auth';
import type { JobCreated, JobProgress, JobResults } from './types';

const BASE = '/api';

/** Raised on 401 so the UI can drop a stale key and re-prompt. */
export class UnauthorizedError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'UnauthorizedError';
  }
}

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
    this.name = 'ApiError';
  }
}

/** FastAPI returns `detail` as a string for HTTPException and as a list for 422. */
async function readError(response: Response): Promise<string> {
  try {
    const body = await response.json();
    const detail = body?.detail;
    if (typeof detail === 'string') return detail;
    if (Array.isArray(detail)) {
      return detail
        .map((d: { loc?: unknown[]; msg?: string }) =>
          d.loc ? `${d.loc.slice(1).join('.')}: ${d.msg}` : d.msg,
        )
        .join('; ');
    }
  } catch {
    /* fall through to the status text */
  }
  return response.statusText || `Request failed (${response.status})`;
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const token = getToken();
  const headers = new Headers(init.headers);
  // Sent only when a key is stored: a local API with auth off needs no header.
  if (token) headers.set('Authorization', `Bearer ${token}`);

  const response = await fetch(`${BASE}${path}`, { ...init, headers });
  if (!response.ok) {
    const message = await readError(response);
    if (response.status === 401) throw new UnauthorizedError(message);
    throw new ApiError(message, response.status);
  }
  return (await response.json()) as T;
}

/** Probe whether the API accepts the stored key. Used by the token gate. */
export async function checkAuth(): Promise<boolean> {
  try {
    await request('/jobs/00000000-0000-0000-0000-000000000000');
    return true;
  } catch (err) {
    if (err instanceof UnauthorizedError) return false;
    // Anything else (404 for the nonexistent job, a network blip) means the key
    // was accepted or is not the problem.
    return true;
  }
}

export interface CreateJobInput {
  title?: string;
  jdText?: string;
  jdFile?: File;
  resumes: File[];
}

export function createJob({ title, jdText, jdFile, resumes }: CreateJobInput) {
  const form = new FormData();
  if (title?.trim()) form.append('title', title.trim());
  if (jdText?.trim()) form.append('jd_text', jdText.trim());
  if (jdFile) form.append('jd_file', jdFile);
  resumes.forEach((file) => form.append('resumes', file));
  return request<JobCreated>('/jobs', { method: 'POST', body: form });
}

export function getJob(jobId: string) {
  return request<JobProgress>(`/jobs/${jobId}`);
}

export function getResults(jobId: string) {
  return request<JobResults>(`/jobs/${jobId}/results`);
}

export function rerank(jobId: string, jdText: string) {
  return request<JobResults>(`/jobs/${jobId}/rerank`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ jd_text: jdText }),
  });
}
