import { useEffect, useState } from 'react';
import { UnauthorizedError, getJob } from '../api';
import type { JobProgress } from '../types';

const POLL_INTERVAL_MS = 800;

interface Props {
  jobId: string;
  onDone: () => void;
  onCancel: () => void;
  onUnauthorized: () => void;
}

export function ProgressView({ jobId, onDone, onCancel, onUnauthorized }: Props) {
  const [job, setJob] = useState<JobProgress | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    let timer: number | undefined;

    async function poll() {
      try {
        const next = await getJob(jobId);
        if (cancelled) return;
        setJob(next);
        if (next.status === 'completed' || next.status === 'failed') {
          onDone();
          return;
        }
      } catch (err) {
        if (cancelled) return;
        if (err instanceof UnauthorizedError) {
          onUnauthorized();
          return; // stop polling rather than hammering a rejected key
        }
        setError(err instanceof Error ? err.message : 'Lost contact with the API');
      }
      // Chained timeout rather than setInterval: a slow response can never stack
      // requests on top of each other.
      timer = window.setTimeout(poll, POLL_INTERVAL_MS);
    }

    poll();
    return () => {
      cancelled = true;
      if (timer) window.clearTimeout(timer);
    };
  }, [jobId, onDone, onUnauthorized]);

  const done = job ? job.processed_resumes + job.failed_resumes : 0;
  const percent = job ? Math.round(job.progress * 100) : 0;

  return (
    <div className="card">
      <h2>Screening in progress</h2>
      <p className="muted small mono">{jobId}</p>

      <div className="progress-bar" role="progressbar" aria-valuenow={percent}>
        <div className="progress-bar__fill" style={{ width: `${percent}%` }} />
      </div>

      <p className="progress-line">
        <span className={`status status--${job?.status ?? 'queued'}`}>
          {job?.status ?? 'queued'}
        </span>
        {job && (
          <>
            <strong>
              {done} / {job.total_resumes}
            </strong>
            <span className="muted">resumes</span>
            {job.failed_resumes > 0 && (
              <span className="error-text small">{job.failed_resumes} failed</span>
            )}
          </>
        )}
      </p>

      {job?.status === 'queued' && (
        <p className="muted small">Waiting for a worker to pick the job up…</p>
      )}
      {error && <p className="error-text">{error}</p>}

      <div className="actions">
        <button type="button" onClick={onCancel}>
          Start over
        </button>
      </div>
    </div>
  );
}
