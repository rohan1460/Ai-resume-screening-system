import { useCallback, useEffect, useState } from 'react';
import { UnauthorizedError, getResults, rerank } from '../api';
import type { JobResults } from '../types';
import { ResultsTable } from './ResultsTable';
import { SkillChips } from './SkillChips';

interface Props {
  jobId: string;
  onStartOver: () => void;
  onUnauthorized: () => void;
}

export function ResultsView({ jobId, onStartOver, onUnauthorized }: Props) {
  const [data, setData] = useState<JobResults | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [rerankOpen, setRerankOpen] = useState(false);
  const [newJd, setNewJd] = useState('');
  const [reranking, setReranking] = useState(false);
  const [rerankCount, setRerankCount] = useState(0);

  const load = useCallback(async () => {
    try {
      setData(await getResults(jobId));
    } catch (err) {
      if (err instanceof UnauthorizedError) {
        onUnauthorized();
        return;
      }
      setError(err instanceof Error ? err.message : 'Could not load results');
    }
  }, [jobId, onUnauthorized]);

  useEffect(() => {
    load();
  }, [load]);

  async function submitRerank(event: React.FormEvent) {
    event.preventDefault();
    if (!newJd.trim() || reranking) return;
    setReranking(true);
    setError(null);
    try {
      setData(await rerank(jobId, newJd));
      setRerankCount((n) => n + 1);
      setRerankOpen(false);
      setNewJd('');
    } catch (err) {
      if (err instanceof UnauthorizedError) {
        onUnauthorized();
        return;
      }
      setError(err instanceof Error ? err.message : 'Re-rank failed');
    } finally {
      setReranking(false);
    }
  }

  if (error && !data) {
    return (
      <div className="card">
        <p className="error-text">{error}</p>
        <button type="button" onClick={onStartOver}>
          Start over
        </button>
      </div>
    );
  }

  if (!data) {
    return <div className="card muted">Loading results…</div>;
  }

  return (
    <div className="stack">
      <div className="card">
        <div className="results-header">
          <div>
            <h2>Ranked candidates</h2>
            <p className="muted small">
              {data.processed_resumes} scored
              {data.failed_resumes > 0 && ` · ${data.failed_resumes} failed`}
              {rerankCount > 0 && ` · re-ranked ${rerankCount}×`}
            </p>
          </div>
          <div className="actions">
            <button type="button" onClick={() => setRerankOpen((v) => !v)}>
              {rerankOpen ? 'Cancel' : 'Re-rank with a new JD'}
            </button>
            <button type="button" onClick={onStartOver}>
              New job
            </button>
          </div>
        </div>

        {rerankOpen && (
          <form className="rerank-form" onSubmit={submitRerank}>
            <label className="field">
              <span>New job description</span>
              <textarea
                rows={6}
                value={newJd}
                autoFocus
                placeholder="Score the same candidates against a different role…"
                onChange={(e) => setNewJd(e.target.value)}
                disabled={reranking}
              />
            </label>
            <p className="muted small">
              Resumes are not re-parsed or re-embedded — only the new JD is, so this is
              near-instant.
            </p>
            <button type="submit" className="primary" disabled={!newJd.trim() || reranking}>
              {reranking ? 'Re-ranking…' : 'Re-rank'}
            </button>
          </form>
        )}

        {error && <p className="error-text">{error}</p>}

        <div className="required-skills">
          <span className="muted small">Required by the job description:</span>{' '}
          <SkillChips skills={data.required_skills} variant="matched" limit={40} />
        </div>

        <div className="formula">
          final_score = <strong>{data.w_skill}</strong> × skill_score +{' '}
          <strong>{data.w_semantic}</strong> × semantic_score
        </div>
      </div>

      <div className="card">
        <ResultsTable
          results={data.results}
          weights={{ skill: data.w_skill, semantic: data.w_semantic }}
        />
      </div>

      {data.failures.length > 0 && (
        <div className="card">
          <h3>Could not be processed</h3>
          <ul className="failure-list">
            {data.failures.map((failure) => (
              <li key={failure.filename}>
                <span className="mono">{failure.filename}</span>
                <span className="error-text small">{failure.error}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
