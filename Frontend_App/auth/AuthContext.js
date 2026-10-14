import React, { createContext, useContext, useState, useEffect } from 'react';
import { Platform } from 'react-native';
import { getItem, setItem, deleteItem } from './secureStore';
import { api, setAccessToken, applySavedBackend, onSessionExpired } from '../api/client';
import { clearReviewQueueCache, bumpReviewCacheSessionId } from '../offline/queue';

const ACCESS_KEY = 'nn_access';

const AuthContext = createContext(null);

// Decode the (unverified, presentation-only) JWT payload. atob is missing on
// Hermes, and a malformed token must never crash startup — so manual base64
// fallback + try/catch, with an expiry check so a stale token reads logged-out.
function decodeRole(token) {
  try {
    const part = String(token || '').split('.')[1];
    if (!part) return null;
    const b64 = part.replace(/-/g, '+').replace(/_/g, '/');
    const padded = b64 + '='.repeat((4 - (b64.length % 4)) % 4);
    let json;
    if (typeof atob === 'function') {
      json = atob(padded);
    } else {
      // Minimal base64 decode for Hermes (ASCII JSON payload only).
      const chars = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/=';
      let out = '';
      let i = 0;
      const clean = padded.replace(/[^A-Za-z0-9+/=]/g, '');
      while (i < clean.length) {
        const e1 = chars.indexOf(clean.charAt(i++));
        const e2 = chars.indexOf(clean.charAt(i++));
        const e3 = chars.indexOf(clean.charAt(i++));
        const e4 = chars.indexOf(clean.charAt(i++));
        const n = (e1 << 18) | (e2 << 12) | ((e3 & 63) << 6) | (e4 & 63);
        out += String.fromCharCode((n >> 16) & 255, (n >> 8) & 255, n & 255);
      }
      json = out.replace(/\0+$/, '');
    }
    const payload = JSON.parse(json);
    if (payload?.exp && payload.exp * 1000 < Date.now()) return null;
    return payload?.role ?? null;
  } catch {
    return null;
  }
}

export function AuthProvider({ children }) {
  const [role, setRole] = useState(null);
  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    // A second 401 (or a failed refresh) means the session is dead — the API
    // layer broadcasts it; drop role so the app routes back to Login.
    const off = onSessionExpired(() => {
      try { bumpReviewCacheSessionId(); } catch {}
      try { clearReviewQueueCache(); } catch {}
      setRole(null);
      deleteItem(ACCESS_KEY);
    });
    (async () => {
      // try/finally guarantees isLoading is cleared even if storage or the
      // network call throws — otherwise the app hangs on a blank screen.
      try {
        // Restore the target backend BEFORE the first request
        await applySavedBackend();
        // Native has no cookie jar (the refresh cookie is web-only), so
        // silently rehydrate the last access token from SecureStore and set
        // the header before any screen renders.
        try {
          // Web never persisted an access token (memory-only per Backend.md
          // C8 / 09 §3.2 — secureStore falls back to localStorage on web,
          // which is XSS-readable). The web session comes from the httpOnly
          // refresh cookie in the /auth/refresh call below.
          if (Platform.OS !== 'web') {
            const saved = await getItem(ACCESS_KEY);
            if (saved) {
              setAccessToken(saved);
              const r = decodeRole(saved);
              if (r) {
                setRole(r);
                setIsLoading(false);
                // Best-effort refresh in the background to extend the session.
                api.post('/auth/refresh', undefined, { timeout: 10000 })
                  .then(({ data }) => {
                    if (data?.access_token) {
                      setAccessToken(data.access_token);
                      setItem(ACCESS_KEY, data.access_token);
                      setRole(decodeRole(data.access_token));
                    }
                  })
                  .catch(() => {});
                return;
              }
            }
          }
        } catch { /* fall through to login */ }

        // The refresh token is an httpOnly cookie set by POST /auth/login.
        // On web builds, axios sends this cookie automatically (withCredentials).
        // On native (iOS/Android), there is NO cookie jar, so if there was no
        // saved access token above, there is no active session — do not block
        // startup on a hopeless network call.
        if (Platform.OS === 'web') {
          try {
            const { data } = await api.post('/auth/refresh', undefined, { timeout: 3000 });
            if (data?.access_token) {
              setAccessToken(data.access_token);
              setItem(ACCESS_KEY, data.access_token);
              setRole(decodeRole(data.access_token));
            }
          } catch {
            /* no valid web cookie session */
          }
        }
      } catch {
        /* no valid session — fall through to the login screen */
      } finally {
        setIsLoading(false);
      }
    })();
    return off;
  }, []);

  const login = async (employee_id, password) => {
    const install_id = await getItem('nn_install_id') || undefined;
    // 60s, not the shared 30s default: this is the one request that can land on
    // a Render free-tier service that has been spun down, and the first login of
    // a demo is exactly when that happens. A cold start measured at 30-50s would
    // otherwise still time out and show "cannot reach the backend" for a backend
    // that is healthy.
    const { data } = await api.post('/auth/login', { employee_id, password, install_id }, { timeout: 60000 });
    try { bumpReviewCacheSessionId(); } catch {}
    try { clearReviewQueueCache(null, { legacyOnly: true }); } catch {}
    setAccessToken(data.access_token);
    await setItem('nn_install_id', data.install_id);
    // Native session restore: the refresh cookie never reaches SecureStore
    // (httpOnly, web-only), so persist the access token for silent rehydrate
    // on boot (see the bootstrap effect above). WEB DOES NOT PERSIST: the
    // fallback store is localStorage (see secureStore.js), which contradicts
    // the documented memory-only access token (Backend.md C8, 09 T12 XSS) —
    // on web the httpOnly refresh cookie restores the session instead.
    if (Platform.OS !== 'web') {
      await setItem(ACCESS_KEY, data.access_token);
    }
    // The refresh token is only ever set as an httpOnly cookie by the server —
    // it is never written to SecureStore, which is the point of the cookie.
    setRole(decodeRole(data.access_token));
  };

  const logout = async () => {
    try { await api.post('/auth/logout'); } catch {}
    try { await clearReviewQueueCache(); } catch {}
    try { bumpReviewCacheSessionId(); } catch {}
    // nn_install_id is DEVICE identity, not session state — keep it so the
    // next login on this phone re-binds the same install record. Only the
    // in-memory token + role are cleared here (refresh cookie is cleared
    // server-side by /auth/logout).
    setAccessToken(null);
    await deleteItem(ACCESS_KEY);
    setRole(null);
  };

  return <AuthContext.Provider value={{ role, login, logout, isLoading }}>{children}</AuthContext.Provider>;
}

export const useAuth = () => useContext(AuthContext);
