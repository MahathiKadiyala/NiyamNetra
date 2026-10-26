// Frontend_App/__tests__/test_app_branding_and_icons.js
// Regression test suite for NiyamNetra app icon and branding asset verification.

const assert = require('assert');
const path = require('path');
const fs = require('fs');
const zlib = require('zlib');

console.log('=== Running NiyamNetra App Icon & Branding Asset Tests ===\n');

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

/**
 * Reads basic PNG metadata from the IHDR chunk.
 * PNG layout:
 * - 0..7: Signature
 * - 8..11: Chunk length (13)
 * - 12..15: 'IHDR'
 * - 16..19: Width (UInt32BE)
 * - 20..23: Height (UInt32BE)
 * - 24: Bit depth
 * - 25: Color type (6 = RGBA, 2 = RGB)
 */
function readPngMetadata(filePath) {
  const buf = fs.readFileSync(filePath);
  const signature = Buffer.from([0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A]);
  assert.ok(buf.subarray(0, 8).equals(signature), `${filePath} is not a valid PNG (signature mismatch)`);

  const width = buf.readUInt32BE(16);
  const height = buf.readUInt32BE(20);
  const bitDepth = buf[24];
  const colorType = buf[25];

  return { width, height, bitDepth, colorType, sizeBytes: buf.length };
}

/**
 * Decompresses IDAT chunks and finds bounding box and max center distance of non-transparent pixels.
 */
function inspectPngAlphaMetrics(filePath) {
  const buf = fs.readFileSync(filePath);
  let pos = 8;
  const idatChunks = [];
  let width = 0;
  let height = 0;

  while (pos < buf.length) {
    const len = buf.readUInt32BE(pos);
    const type = buf.toString('ascii', pos + 4, pos + 8);
    if (type === 'IHDR') {
      width = buf.readUInt32BE(pos + 8);
      height = buf.readUInt32BE(pos + 12);
    } else if (type === 'IDAT') {
      idatChunks.push(buf.subarray(pos + 8, pos + 8 + len));
    }
    pos += 12 + len;
  }

  const raw = zlib.inflateSync(Buffer.concat(idatChunks));
  const rowBytes = 1 + width * 4; // 1 filter byte + 4 bytes per pixel (RGBA)

  let minX = width;
  let maxX = 0;
  let minY = height;
  let maxY = 0;
  let maxDistFromCenter = 0;
  let opaqueCount = 0;

  const centerX = width / 2;
  const centerY = height / 2;

  for (let y = 0; y < height; y++) {
    const rowOffset = y * rowBytes + 1; // skip filter byte
    for (let x = 0; x < width; x++) {
      const alpha = raw[rowOffset + x * 4 + 3];
      if (alpha > 0) {
        opaqueCount++;
        if (x < minX) minX = x;
        if (x > maxX) maxX = x;
        if (y < minY) minY = y;
        if (y > maxY) maxY = y;

        const dist = Math.sqrt((x - centerX) ** 2 + (y - centerY) ** 2);
        if (dist > maxDistFromCenter) {
          maxDistFromCenter = dist;
        }
      }
    }
  }

  return {
    width,
    height,
    minX,
    maxX,
    minY,
    maxY,
    maxDistFromCenter,
    opaqueCount,
  };
}

