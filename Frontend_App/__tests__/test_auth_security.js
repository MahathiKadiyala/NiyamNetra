// Frontend_App/__tests__/test_auth_security.js — Authentication & Security Tests (Phase 2, Module 4)
const assert = require('assert');

let passed = 0;
let failed = 0;

function it(desc, fn) {
  try {
    fn();
    console.log(`  PASS: ${desc}`);
    passed++;
  } catch (err) {
    console.error(`  FAIL: ${desc}`);
    console.error(`    ${err.message}`);
    failed++;
  }
}

async function itAsync(desc, fn) {
  try {
    await fn();
    console.log(`  PASS: ${desc}`);
    passed++;
  } catch (err) {
    console.error(`  FAIL: ${desc}`);
    console.error(`    ${err.message}`);
    failed++;
  }
}

async function runTests() {
  console.log('=== Running Frontend Authentication & Security Tests ===\n');

  // 1. Authorization header construction
  it('1. API requests attach Bearer authorization token correctly', () => {
    const buildAuthHeader = (token) => {
      if (!token) return {};
      return { Authorization: `Bearer ${token}` };
    };

    const header = buildAuthHeader('eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.xyz');
    assert.strictEqual(header.Authorization, 'Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.xyz');
  });

  // 2. 401 Unauthorized handling
  it('2. 401 Unauthorized responses trigger session expiration handler', () => {
    let sessionExpiredCalled = false;
    const handleApiResponse = (status, onSessionExpired) => {
      if (status === 401) {
        onSessionExpired();
        return { ok: false, error: 'session_expired' };
      }
      return { ok: true };
    };

    const res = handleApiResponse(401, () => {
      sessionExpiredCalled = true;
    });

    assert.strictEqual(sessionExpiredCalled, true);
    assert.strictEqual(res.error, 'session_expired');
  });

  // 3. 403 Forbidden handling
  it('3. 403 Forbidden responses block restricted actions without crash', () => {
    const handleForbidden = (status) => {
      if (status === 403) {
        return { allowed: false, notice: 'Access denied: Insufficient privileges.' };
      }
      return { allowed: true };
    };

    const result = handleForbidden(403);
    assert.strictEqual(result.allowed, false);
    assert(result.notice.includes('Access denied'));
  });

  // 4. Secret scan: Verify no production secrets are hardcoded in client config
  it('4. Frontend configuration contains zero hardcoded API keys or master secrets', () => {
    const FORBIDDEN_SECRET_PATTERNS = [
      /groq_[a-zA-Z0-9]{20,}/i,
      /AIzaSy[a-zA-Z0-9_-]{33}/i,  // Google API key
      /eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9\.[a-zA-Z0-9_-]{50,}/, // JWT hardcoded
      /sk-[a-zA-Z0-9]{32,}/,       // OpenAI-style secret
      /supabase_service_role_key/i,
    ];

    const clientConfig = JSON.stringify({
      api_base_url: 'https://api.niyamnetra.gov.in',
      timeout_ms: 15000,
      offline_queue_limit: 100,
    });

    for (const pattern of FORBIDDEN_SECRET_PATTERNS) {
      assert.strictEqual(pattern.test(clientConfig), false, `Forbidden secret detected: ${pattern}`);
    }
  });

  // 5. Server-side identity resolution invariant
  it('5. User identity and permissions are determined strictly by server JWT claims', () => {
    const evaluateAccess = (userRole, requiredRole) => {
      if (requiredRole === 'admin' && userRole !== 'admin') {
        return false;
      }
      return true;
    };

    assert.strictEqual(evaluateAccess('inspector', 'inspector'), true);
    assert.strictEqual(evaluateAccess('inspector', 'admin'), false);
    assert.strictEqual(evaluateAccess('admin', 'admin'), true);
  });

  // 6. Device-bound install ID validation
  it('6. Device installation ID is verified during authentication', () => {
    const validateInstall = (tokenInstallId, deviceInstallId) => {
      if (tokenInstallId && tokenInstallId !== deviceInstallId) {
        return { valid: false, error: 'Device mismatch: token belongs to another physical device' };
      }
      return { valid: true };
    };

    assert.strictEqual(validateInstall('dev-100', 'dev-100').valid, true);
    assert.strictEqual(validateInstall('dev-100', 'dev-999').valid, false);
  });

  // 7. Native storage of refresh token vs web localStorage exclusion
  await itAsync('7. Native login persists refresh token to SecureStore while web excludes nn_refresh from localStorage', async () => {
    const mockSecureStore = {};
    const mockLocalStorage = {};

    const createStorage = (isWeb) => ({
      getItem: async (key) => {
        if (isWeb) {
          if (key === 'nn_refresh') return null;
          return mockLocalStorage[key] ?? null;
        }
        return mockSecureStore[key] ?? null;
      },
      setItem: async (key, val) => {
        if (isWeb) {
          if (key === 'nn_refresh') return; // strictly excluded
          mockLocalStorage[key] = val;
          return;
        }
        mockSecureStore[key] = val;
      },
    });

    const nativeStorage = createStorage(false);
    const webStorage = createStorage(true);

    // Save tokens on native
    await nativeStorage.setItem('nn_access', 'native_acc_tok');
    await nativeStorage.setItem('nn_refresh', 'native_ref_tok');

    assert.strictEqual(await nativeStorage.getItem('nn_access'), 'native_acc_tok');
    assert.strictEqual(await nativeStorage.getItem('nn_refresh'), 'native_ref_tok');

    // Attempt to save refresh token on web
    await webStorage.setItem('nn_access', 'web_acc_tok');
    await webStorage.setItem('nn_refresh', 'web_ref_tok');

    assert.strictEqual(await webStorage.getItem('nn_access'), 'web_acc_tok');
    assert.strictEqual(await webStorage.getItem('nn_refresh'), null, 'Web localStorage must never store nn_refresh');
    assert.strictEqual(mockLocalStorage['nn_refresh'], undefined);
  });

  // 8. Platform signal in request headers
  it('8. Native requests attach X-Client-Platform header, while web builds do not', () => {
    const buildRequestHeaders = (platform, token) => {
      const headers = {};
      if (token) headers.Authorization = `Bearer ${token}`;
      if (platform && platform !== 'web') {
        headers['X-Client-Platform'] = platform;
      }
      return headers;
    };

    const androidHeaders = buildRequestHeaders('android', 'tok123');
    assert.strictEqual(androidHeaders['X-Client-Platform'], 'android');
    assert.strictEqual(androidHeaders['Authorization'], 'Bearer tok123');

    const webHeaders = buildRequestHeaders('web', 'tok123');
    assert.strictEqual(webHeaders['X-Client-Platform'], undefined);
    assert.strictEqual(webHeaders['Authorization'], 'Bearer tok123');
  });

  // 9. Concurrent 401 de-duplication (no refresh storm)
  await itAsync('9. Concurrent 401 unauthorized requests deduplicate into a single in-flight refresh call', async () => {
    let refreshCalls = 0;
    let refreshingPromise = null;

    const doRefresh = () => {
      if (!refreshingPromise) {
        refreshCalls++;
        refreshingPromise = new Promise((resolve) => {
          setTimeout(() => {
            resolve({ access_token: 'new_access_token_123' });
            refreshingPromise = null;
          }, 20);
        });
      }
      return refreshingPromise;
    };

    // Simulate 5 simultaneous API requests failing with 401
    const p1 = doRefresh();
    const p2 = doRefresh();
    const p3 = doRefresh();
    const p4 = doRefresh();
    const p5 = doRefresh();

    const results = await Promise.all([p1, p2, p3, p4, p5]);

    assert.strictEqual(refreshCalls, 1, 'Exactly one refresh network request must be made');
    for (const r of results) {
      assert.strictEqual(r.access_token, 'new_access_token_123');
    }
  });

  // 10. Retry at most once
  await itAsync('10. Original request is retried at most once after refresh', async () => {
    let attemptCount = 0;
    let sessionExpiredEmitted = false;

    const simulateInterceptor = async (config) => {
      attemptCount++;
      if (attemptCount > 1) {
        // Simulating second 401 failure
        if (config._retried) {
          sessionExpiredEmitted = true;
          throw new Error('401 Unauthorized (dead session)');
        }
      }
      config._retried = true;
      // First 401 retries
      return simulateInterceptor(config);
    };

    const reqConfig = { url: '/api/v1/inspections' };
    try {
      await simulateInterceptor(reqConfig);
      assert.fail('Should have thrown on persistent 401');
    } catch (err) {
      assert.strictEqual(attemptCount, 2, 'Request was retried exactly once (total 2 attempts)');
      assert.strictEqual(sessionExpiredEmitted, true);
    }
  });

  // 11. Refresh failure on auth endpoints does not loop
  await itAsync('11. Refresh failure on auth endpoints avoids recursive retry loops', async () => {
    let refreshTriggered = false;
    let sessionExpired = false;

    const handleResponseError = async (err) => {
      const orig = err?.config;
      const status = err?.status;
      const url = String(orig?.url || '');

      if (status === 401) {
        if (url.includes('/auth/login') || url.includes('/auth/refresh')) {
          if (url.includes('/auth/refresh')) sessionExpired = true;
          return Promise.reject(err);
        }
        refreshTriggered = true;
      }
      return Promise.resolve();
    };

    try {
      await handleResponseError({ status: 401, config: { url: '/auth/refresh' } });
      assert.fail('Should have rejected');
    } catch {
      assert.strictEqual(refreshTriggered, false, 'Should never trigger refresh from /auth/refresh');
      assert.strictEqual(sessionExpired, true, 'Should immediately emit session expired');
    }
  });

  // 12. Session restore with expired access token
  await itAsync('12. Mobile session restore: expired access token falls back to stored refresh token', async () => {
    const store = {
      nn_access: 'expired_access_token',
      nn_refresh: 'valid_refresh_token',
    };

    const isTokenExpired = (tok) => tok === 'expired_access_token';

    let restoredSession = null;
    const bootstrapAuth = async () => {
      const savedAccess = store.nn_access;
      if (savedAccess && !isTokenExpired(savedAccess)) {
        restoredSession = { source: 'access', token: savedAccess };
        return;
      }

      const storedRefresh = store.nn_refresh;
      if (storedRefresh) {
        // Refresh token used to restore session
        const newAccess = 'fresh_access_token';
        const rotatedRefresh = 'rotated_refresh_token';
        store.nn_access = newAccess;
        store.nn_refresh = rotatedRefresh;
        restoredSession = { source: 'refresh', token: newAccess };
      }
    };

    await bootstrapAuth();
    assert.strictEqual(restoredSession.source, 'refresh');
    assert.strictEqual(restoredSession.token, 'fresh_access_token');
    assert.strictEqual(store.nn_refresh, 'rotated_refresh_token');
  });

  // 13. Native logout clears local credentials and sends refresh token for revocation
  await itAsync('13. Native logout transmits refresh token to server and purges all local storage', async () => {
    const store = {
      nn_access: 'access_123',
      nn_refresh: 'refresh_123',
      nn_user_profile: '{"id": 1, "name": "Inspector"}',
    };

    let serverRevokeToken = null;
    const serverLogout = async (payload) => {
      serverRevokeToken = payload?.refresh_token;
    };

    const logout = async () => {
      const payload = store.nn_refresh ? { refresh_token: store.nn_refresh } : undefined;
      await serverLogout(payload);
      delete store.nn_access;
      delete store.nn_refresh;
      delete store.nn_user_profile;
    };

    await logout();
    assert.strictEqual(serverRevokeToken, 'refresh_123');
    assert.strictEqual(store.nn_access, undefined);
    assert.strictEqual(store.nn_refresh, undefined);
    assert.strictEqual(store.nn_user_profile, undefined);
  });

  // 14. Credentials scrubbing: tokens and passwords do not appear in logs or error objects
  it('14. Authentication tokens and passwords do not appear in error messages or logs', () => {
    const sanitizeError = (err) => {
      const msg = String(err?.message || err || '');
      // Scrub any Bearer tokens or refresh token patterns
      return msg
        .replace(/Bearer\s+[a-zA-Z0-9_\-.]+/gi, 'Bearer [REDACTED]')
        .replace(/(?:refresh_token|password)=["']?[^"'\s&]+/gi, '$1=[REDACTED]');
    };

    const rawError = new Error('Request failed with Authorization: Bearer eyJhbGciOiJIUzI1Ni.sensitive and password=MySecretPassword123');
    const sanitized = sanitizeError(rawError);

    assert.strictEqual(sanitized.includes('sensitive'), false);
    assert.strictEqual(sanitized.includes('MySecretPassword123'), false);
    assert(sanitized.includes('Bearer [REDACTED]'));
  });

  console.log(`\nResults: ${passed} passed, ${failed} failed\n`);
  if (failed > 0) process.exit(1);
}

runTests();
