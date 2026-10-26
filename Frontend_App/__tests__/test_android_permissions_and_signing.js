// Frontend_App/__tests__/test_android_permissions_and_signing.js
// Regression test suite for Phase 2 Module 6: Android Permissions and Release Signing Hardening.

const assert = require('assert');
const path = require('path');
const fs = require('fs');

console.log('=== Running Module 6 Android Permissions & Release Signing Tests ===\n');

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

async function runTests() {
  const appJsonPath = path.join(__dirname, '../app.json');
  const appJson = JSON.parse(fs.readFileSync(appJsonPath, 'utf8'));

  const mainManifestPath = path.join(__dirname, '../android/app/src/main/AndroidManifest.xml');
  const mainManifest = fs.readFileSync(mainManifestPath, 'utf8');

  const debugManifestPath = path.join(__dirname, '../android/app/src/debug/AndroidManifest.xml');
  const debugManifest = fs.readFileSync(debugManifestPath, 'utf8');

  const buildGradlePath = path.join(__dirname, '../android/app/build.gradle');
  const buildGradle = fs.readFileSync(buildGradlePath, 'utf8');

  const easJsonPath = path.join(__dirname, '../eas.json');
  const easJson = JSON.parse(fs.readFileSync(easJsonPath, 'utf8'));

  const signingPlugin = require('../plugins/withAndroidReleaseSigning.js');

  // 1. Unnecessary permissions absent from app.json permissions array
  it('1. RECORD_AUDIO and SYSTEM_ALERT_WINDOW are absent from app.json permissions', () => {
    const permissions = appJson.expo?.android?.permissions || [];
    assert.strictEqual(
      permissions.some(p => p.includes('RECORD_AUDIO')),
      false,
      'RECORD_AUDIO must not be present in app.json permissions'
    );
    assert.strictEqual(
      permissions.some(p => p.includes('SYSTEM_ALERT_WINDOW')),
      false,
      'SYSTEM_ALERT_WINDOW must not be present in app.json permissions'
    );
  });

  // 2. Unnecessary permissions absent from src/main/AndroidManifest.xml
  it('2. RECORD_AUDIO and SYSTEM_ALERT_WINDOW are absent from src/main/AndroidManifest.xml', () => {
    assert.strictEqual(
      mainManifest.includes('android.permission.RECORD_AUDIO'),
      false,
      'RECORD_AUDIO must not be declared in src/main/AndroidManifest.xml'
    );
    assert.strictEqual(
      mainManifest.includes('android.permission.SYSTEM_ALERT_WINDOW'),
      false,
      'SYSTEM_ALERT_WINDOW must not be declared in src/main/AndroidManifest.xml'
    );
  });

  // 3. blockedPermissions in app.json prevents plugin/library reintroduction
  it('3. blockedPermissions in app.json explicitly blocks RECORD_AUDIO and SYSTEM_ALERT_WINDOW', () => {
    const blocked = appJson.expo?.android?.blockedPermissions || [];
    assert.ok(
      blocked.includes('android.permission.RECORD_AUDIO'),
      'RECORD_AUDIO must be explicitly listed in android.blockedPermissions'
    );
    assert.ok(
      blocked.includes('android.permission.SYSTEM_ALERT_WINDOW'),
      'SYSTEM_ALERT_WINDOW must be explicitly listed in android.blockedPermissions'
    );
  });

  // 4. expo-camera plugin explicitly disables microphone permission
  it('4. expo-camera plugin explicitly disables microphone permission in app.json', () => {
    const plugins = appJson.expo?.plugins || [];
    const cameraPlugin = plugins.find(p => Array.isArray(p) && p[0] === 'expo-camera');
    assert.ok(cameraPlugin, 'expo-camera plugin entry must exist in plugins array');
    const cameraConfig = cameraPlugin[1];
    assert.strictEqual(
      cameraConfig.microphonePermission,
      false,
      'expo-camera microphonePermission must be explicitly set to false'
    );
    assert.ok(cameraConfig.cameraPermission, 'expo-camera cameraPermission explanation must remain configured');
  });

  // 5. Required camera, location, and storage permissions remain
  it('5. Required camera, location, and network permissions are preserved', () => {
    const permissions = appJson.expo?.android?.permissions || [];
    assert.ok(
      permissions.includes('CAMERA') || permissions.includes('android.permission.CAMERA'),
      'CAMERA permission must be present in app.json'
    );
    assert.ok(
      permissions.includes('ACCESS_FINE_LOCATION') || permissions.includes('android.permission.ACCESS_FINE_LOCATION'),
      'ACCESS_FINE_LOCATION permission must be present in app.json'
    );
    assert.ok(
      permissions.includes('ACCESS_COARSE_LOCATION') || permissions.includes('android.permission.ACCESS_COARSE_LOCATION'),
      'ACCESS_COARSE_LOCATION permission must be present in app.json'
    );

    // Tracked native main manifest must contain runtime required permissions
    assert.ok(mainManifest.includes('android.permission.CAMERA'), 'src/main/AndroidManifest.xml must include CAMERA');
    assert.ok(mainManifest.includes('android.permission.ACCESS_FINE_LOCATION'), 'src/main/AndroidManifest.xml must include ACCESS_FINE_LOCATION');
    assert.ok(mainManifest.includes('android.permission.ACCESS_COARSE_LOCATION'), 'src/main/AndroidManifest.xml must include ACCESS_COARSE_LOCATION');
    assert.ok(mainManifest.includes('android.permission.INTERNET'), 'src/main/AndroidManifest.xml must include INTERNET');
  });

  // 6. Debug manifest preserves SYSTEM_ALERT_WINDOW for developer overlays
  it('6. src/debug/AndroidManifest.xml preserves SYSTEM_ALERT_WINDOW for debug tooling', () => {
    assert.ok(
      debugManifest.includes('android.permission.SYSTEM_ALERT_WINDOW'),
      'src/debug/AndroidManifest.xml must preserve SYSTEM_ALERT_WINDOW for local debug tooling'
    );
  });

  // 7. Release buildType does not fall back to debug signing
  it('7. buildTypes.release in android/app/build.gradle does not fall back to signingConfigs.debug', () => {
    const releaseMatch = buildGradle.match(/release\s*\{([\s\S]*?)\n\s*\}/);
    assert.ok(releaseMatch, 'release block must exist in build.gradle');
    const releaseBlock = releaseMatch[1];

    assert.strictEqual(
      /signingConfig\s+signingConfigs\.debug/.test(releaseBlock),
      false,
      'buildTypes.release must NOT specify signingConfig signingConfigs.debug'
    );
    assert.ok(
      releaseBlock.includes('signingConfig null'),
      'buildTypes.release must explicitly specify signingConfig null to prevent debug keystore fallback'
    );
  });

  // 8. withAndroidReleaseSigning config plugin enforces no-debug-fallback and supports properties
  it('8. withAndroidReleaseSigning plugin eliminates debug keystore fallback', () => {
    // Test on raw snippet containing signingConfigs.debug in release
    const dummyGradle = `
android {
    buildTypes {
        release {
            signingConfig signingConfigs.debug
        }
    }
}
dependencies {
}
`;
    const { contents, patched } = signingPlugin.patchAppGradle(dummyGradle);
    assert.strictEqual(patched, true, 'Plugin should patch unpatched gradle');
    assert.strictEqual(
      contents.includes('signingConfig signingConfigs.debug'),
      false,
      'Plugin must sanitize signingConfig signingConfigs.debug out of release'
    );
    assert.ok(contents.includes(signingPlugin.MARKER), 'Plugin must append MARKER block');
    assert.ok(contents.includes('project.findProperty("NN_RELEASE_KEYSTORE")'), 'Plugin must support gradle properties');
    assert.ok(contents.includes('signingConfig null'), 'Plugin must set signingConfig null when no keystore is provided');
  });

  // 9. eas.json preserves APK output for sideloading
  it('9. eas.json preview and production profiles remain configured with buildType: "apk"', () => {
    const previewBuild = easJson.build?.preview?.android?.buildType;
    const prodBuild = easJson.build?.production?.android?.buildType;
    assert.strictEqual(previewBuild, 'apk', 'preview profile must specify buildType "apk"');
    assert.strictEqual(prodBuild, 'apk', 'production profile must specify buildType "apk"');
  });

  // 10. Package identity and version configuration consistency
  it('10. Package ID, version name, and version code are consistent and monotonic', () => {
    const appPkg = appJson.expo?.android?.package;
    const appVersion = appJson.expo?.version;
    const appCode = appJson.expo?.android?.versionCode;

    assert.strictEqual(appPkg, 'com.niyamnetra.app', 'Package ID must be com.niyamnetra.app');

    // Verify build.gradle matches
    const gradleAppId = (buildGradle.match(/applicationId\s+['"]([^'"]+)['"]/) || [])[1];
    const gradleVersionName = (buildGradle.match(/versionName\s+['"]([^'"]+)['"]/) || [])[1];
    const gradleVersionCode = parseInt((buildGradle.match(/versionCode\s+(\d+)/) || [])[1], 10);

    assert.strictEqual(gradleAppId, appPkg, 'Gradle applicationId must match app.json package');
    assert.strictEqual(gradleVersionName, appVersion, 'Gradle versionName must match app.json version');
    assert.strictEqual(gradleVersionCode, appCode, 'Gradle versionCode must match app.json versionCode');

    // Current version code must be strictly greater than staged APK (versionCode 5)
    assert.ok(appCode > 5, `Source versionCode (${appCode}) must be greater than staged APK (5) to support upgrades`);
  });

  // 11. Secrets hygiene: no hardcoded passwords or keystore binary paths in source
  it('11. No keystore passwords or private key material are hardcoded in source files', () => {
    const forbiddenPatterns = [
      /password\s*=\s*['"][^'"]+['"]/i,
      /storePassword\s+['"](?!android['"])[^'"]+['"]/,
      /keyPassword\s+['"](?!android['"])[^'"]+['"]/,
    ];

    for (const pattern of forbiddenPatterns) {
      assert.strictEqual(
        pattern.test(buildGradle),
        false,
        `Hardcoded password pattern ${pattern} found in build.gradle`
      );
    }
  });

  console.log(`\nResults: ${passed} passed, ${failed} failed\n`);
  if (failed > 0) process.exit(1);
}

runTests().catch(err => {
  console.error('Test runner uncaught error:', err);
  process.exit(1);
});
