import { useState } from 'react';
import { checkAuth } from '../api';
import { setToken } from '../auth';

interface Props {
  onAuthenticated: () => void;
  /** Shown when a previously stored key stopped working. */
  expired?: boolean;
}

export function TokenGate({ onAuthenticated, expired }: Props) {
  const [value, setValue] = useState('');
  const [checking, setChecking] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!value.trim() || checking) return;
    setChecking(true);
    setError(null);

    setToken(value.trim());
    const ok = await checkAuth();
    setChecking(false);

    if (ok) {
      onAuthenticated();
    } else {
      setError('That key was rejected by the API.');
    }
  }

  return (
    <form className="card token-gate rise" onSubmit={submit}>
      <h2>API key required</h2>
      {expired && <p className="error-text">Your saved key is no longer accepted.</p>}
      <p className="muted small">
        This API is protected by a bearer token. Paste the key you were given — it is stored in
        this browser only and is never bundled into the app.
      </p>

      <label className="field">
        <span>API key</span>
        <input
          type="password"
          value={value}
          autoFocus
          autoComplete="off"
          placeholder="paste your key"
          onChange={(e) => setValue(e.target.value)}
          disabled={checking}
        />
      </label>

      {error && <p className="error-text">{error}</p>}

      <button type="submit" className="primary" disabled={!value.trim() || checking}>
        {checking ? 'Checking…' : 'Continue'}
      </button>
    </form>
  );
}
