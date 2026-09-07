/**
 * API-key storage.
 *
 * The key lives in this browser's localStorage, entered by the person using the app.
 * It is deliberately NOT a build-time variable: anything baked into the bundle is
 * readable by everyone who loads the page, which would make it not a secret at all.
 */

const STORAGE_KEY = 'resume-screening.api-key';

export function getToken(): string | null {
  try {
    return window.localStorage.getItem(STORAGE_KEY);
  } catch {
    // Private mode and some embedded browsers throw on access.
    return null;
  }
}

export function setToken(token: string): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, token);
  } catch {
    /* the in-memory session still works for this tab */
  }
}

export function clearToken(): void {
  try {
    window.localStorage.removeItem(STORAGE_KEY);
  } catch {
    /* nothing to do */
  }
}
