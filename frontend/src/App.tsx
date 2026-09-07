import { useCallback, useEffect, useState } from 'react';
import { checkAuth } from './api';
import { clearToken, getToken } from './auth';
import { CreateJobForm } from './components/CreateJobForm';
import { Hero } from './components/Hero';
import { ProgressView } from './components/ProgressView';
import { ResultsView } from './components/ResultsView';
import { TokenGate } from './components/TokenGate';

type Stage = 'create' | 'progress' | 'results';
type Auth = 'checking' | 'needed' | 'expired' | 'ok';

/** A document with a scored corner. */
function Mark() {
  return (
    <svg width="30" height="30" viewBox="0 0 24 24" aria-hidden style={{ flexShrink: 0 }}>
      <rect width="24" height="24" rx="7" fill="currentColor" />
      <path d="M8 6h5.5L17 9.5V18H8z" fill="var(--bg)" />
      <path d="M13.5 6 17 9.5h-3.5z" fill="#fcd34d" />
      <rect x="9.6" y="12" width="5.8" height="1.3" rx="0.65" fill="#f59e0b" />
      <rect x="9.6" y="14.6" width="3.6" height="1.3" rx="0.65" fill="#f59e0b" />
    </svg>
  );
}

const COPY = {
  create: {
    badge: 'Explainable screening',
    title: (
      <>
        Ranked candidates,
        <br />
        and the reason for every score.
      </>
    ),
    lede: 'Keyword matching alone misses paraphrased experience. Embeddings alone miss hard requirements. Every score here shows both, plus the skills behind it.',
  },
  progress: {
    badge: 'Working',
    title: <>Reading the resumes.</>,
    lede: 'Each one is parsed, matched against the job description and embedded. A file that cannot be read fails on its own without stopping the batch.',
  },
  results: {
    badge: 'Done',
    title: <>The ranking, with its reasoning.</>,
    lede: 'Sort by any column. Green is a skill the job asked for and the candidate has; red is one they are missing.',
  },
} as const;

export default function App() {
  const [stage, setStage] = useState<Stage>('create');
  const [jobId, setJobId] = useState<string | null>(null);
  const [auth, setAuth] = useState<Auth>('checking');

  // One probe on load: with auth off (local dev) this passes with no key, so the
  // gate never appears and nothing has to be configured.
  useEffect(() => {
    let cancelled = false;
    checkAuth().then((ok) => {
      if (cancelled) return;
      setAuth(ok ? 'ok' : getToken() ? 'expired' : 'needed');
    });
    return () => {
      cancelled = true;
    };
  }, []);

  const onCreated = useCallback((id: string) => {
    setJobId(id);
    setStage('progress');
  }, []);

  const onDone = useCallback(() => setStage('results'), []);

  const reset = useCallback(() => {
    setJobId(null);
    setStage('create');
  }, []);

  const onUnauthorized = useCallback(() => {
    clearToken();
    setAuth('expired');
    reset();
  }, [reset]);

  const signOut = useCallback(() => {
    clearToken();
    setAuth('needed');
    reset();
  }, [reset]);

  const copy = COPY[stage];

  return (
    <div className="app">
      <header className="nav">
        <div className="nav__inner">
          {/* Home. A button rather than a link: there is no router here, so this
              returns to the first stage instead of navigating. */}
          <button
            type="button"
            className="nav__brand"
            onClick={reset}
            disabled={auth !== 'ok' || stage === 'create'}
            aria-label="Back to the start"
          >
            <Mark />
            Screening
          </button>

          <div className="nav__right">
            {auth === 'ok' && (
              <span className="nav__status">
                <span className="dot dot--live" />
                API connected
              </span>
            )}
            {auth === 'ok' && getToken() && (
              <button type="button" className="link-button small" onClick={signOut}>
                Forget key
              </button>
            )}
          </div>
        </div>
      </header>

      <main className="app-main">
        {auth === 'ok' && (
          <Hero
            scene={stage === 'create'}
            badge={copy.badge}
            title={copy.title}
            lede={copy.lede}
          />
        )}

        {auth === 'checking' && <div className="card muted">Connecting…</div>}

        {(auth === 'needed' || auth === 'expired') && (
          <TokenGate onAuthenticated={() => setAuth('ok')} expired={auth === 'expired'} />
        )}

        {auth === 'ok' && (
          <>
            {stage === 'create' && (
              <CreateJobForm onCreated={onCreated} onUnauthorized={onUnauthorized} />
            )}
            {stage === 'progress' && jobId && (
              <ProgressView
                jobId={jobId}
                onDone={onDone}
                onCancel={reset}
                onUnauthorized={onUnauthorized}
              />
            )}
            {stage === 'results' && jobId && (
              <ResultsView jobId={jobId} onStartOver={reset} onUnauthorized={onUnauthorized} />
            )}
          </>
        )}
      </main>

      <footer className="app-footer">
        <div className="app-footer__inner">
          <span>all-MiniLM-L6-v2 · pgvector · weights 0.6 skill / 0.4 semantic</span>
          <a href="/api/../docs" className="link-button">
            API docs
          </a>
        </div>
      </footer>
    </div>
  );
}
