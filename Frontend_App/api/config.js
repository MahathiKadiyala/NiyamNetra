// ============================================================================
// NIYAMNETRA BACKEND CONFIGURATION (EDIT HERE BEFORE RUNNING)
// ============================================================================
// 1. Target mode: 'lan' for local laptop on Wi-Fi, or 'render' for cloud backend
export const ACTIVE_BACKEND = 'render'; // 'lan' | 'render'

// 2. Production API Base URL
export const RENDER_API_URL = 'https://niyamnetra-backend.onrender.com';
export const API_PORT = 8000;
// ============================================================================

let Platform = { OS: 'android' };
let NativeModules = {};
try {
  // eslint-disable-next-line global-require
  const rn = require('react-native');
  if (rn.Platform) Platform = rn.Platform;
  if (rn.NativeModules) NativeModules = rn.NativeModules;
} catch {
  // Node.js or test environment fallback
  if (typeof window !== 'undefined' && window.location) {
    Platform = { OS: 'web' };
  } else if (typeof process !== 'undefined' && process.env && process.env.EXPO_OS) {
    Platform = { OS: process.env.EXPO_OS };
  }
}

export const EVIDENCE_MIN_FREE_GB = 5;
export const EVIDENCE_MIN_FREE_BYTES = EVIDENCE_MIN_FREE_GB * 1024 ** 3;
export const LOCAL_API_URL = Platform.OS === 'android' ? `http://10.0.2.2:${API_PORT}` : `http://127.0.0.1:${API_PORT}`;
export const LAPTOP_WIFI_IP = '';

// ---------------------------------------------------------------------------
// Screen capture / recording policy for the field app.
// ---------------------------------------------------------------------------
// true  = screenshots and screen recording always work (the SIH demo is
//         recorded, so this is the shipping setting). Nothing in the app sets
//         Android FLAG_SECURE while this is true - see
//         hooks/useAllowScreenCapture.js and plugins/withAllowScreenCapture.js.
// false = deliberate opt-in to blocking capture: on Android the app would be
//         recorded as a black screen and OEM recorders show "cannot record due
//         to security reasons". Never set this for a demo build.
export const ALLOW_SCREEN_CAPTURE = true;

/**
 * Normalizes a backend address or URL into a complete, valid origin URL.
 * Handles:
 * - Already-schemed URLs (https://... or http://...)
 * - Hostnames and domain names
 * - IPv4 and bracketed IPv6 addresses
 * - Hosts that already contain a port (avoids duplicate ports)
 * - Trailing slashes
 * Rejects:
 * - Empty or whitespace-only strings
 * - Malformed hosts (consecutive dots, trailing dots)
 * - Out-of-range port numbers (< 1 or > 65535)
 * - Query parameters and fragments (preventing secret leakage)
 */
export function normalizeBackendUrl(input, defaultPort = API_PORT) {
  let s = String(input || '').trim().replace(/\/+$/, '');
  if (!s) return null;
  // Disallow query parameters and fragments in API base URLs
  if (s.includes('?') || s.includes('#')) return null;

  if (!/^https?:\/\//i.test(s)) {
    s = `http://${s}`;
  }

  const m = /^(https?):\/\/(\[[0-9a-fA-F:]+\]|[A-Za-z0-9](?:[A-Za-z0-9\-]*[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9\-]*[A-Za-z0-9])?)*)(?::(\d+))?(?:\/.*)?$/i.exec(s);
  if (!m) return null;

  const scheme = m[1].toLowerCase();
  const host = m[2];
  if (/\.$|\.\./.test(host)) return null;

  let port = m[3];
  if (port !== undefined) {
    const n = Number(port);
    if (!Number.isInteger(n) || n < 1 || n > 65535) return null;
  } else if (scheme !== 'https') {
    port = defaultPort ? String(defaultPort) : '';
  } else {
    port = '';
  }

  return `${scheme}://${host}${port ? `:${port}` : ''}`;
}

/**
 * Resolves the LAN developer-machine backend host using supported project mechanisms:
 * 1. Explicit options host (for testing or runtime override)
 * 2. EXPO_PUBLIC_LAN_HOST or REACT_NATIVE_PACKAGER_HOSTNAME environment variables
 * 3. User-configured LAPTOP_WIFI_IP (if populated in config.js)
 * 4. Expo Constants (debuggerHost or hostUri from Metro dev server)
 * 5. NativeModules.SourceCode.scriptURL (Metro bundle host in development)
 * 6. Web platform window.location.hostname
 * 7. Android emulator (10.0.2.2) when explicitly running in emulator mode
 */
