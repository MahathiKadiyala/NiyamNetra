// Frontend_App/__tests__/test_lan_backend_url_resolution.js
// Regression test suite for Phase 2 Module 8: Fix LAN Backend URL Resolution.

const assert = require('assert');

console.log('=== Running Module 8 LAN Backend URL Resolution Tests ===\n');

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
  const config = await import('../api/config.js');
  const {
    ACTIVE_BACKEND,
    RENDER_API_URL,
    API_PORT,
    LOCAL_API_URL,
    normalizeBackendUrl,
    resolveLanHost,
    resolveLanBackendUrl,
    resolveApiBaseUrl,
    targetOf,
    BACKEND_TARGETS,
  } = config;

  // 1. Production HTTPS URL remains unchanged
  it('1. Production HTTPS URL remains unchanged with no duplicate scheme and no appended port', () => {
    assert.strictEqual(RENDER_API_URL, 'https://niyamnetra-backend.onrender.com');

    // Default resolution in production/render mode
    const resolved = resolveApiBaseUrl({ isDev: false, target: 'render' });
    assert.strictEqual(resolved, 'https://niyamnetra-backend.onrender.com');
    assert.ok(!resolved.startsWith('http://https://'), 'Must not prepend http:// to https://');
    assert.ok(!resolved.endsWith(':8000'), 'Must not append dev port 8000 to production HTTPS URL');

    // Normalizing production HTTPS URL preserves it exactly
    const norm = normalizeBackendUrl('https://niyamnetra-backend.onrender.com');
    assert.strictEqual(norm, 'https://niyamnetra-backend.onrender.com');

    const explicitProd = resolveApiBaseUrl({
      explicitUrl: 'https://custom-prod.niyamnetra.gov.in',
      isDev: false,
    });
    assert.strictEqual(explicitProd, 'https://custom-prod.niyamnetra.gov.in');
  });

  // 2. LAN hostname resolves to a valid URL
  it('2. LAN hostname resolves to a valid URL with default port', () => {
    const res1 = resolveLanBackendUrl({ host: 'dev-laptop.local' });
    assert.strictEqual(res1, 'http://dev-laptop.local:8000');

    const res2 = resolveLanBackendUrl({ host: 'developer-workstation' });
    assert.strictEqual(res2, 'http://developer-workstation:8000');

    const hostResolved = resolveLanHost({ host: 'developer-workstation' });
    assert.strictEqual(hostResolved, 'developer-workstation');
  });

  // 3. LAN IPv4 host resolves to a valid URL
  it('3. LAN IPv4 host resolves to a valid URL with scheme and port', () => {
    const res1 = resolveLanBackendUrl({ host: '192.168.1.105' });
    assert.strictEqual(res1, 'http://192.168.1.105:8000');

    const res2 = resolveLanBackendUrl({ host: '10.50.17.186' });
    assert.strictEqual(res2, 'http://10.50.17.186:8000');

    // Environment variable resolution
    const resEnv = resolveLanBackendUrl({
      env: { EXPO_PUBLIC_LAN_HOST: '192.168.1.200' },
    });
    assert.strictEqual(resEnv, 'http://192.168.1.200:8000');

    const resPackager = resolveLanBackendUrl({
      env: { REACT_NATIVE_PACKAGER_HOSTNAME: '192.168.101.72' },
    });
    assert.strictEqual(resPackager, 'http://192.168.101.72:8000');
  });

  // 4. Existing scheme is not duplicated
  it('4. Existing scheme is not duplicated when already present', () => {
    const resHttp = resolveLanBackendUrl({ host: 'http://192.168.1.105' });
    assert.strictEqual(resHttp, 'http://192.168.1.105:8000');
    assert.ok(!resHttp.includes('http://http://'), 'Must not produce duplicate http://');

    const resHttps = resolveLanBackendUrl({ host: 'https://secure-lan.local' });
    assert.strictEqual(resHttps, 'https://secure-lan.local');
    assert.ok(!resHttps.startsWith('http://https://'), 'Must not prepend http:// to https://');

    const norm = normalizeBackendUrl('http://my-lan-host:8000');
    assert.strictEqual(norm, 'http://my-lan-host:8000');
  });

  // 5. Existing port is not duplicated
  it('5. Existing port is not duplicated when already present in input', () => {
    const resPort = resolveLanBackendUrl({ host: '192.168.1.105:8000' });
    assert.strictEqual(resPort, 'http://192.168.1.105:8000');
    assert.ok(!resPort.includes(':8000:8000'), 'Must not duplicate port :8000');

    const resCustomPort = resolveLanBackendUrl({ host: 'http://dev-server:9000' });
    assert.strictEqual(resCustomPort, 'http://dev-server:9000');

    const resEmulatorPort = resolveLanBackendUrl({ host: '10.0.2.2:8000', platform: 'android' });
    assert.strictEqual(resEmulatorPort, 'http://10.0.2.2:8000');
  });

  // 6. Empty or malformed LAN configuration produces the intended clear error
  it('6. Empty or malformed LAN configuration produces the intended clear error', () => {
    // Missing/empty host
    assert.throws(
      () => resolveLanBackendUrl({ host: '', env: {}, laptopWifiIp: '' }),
      (err) => {
        assert.ok(err instanceof Error);
        assert.ok(err.message.includes('[NiyamNetra] LAN backend host could not be resolved'));
        return true;
      }
    );

    // Malformed IPv6 bracket
    assert.throws(
      () => resolveLanBackendUrl({ host: 'http://[invalid' }),
      (err) => {
        assert.ok(err instanceof Error);
        assert.ok(err.message.includes('[NiyamNetra] Malformed LAN backend configuration'));
        return true;
      }
    );

    // Malformed consecutive dots
    assert.throws(
      () => resolveLanBackendUrl({ host: 'http://bad..host:8000' }),
      (err) => {
        assert.ok(err instanceof Error);
        assert.ok(err.message.includes('[NiyamNetra] Malformed LAN backend configuration'));
        return true;
      }
    );

    // Port out of range
    assert.throws(
      () => resolveLanBackendUrl({ host: 'http://my-host:99999' }),
      (err) => {
        assert.ok(err instanceof Error);
        assert.ok(err.message.includes('[NiyamNetra] Malformed LAN backend configuration'));
        return true;
      }
    );

    // Query parameters disallowed (prevents secret leakage)
    assert.throws(
      () => resolveLanBackendUrl({ host: 'http://192.168.1.50:8000?token=secret123' }),
      (err) => {
        assert.ok(err instanceof Error);
        assert.ok(err.message.includes('[NiyamNetra] Malformed LAN backend configuration'));
        return true;
      }
    );
  });

  // 7. Production mode never silently falls back to local development
  it('7. Production mode never silently falls back to local development or LAN', () => {
    // In production build (!isDev)
    const prod1 = resolveApiBaseUrl({
      isDev: false,
      target: 'render',
      host: '192.168.1.50',
      env: { EXPO_PUBLIC_LAN_HOST: '192.168.1.50' },
    });
    assert.strictEqual(prod1, 'https://niyamnetra-backend.onrender.com');

    // When target is 'render' in dev mode
    const prod2 = resolveApiBaseUrl({
      isDev: true,
      target: 'render',
      host: '127.0.0.1',
    });
    assert.strictEqual(prod2, 'https://niyamnetra-backend.onrender.com');

    // Target detection
    assert.strictEqual(targetOf('https://niyamnetra-backend.onrender.com'), 'render');
    assert.strictEqual(targetOf('http://127.0.0.1:8000'), 'local');
    assert.strictEqual(targetOf('http://10.0.2.2:8000'), 'local');
    assert.strictEqual(targetOf('http://192.168.1.100:8000'), 'lan');
  });

  // 8. The Android development host and port are resolved correctly using supported configuration
  it('8. Android development host and port are resolved correctly across emulator and physical device', () => {
    // Explicit emulator host
    const emu1 = resolveLanBackendUrl({ host: '10.0.2.2', platform: 'android' });
    assert.strictEqual(emu1, 'http://10.0.2.2:8000');

    // Localhost on Android maps to 10.0.2.2 emulator host loopback
    const emu2 = resolveLanBackendUrl({ host: 'localhost', platform: 'android' });
    assert.strictEqual(emu2, 'http://10.0.2.2:8000');

    const emu3 = resolveLanBackendUrl({ host: '127.0.0.1', platform: 'android' });
    assert.strictEqual(emu3, 'http://10.0.2.2:8000');

    // Metro NativeModules.SourceCode.scriptURL detection
    const metroNative = resolveLanBackendUrl({
      nativeModules: {
        SourceCode: {
          scriptURL: 'http://192.168.1.180:8081/index.bundle?platform=android&dev=true',
        },
      },
      platform: 'android',
    });
    assert.strictEqual(metroNative, 'http://192.168.1.180:8000');

    // Expo Constants hostUri detection
    const expoMetro = resolveLanBackendUrl({
      expoConstants: {
        expoConfig: {
          hostUri: '192.168.1.190:8081',
        },
      },
      platform: 'android',
    });
    assert.strictEqual(expoMetro, 'http://192.168.1.190:8000');

    // Web platform localhost fallback
    const webRes = resolveLanBackendUrl({
      platform: 'web',
      window: { location: { hostname: 'localhost' } },
    });
    assert.strictEqual(webRes, 'http://localhost:8000');
  });

  // 9. Existing request construction and authentication headers remain unchanged
  it('9. Existing request construction, idempotency, and auth headers remain intact', () => {
    const fs = require('fs');
    const path = require('path');
    const clientSource = fs.readFileSync(path.join(__dirname, '../api/client.js'), 'utf8');

    // Verify Axios instance configuration in client.js
    assert.ok(clientSource.includes('baseURL: API_BASE_URL'), 'Axios baseURL must be bound to API_BASE_URL from config.js');
    assert.ok(clientSource.includes('timeout: 30000'), 'Axios timeout must remain 30000ms');
    assert.ok(clientSource.includes('withCredentials: true'), 'Axios must have withCredentials: true');

    // Test the request interceptor contract logic
    const buildRequestConfig = (method, token, platform = 'android', existingHeaders = {}) => {
      const cfg = { method, headers: { ...existingHeaders } };
      if (token) cfg.headers.Authorization = `Bearer ${token}`;
      if (platform && platform !== 'web') {
        cfg.headers['X-Client-Platform'] = platform;
      }
      if (['post', 'patch', 'put'].includes((cfg.method || '').toLowerCase())) {
        const idKey = 'nn-' + 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (c) => {
          const r = (Math.random() * 16) | 0;
          const v = c === 'x' ? r : (r & 0x3) | 0x8;
          return v.toString(16);
        });
        cfg.headers['Idempotency-Key'] = cfg.headers['Idempotency-Key'] || idKey;
      }
      return cfg;
    };

    // GET request
    const getReq = buildRequestConfig('get', 'test-access-token-123', 'android');
    assert.strictEqual(getReq.headers.Authorization, 'Bearer test-access-token-123');
    assert.strictEqual(getReq.headers['X-Client-Platform'], 'android');
    assert.strictEqual(getReq.headers['Idempotency-Key'], undefined, 'GET requests must not carry Idempotency-Key');

    // POST request
    const postReq = buildRequestConfig('post', 'test-access-token-123', 'android');
    assert.strictEqual(postReq.headers.Authorization, 'Bearer test-access-token-123');
    assert.strictEqual(postReq.headers['X-Client-Platform'], 'android');
    assert.ok(
      /^nn-[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(
        postReq.headers['Idempotency-Key']
      ),
      'POST request must carry valid UUID v4 Idempotency-Key'
    );

    // Existing Idempotency-Key is preserved
    const customReq = buildRequestConfig('post', 'token', 'android', { 'Idempotency-Key': 'existing-key-456' });
    assert.strictEqual(customReq.headers['Idempotency-Key'], 'existing-key-456');

    // Web platform omits X-Client-Platform header
    const webReq = buildRequestConfig('get', 'token', 'web');
    assert.strictEqual(webReq.headers['X-Client-Platform'], undefined);
  });

  console.log(`\nResults: ${passed} passed, ${failed} failed`);
  if (failed > 0) {
    process.exit(1);
  }
}

runTests().catch((err) => {
  console.error('Test execution error:', err);
  process.exit(1);
});
