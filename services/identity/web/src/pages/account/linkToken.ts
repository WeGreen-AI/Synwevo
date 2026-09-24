import { useEffect, useState } from 'react';

// Producer: the account route's AccountCapabilityGate, before RequireSession redirects.
const EMAIL_CHANGE_BRIDGE_KEY = 'synwevo.account.email-change.pending';

/** Capture a mail capability before removing it from history, including same-page links. */
export function useAccountToken(action?: string) {
  function read() {
    const fragment = new URLSearchParams(window.location.hash.slice(1));
    const params = fragment.has('token') ? fragment : new URLSearchParams(window.location.search);
    if (params.has('token')) return !action || params.get('action') === action ? params.get('token') || '' : '';
    if (action === 'email-change') {
      try {
        const pending = JSON.parse(sessionStorage.getItem(EMAIL_CHANGE_BRIDGE_KEY) || 'null');
        const age = Date.now() - pending?.captured_at;
        if (typeof pending?.token === 'string' && pending.token.length >= 20 && pending.token.length <= 256
          && typeof pending.captured_at === 'number' && age >= 0 && age <= 15 * 60 * 1000) return pending.token;
      } catch { /* Missing, malformed or unavailable storage is not a capability. */ }
    }
    return '';
  }
  const [token, setToken] = useState(read);
  useEffect(() => {
    function capture() {
      const next = read();
      // Effects, not state initializers, consume storage: StrictMode may initialize twice.
      if (action === 'email-change') {
        try { sessionStorage.removeItem(EMAIL_CHANGE_BRIDGE_KEY); } catch { /* No stored capability. */ }
      }
      if (next) {
        setToken(next);
        const url = new URL(window.location.href);
        const fragment = new URLSearchParams(url.hash.slice(1));
        fragment.delete('token'); fragment.delete('action');
        url.searchParams.delete('token'); url.searchParams.delete('action'); url.hash = fragment.toString();
        window.history.replaceState(window.history.state, '', url.pathname + url.search + url.hash);
      }
    }
    capture();
    window.addEventListener('hashchange', capture);
    window.addEventListener('popstate', capture);
    return () => {
      window.removeEventListener('hashchange', capture);
      window.removeEventListener('popstate', capture);
    };
  }, [action]);
  return token;
}