async function runTests() {
  const appJsonPath = path.join(__dirname, '../app.json');
  const appJson = JSON.parse(fs.readFileSync(appJsonPath, 'utf8'));
  const assetsDir = path.join(__dirname, '../assets');

  // 1. app.json configures valid paths for all branding assets
  it('1. app.json defines valid asset paths for icon, adaptiveIcon, splash, and favicon', () => {
    assert.strictEqual(appJson.expo?.icon, './assets/icon.png', 'app.json expo.icon must be ./assets/icon.png');
    assert.strictEqual(
      appJson.expo?.android?.adaptiveIcon?.foregroundImage,
      './assets/adaptive-icon.png',
      'app.json adaptiveIcon.foregroundImage must be ./assets/adaptive-icon.png'
    );
    assert.strictEqual(
      appJson.expo?.splash?.image,
      './assets/splash.png',
      'app.json splash.image must be ./assets/splash.png'
    );
    assert.strictEqual(
      appJson.expo?.web?.favicon,
      './assets/favicon.png',
      'app.json web.favicon must be ./assets/favicon.png'
    );
  });

  // 2. All referenced asset files physically exist on disk
  it('2. All referenced icon and splash assets exist on disk', () => {
    const requiredFiles = ['icon.png', 'adaptive-icon.png', 'splash.png', 'favicon.png'];
    for (const f of requiredFiles) {
      const fullPath = path.join(assetsDir, f);
      assert.ok(fs.existsSync(fullPath), `Asset file ${f} must exist at ${fullPath}`);
      const stats = fs.statSync(fullPath);
      assert.ok(stats.size > 100, `Asset ${f} size (${stats.size} bytes) must be non-trivial`);
    }
  });

  // 3. icon.png is a valid 1024x1024 PNG
  it('3. icon.png is a valid 1024 x 1024 PNG with RGBA color depth', () => {
    const meta = readPngMetadata(path.join(assetsDir, 'icon.png'));
    assert.strictEqual(meta.width, 1024, 'icon.png width must be exactly 1024');
    assert.strictEqual(meta.height, 1024, 'icon.png height must be exactly 1024');
    assert.strictEqual(meta.colorType, 6, 'icon.png must have RGBA color type (6)');
  });

  // 4. adaptive-icon.png is a valid 1024x1024 PNG with transparency
  it('4. adaptive-icon.png is a valid 1024 x 1024 PNG with RGBA transparency', () => {
    const meta = readPngMetadata(path.join(assetsDir, 'adaptive-icon.png'));
    assert.strictEqual(meta.width, 1024, 'adaptive-icon.png width must be exactly 1024');
    assert.strictEqual(meta.height, 1024, 'adaptive-icon.png height must be exactly 1024');
    assert.strictEqual(meta.colorType, 6, 'adaptive-icon.png must have RGBA color type (6)');
  });

  // 5. adaptive-icon.png content is strictly inside the Android 66% circular safe zone (radius <= 338 px)
  it('5. adaptive-icon.png emblem is centered and strictly within the 66% circular safe zone', () => {
    const metrics = inspectPngAlphaMetrics(path.join(assetsDir, 'adaptive-icon.png'));
    assert.ok(metrics.opaqueCount > 1000, 'adaptive-icon.png must contain visible emblem pixels');

    // Safe zone for 1024x1024 Android adaptive icon is 66% diameter = 676 px (radius 338 px)
    const SAFE_ZONE_RADIUS = 338;
    assert.ok(
      metrics.maxDistFromCenter <= SAFE_ZONE_RADIUS,
      `Emblem pixels exceed circular safe zone: max distance ${metrics.maxDistFromCenter.toFixed(1)} px > ${SAFE_ZONE_RADIUS} px`
    );

    // Verify emblem is vertically and horizontally centered
    const emblemCenterY = (metrics.minY + metrics.maxY) / 2;
    const emblemCenterX = (metrics.minX + metrics.maxX) / 2;
    assert.ok(
      Math.abs(emblemCenterY - 512) < 20,
      `Emblem must be vertically centered at 512 (got ${emblemCenterY.toFixed(1)})`
    );
    assert.ok(
      Math.abs(emblemCenterX - 512) < 20,
      `Emblem must be horizontally centered at 512 (got ${emblemCenterX.toFixed(1)})`
    );
  });

  // 6. splash.png is a valid 1242x1242 PNG containing the full wordmark version
  it('6. splash.png is a valid 1242 x 1242 PNG with centered wordmark branding', () => {
    const meta = readPngMetadata(path.join(assetsDir, 'splash.png'));
    assert.strictEqual(meta.width, 1242, 'splash.png width must be exactly 1242');
    assert.strictEqual(meta.height, 1242, 'splash.png height must be exactly 1242');
    assert.strictEqual(meta.colorType, 6, 'splash.png must have RGBA color type (6)');

    const metrics = inspectPngAlphaMetrics(path.join(assetsDir, 'splash.png'));
    assert.ok(metrics.opaqueCount > 10000, 'splash.png must contain substantial visible logo and wordmark pixels');

    // Content should be well-centered
    const contentCenterY = (metrics.minY + metrics.maxY) / 2;
    const contentCenterX = (metrics.minX + metrics.maxX) / 2;
    assert.ok(
      Math.abs(contentCenterY - 621) < 25,
      `Splash content must be vertically centered near 621 (got ${contentCenterY.toFixed(1)})`
    );
    assert.ok(
      Math.abs(contentCenterX - 621) < 25,
      `Splash content must be horizontally centered near 621 (got ${contentCenterX.toFixed(1)})`
    );
  });

  // 7. favicon.png is a valid 48x48 web favicon
  it('7. favicon.png is a valid 48 x 48 PNG', () => {
    const meta = readPngMetadata(path.join(assetsDir, 'favicon.png'));
    assert.strictEqual(meta.width, 48, 'favicon.png width must be 48');
    assert.strictEqual(meta.height, 48, 'favicon.png height must be 48');
  });

  // 8. Background colors in app.json match NiyamNetra brand navy (#0F2A44)
  it('8. app.json splash and adaptiveIcon background colors match brand navy (#0F2A44)', () => {
    assert.strictEqual(
      appJson.expo?.splash?.backgroundColor?.toUpperCase(),
      '#0F2A44',
      'splash.backgroundColor must be #0F2A44'
    );
    assert.strictEqual(
      appJson.expo?.android?.adaptiveIcon?.backgroundColor?.toUpperCase(),
      '#0F2A44',
      'adaptiveIcon.backgroundColor must be #0F2A44'
    );
  });

  // 9. Preserves application identity, versioning, and package ID
  it('9. Preserves package ID com.niyamnetra.app, version 1.0.5, and versionCode 6', () => {
    assert.strictEqual(appJson.expo?.android?.package, 'com.niyamnetra.app');
    assert.strictEqual(appJson.expo?.version, '1.0.5');
    assert.strictEqual(appJson.expo?.android?.versionCode, 6);
  });

  // 10. Preserves hardened permissions and security plugins
  it('10. Preserves blocked permissions and release security plugins', () => {
    const blocked = appJson.expo?.android?.blockedPermissions || [];
    assert.ok(blocked.includes('android.permission.RECORD_AUDIO'));
    assert.ok(blocked.includes('android.permission.SYSTEM_ALERT_WINDOW'));

    const plugins = appJson.expo?.plugins || [];
    assert.ok(plugins.includes('./plugins/withAndroidReleaseSigning'));
    assert.ok(plugins.includes('./plugins/withAndroidAbis'));
    assert.ok(plugins.includes('./plugins/withCleartextTraffic'));
  });

  console.log(`\nResults: ${passed} passed, ${failed} failed\n`);
  if (failed > 0) process.exit(1);
}

runTests().catch(err => {
  console.error('Test runner uncaught error:', err);
  process.exit(1);
});