export function resolveLanHost(options = {}) {
  const env = options.env || (typeof process !== 'undefined' ? process.env : {}) || {};

  // 1. Explicit override option
  if (options.host) {
    return String(options.host).trim();
  }

  // 2. Explicit development environment variable
  const envLanHost = (env.EXPO_PUBLIC_LAN_HOST || env.REACT_NATIVE_PACKAGER_HOSTNAME || '').trim();
  if (envLanHost) {
    return envLanHost;
  }

  // 3. User-configured LAPTOP_WIFI_IP in config.js
  const configuredWifiIp = options.laptopWifiIp !== undefined ? options.laptopWifiIp : LAPTOP_WIFI_IP;
  if (configuredWifiIp && String(configuredWifiIp).trim()) {
    return String(configuredWifiIp).trim();
  }

  // 4. Expo Constants (Metro dev server host)
  if (options.expoConstants !== undefined) {
    const c = options.expoConstants;
    const hostUri = c?.expoConfig?.hostUri
      || c?.manifest2?.extra?.expoGo?.debuggerHost
      || c?.manifest?.debuggerHost;
    if (hostUri && typeof hostUri === 'string') {
      return hostUri.split(':')[0];
    }
  } else {
    try {
      // eslint-disable-next-line global-require
      const Constants = require('expo-constants').default || require('expo-constants');
      const hostUri = Constants?.expoConfig?.hostUri
        || Constants?.manifest2?.extra?.expoGo?.debuggerHost
        || Constants?.manifest?.debuggerHost;
      if (hostUri && typeof hostUri === 'string') {
        return hostUri.split(':')[0];
      }
    } catch { /* expo-constants not available */ }
  }

  // 5. NativeModules.SourceCode.scriptURL (React Native dev bundle source)
  const nativeModules = options.nativeModules !== undefined ? options.nativeModules : NativeModules;
  if (nativeModules?.SourceCode?.scriptURL) {
    const m = String(nativeModules.SourceCode.scriptURL).match(/^https?:\/\/([^:/]+)/i);
    if (m && m[1]) {
      return m[1];
    }
  }

  // 6. Web platform (window.location.hostname)
  const platform = options.platform || Platform.OS;
  if (platform === 'web') {
    const win = options.window !== undefined ? options.window : (typeof window !== 'undefined' ? window : null);
    const webHost = win?.location?.hostname;
    if (webHost) return webHost;
    return '127.0.0.1';
  }

  // 7. Explicit Android emulator option
  if (options.isAndroidEmulator) {
    return '10.0.2.2';
  }

  return null;
}

/**
 * Resolves the LAN development backend base URL.
 * Throws a clear development configuration error if the host is missing or malformed,
 * preventing silent fallback to a different backend.
 */
export function resolveLanBackendUrl(options = {}) {
  const host = resolveLanHost(options);
  if (!host) {
    throw new Error(
      '[NiyamNetra] LAN backend host could not be resolved. ' +
      'Please set EXPO_PUBLIC_LAN_HOST, configure LAPTOP_WIFI_IP in api/config.js, ' +
      'or ensure the app is connected to an active Expo development server.'
    );
  }

  const port = options.port || API_PORT;
  const normalized = normalizeBackendUrl(host, port);
  if (!normalized) {
    throw new Error(
      `[NiyamNetra] Malformed LAN backend configuration: "${host}". Expected a valid IP address, hostname, or URL.`
    );
  }

  // On Android devices/emulators, map loopback localhost/127.0.0.1 to the 10.0.2.2 emulator alias
  const platform = options.platform || Platform.OS;
  if (platform === 'android') {
    const m = normalized.match(/^(https?:\/\/)(localhost|127\.0\.0\.1)(:\d+)?$/i);
    if (m) {
      return `${m[1]}10.0.2.2${m[3] || `:${port}`}`;
    }
  }

  return normalized;
}

// Backward-compatible host resolution helper
export function resolveHost() {
  try {
    return resolveLanHost();
  } catch {
    return null;
  }
}

