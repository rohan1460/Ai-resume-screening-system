import { useEffect, useState } from 'react';
import { UnauthorizedError, createJob } from '../api';
import { FileDropzone } from './FileDropzone';
import { SAMPLE_JDS } from '../sampleJds';

interface Props {
  onCreated: (jobId: string) => void;
  onUnauthorized: () => void;
}

export function CreateJobForm({ onCreated, onUnauthorized }: Props) {
  const [title, setTitle] = useState('');
  const [jdText, setJdText] = useState('');
  const [jdFile, setJdFile] = useState<File | null>(null);
  const [resumes, setResumes] = useState<File[]>([]);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [pasteHint, setPasteHint] = useState<string | null>(null);
  const [sampleOpen, setSampleOpen] = useState(false);

  // A menu should close on Escape and on a click anywhere else.
  useEffect(() => {
    if (!sampleOpen) return;
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && setSampleOpen(false);
    const onClick = () => setSampleOpen(false);
    window.addEventListener('keydown', onKey);
    // Deferred, or the click that opened the menu would immediately close it.
    const id = window.setTimeout(() => window.addEventListener('click', onClick), 0);
    return () => {
      window.removeEventListener('keydown', onKey);
      window.removeEventListener('click', onClick);
      window.clearTimeout(id);
    };
  }, [sampleOpen]);

  async function pasteFromClipboard() {
    try {
      const text = await navigator.clipboard.readText();
      if (!text.trim()) {
        setPasteHint('Your clipboard is empty.');
        return;
      }
      setJdText(text);
      setPasteHint(null);
    } catch {
      // Permission denied, or a browser without the async clipboard API.
      setPasteHint('Your browser blocked clipboard access — use ⌘V in the box instead.');
    }
  }

  const hasJd = jdText.trim().length > 0 || jdFile !== null;
  const jdFileDisabled = submitting || jdText.trim().length > 0;
  const canSubmit = hasJd && resumes.length > 0 && !submitting;

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!canSubmit) return;
    setSubmitting(true);
    setError(null);
    try {
      const job = await createJob({
        title,
        jdText,
        jdFile: jdFile ?? undefined,
        resumes,
      });
      onCreated(job.job_id);
    } catch (err) {
      if (err instanceof UnauthorizedError) {
        onUnauthorized();
        return;
      }
      setError(err instanceof Error ? err.message : 'Something went wrong');
      setSubmitting(false);
    }
  }

  return (
    <form className="card" onSubmit={submit}>
      <h2>New screening job</h2>

      <label className="field">
        <span>Job title (optional)</span>
        <input
          type="text"
          value={title}
          placeholder="Senior Backend Engineer"
          onChange={(e) => setTitle(e.target.value)}
          disabled={submitting}
        />
      </label>

      <div className="field">
        <div className="field__header">
          <span>Job description</span>
          <div className="field__actions">
            <button
              type="button"
              className="chip-button"
              onClick={pasteFromClipboard}
              disabled={submitting || jdFile !== null}
            >
              Paste
            </button>
            <div className="sample-menu">
              <button
                type="button"
                className="chip-button"
                aria-haspopup="menu"
                aria-expanded={sampleOpen}
                onClick={() => setSampleOpen((v) => !v)}
                disabled={submitting || jdFile !== null}
              >
                Use sample <span className="chip-button__caret">▾</span>
              </button>

              {sampleOpen && (
                <div className="sample-menu__list" role="menu">
                  {SAMPLE_JDS.map((sample) => (
                    <button
                      key={sample.id}
                      type="button"
                      role="menuitem"
                      className="sample-menu__item"
                      onClick={() => {
                        setJdText(sample.text);
                        if (!title.trim()) setTitle(sample.title);
                        setPasteHint(null);
                        setSampleOpen(false);
                      }}
                    >
                      <span className="sample-menu__label">{sample.label}</span>
                      <span className="sample-menu__hint">{sample.hint}</span>
                    </button>
                  ))}
                </div>
              )}
            </div>
            {jdText.length > 0 && (
              <button
                type="button"
                className="chip-button"
                onClick={() => setJdText('')}
                disabled={submitting}
              >
                Clear
              </button>
            )}
          </div>
        </div>
        <textarea
          rows={10}
          value={jdText}
          placeholder="Paste the job description here…"
          onChange={(e) => setJdText(e.target.value)}
          disabled={submitting || jdFile !== null}
        />
        {pasteHint && <p className="muted small">{pasteHint}</p>}
        {jdText.trim().length > 0 && (
          <p className="muted small">{jdText.trim().split(/\s+/).length} words</p>
        )}
      </div>

      <div className="field">
        <span>…or upload it as a file</span>
        <div className="jd-file-row">
          {/* The native control cannot be styled, so it is hidden behind a label —
              which keeps the click target and keyboard behaviour the browser gives
              us for free. */}
          <label className={`file-button${jdFileDisabled ? ' file-button--disabled' : ''}`}>
            <span className="file-button__icon" aria-hidden>
              <svg width="15" height="15" viewBox="0 0 24 24" fill="none">
                <path
                  d="M12 16V4m0 0L7.5 8.5M12 4l4.5 4.5"
                  stroke="currentColor"
                  strokeWidth="2.2"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                />
                <path
                  d="M4 15v3a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-3"
                  stroke="currentColor"
                  strokeWidth="2.2"
                  strokeLinecap="round"
                />
              </svg>
            </span>
            {jdFile ? 'Replace file' : 'Choose file'}
            <input
              type="file"
              accept=".pdf,.docx,.txt"
              disabled={jdFileDisabled}
              onChange={(e) => setJdFile(e.target.files?.[0] ?? null)}
            />
          </label>

          {jdFile ? (
            <span className="file-chosen">
              <span className="file-chosen__name mono">{jdFile.name}</span>
              <button
                type="button"
                className="file-chosen__clear"
                onClick={() => setJdFile(null)}
                aria-label={`Remove ${jdFile.name}`}
              >
                ×
              </button>
            </span>
          ) : (
            <span className="muted small">No file chosen</span>
          )}
        </div>
        <p className="muted small">
          Paste or upload — whichever you fill in disables the other.
        </p>
      </div>

      <div className="field">
        <span>Resumes</span>
        <FileDropzone files={resumes} onChange={setResumes} disabled={submitting} />
      </div>

      {error && <p className="error-text">{error}</p>}

      <div className="actions">
        <button type="submit" className="primary" disabled={!canSubmit}>
          {submitting
            ? 'Uploading…'
            : `Screen ${resumes.length || ''} resume${resumes.length === 1 ? '' : 's'}`}
        </button>
        {!hasJd && <span className="muted small">Add a job description to continue</span>}
        {hasJd && resumes.length === 0 && (
          <span className="muted small">Add at least one resume</span>
        )}
      </div>
    </form>
  );
}