/**
 * Resolves the primary API base URL.
 * Precedence:
 * 1. EXPO_PUBLIC_API_BASE_URL (if non-empty, normalized)
 * 2. If !isDev or target === 'render', strictly returns RENDER_API_URL (never falls back to LAN/localhost)
 * 3. If target === 'local', returns localhost (or 10.0.2.2 on Android)
 * 4. If target === 'lan', resolves LAN host and port (or throws clear development error)
 */
export function resolveApiBaseUrl(options = {}) {
  const env = options.env || (typeof process !== 'undefined' ? process.env : {}) || {};
  const explicit = (options.explicitUrl || env.EXPO_PUBLIC_API_BASE_URL || '').trim();
  if (explicit) {
    const normalized = normalizeBackendUrl(explicit);
    if (!normalized) {
      throw new Error(`[NiyamNetra] Invalid EXPO_PUBLIC_API_BASE_URL: "${explicit}"`);
    }
    return normalized;
  }

  const isDev = options.isDev !== undefined
    ? options.isDev
    : (typeof __DEV__ !== 'undefined' ? __DEV__ : false);
  const target = options.target || ACTIVE_BACKEND;

  // In production / standalone APK release builds, default to cloud Render backend.
  // Production mode must NEVER silently fall back to LAN or localhost.
  if (!isDev || target === 'render') {
    return RENDER_API_URL;
  }

  if (target === 'local') {
    const localHost = (options.platform || Platform.OS) === 'android' ? '10.0.2.2' : '127.0.0.1';
    return `http://${localHost}:${API_PORT}`;
  }

  // LAN mode (dev)
  return resolveLanBackendUrl(options);
}

export const API_BASE_URL = resolveApiBaseUrl();

// ---------------------------------------------------------------------------
// Runtime target resolution & storage helpers (keeps backward compatibility)
const CUSTOM_KEY = 'nn_backend_custom';
let customUrl = null;

export const getCustomUrl = () => customUrl;

export async function loadCustomUrl() {
  try {
    const { getItem } = await import('../auth/secureStore');
    customUrl = normalizeBackendUrl(await getItem(CUSTOM_KEY));
  } catch { customUrl = null; }
  return customUrl;
}

export async function saveCustomUrl(input) {
  const url = normalizeBackendUrl(input);
  if (!url) return null;
  customUrl = url;
  try {
    const { setItem } = await import('../auth/secureStore');
    await setItem(CUSTOM_KEY, url);
  } catch { /* session fallback */ }
  return url;
}

export const BACKEND_TARGETS = {
  lan: {
    label: 'Wi-Fi (Laptop)',
    hint: 'Local server on Wi-Fi',
    resolve: () => resolveLanBackendUrl(),
  },
  render: {
    label: 'Cloud (Render)',
    hint: 'Deployed Render cloud',
    resolve: () => RENDER_API_URL,
  },
  local: {
    label: 'Local (This PC)',
    hint: 'Localhost on this device',
    resolve: () => LOCAL_API_URL,
  },
  custom: {
    label: 'Custom URL',
    hint: 'User-provided backend address',
    resolve: () => customUrl || RENDER_API_URL,
  },
};

const TARGET_KEY = 'nn_backend_target';
export const TARGET_NAMES = ['lan', 'render', 'local', 'custom'];

export function targetOf(url) {
  const u = String(url || '');
  if (u.startsWith(RENDER_API_URL)) return 'render';
  if (u.startsWith(LOCAL_API_URL) || u.includes('127.0.0.1') || u.includes('localhost') || u.includes('10.0.2.2')) return 'local';
  if (customUrl && u.startsWith(customUrl)) return 'custom';
  return 'lan';
}

export async function loadSavedTarget() {
  // The backend is PINNED to ACTIVE_BACKEND (top of this file). The on-screen
  // server-target switcher was removed from the login screen, so a stale
  // 'lan' choice saved on a phone must never silently take over the URL.
  // Point ACTIVE_BACKEND at 'lan' to test against a laptop backend again.
  return ACTIVE_BACKEND;
}

export async function saveTarget(name) {
  const { setItem } = await import('../auth/secureStore');
  await setItem(TARGET_KEY, name);
}

export function warnIfCookieHostMismatch(baseUrl) {
  if (Platform.OS !== 'web' || typeof window === 'undefined') return null;
  try {
    const apiHost = new URL(baseUrl).hostname;
    const pageHost = window.location.hostname;
    if (apiHost === pageHost) return null;
    return `[NiyamNetra] Page is on "${pageHost}" but API is on "${apiHost}".`;
  } catch {
    return null;
  }
}
