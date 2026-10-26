// ============================================================================
// withAndroidReleaseSigning — Expo config plugin (Android only)
// ============================================================================
// WHY THIS EXISTS
//
// "App not installed" on some phones while the same APK installs elsewhere is
// almost always a certificate problem — and this project had both kinds.
//
// 1. TWO DIFFERENT KEYS IN CIRCULATION.
//    The Expo template signs the release build type with the DEBUG keystore
//        buildTypes { release { signingConfig signingConfigs.debug } }
//    so a locally built release APK (`gradlew assembleRelease`,
//    `expo run:android --variant release`) carries CN=Android Debug. An EAS
//    build signs with the keystore EAS manages for the project — a different
//    certificate. Android refuses to replace an installed app with an APK
//    signed by another key: the install fails instantly with "App not
//    installed", and only on the phones that already have the *other* variant.
//    (Verified on the APK this project produced: `apksigner verify` reported
//    "Signer #1 certificate DN: CN=Android Debug".)
//    Fix: sign local release builds with the same keystore by pointing the four
//    NN_RELEASE_* variables below at it (`eas credentials` can download the EAS
//    keystore). Without them, the release build remains unsigned (or signed by
//    EAS in cloud builds) and will NEVER silently fall back to the debug keystore.
//
// 2. SIGNATURE SCHEMES (checked, not fixable here).
//    With minSdkVersion >= 24 the Android Gradle plugin (8.11) ships the APK
//    signed with the v2 scheme only and ignores `enableV1Signing` even when it
//    is set to true at configuration time — verified on this project: the
//    property read back `true` from the signing config, and `apksigner verify`
//    on the produced APK still reported "Verified using v1 scheme (JAR
//    signing): false". That is fine for Android 7.0+ (this app's floor), which
//    is every device minSdkVersion 24 can reach. If a specific OEM installer
//    turns out to insist on the v1 JAR signature, re-sign the finished APK with
//    the build-tools' apksigner — the exact commands are in
//    ANDROID_APK_INSTALL.md §5. No plugin can do it inside a normal Gradle
//    build, so it is documented rather than faked.
//
// WHAT IT TOUCHES
//
// Only android/app/build.gradle, and only by APPENDING one marked block. EAS
// does not rewrite that file's signingConfigs: it adds a separate
// android/app/eas-build.gradle that points the release (and debug) build types
// at `signingConfigs.release`, reading its keystore from credentials.json. So
// the optional NN_RELEASE_* keystore is only ever consulted for local builds —
// it can never displace the EAS keystore during a cloud build.
// ============================================================================
const { withAppBuildGradle } = require('@expo/config-plugins');

const MARKER = 'NIYAMNETRA_RELEASE_SIGNING';

const BLOCK = `
// ${MARKER} — begin
// Managed by plugins/withAndroidReleaseSigning.js. Keep the marker: it is what
// makes the plugin idempotent.
def nnKeystorePath = System.getenv("NN_RELEASE_KEYSTORE") ?: (project.findProperty("NN_RELEASE_KEYSTORE") ?: null)
def nnKeystorePassword = System.getenv("NN_RELEASE_KEYSTORE_PASSWORD") ?: (project.findProperty("NN_RELEASE_KEYSTORE_PASSWORD") ?: null)
def nnKeyAlias = System.getenv("NN_RELEASE_KEY_ALIAS") ?: (project.findProperty("NN_RELEASE_KEY_ALIAS") ?: null)
def nnKeyPassword = System.getenv("NN_RELEASE_KEY_PASSWORD") ?: (project.findProperty("NN_RELEASE_KEY_PASSWORD") ?: null)
def nnKeystoreFile = (nnKeystorePath && nnKeystorePassword && nnKeyAlias && nnKeyPassword) ? file(nnKeystorePath) : null
if (nnKeystoreFile != null && !nnKeystoreFile.exists()) {
    throw new GradleException("NN_RELEASE_KEYSTORE points at " + nnKeystoreFile + " but that file does not exist")
}
android {
    signingConfigs {
        release {
            // Local builds use the real keystore when it was provided. During an
            // EAS cloud build none of these variables exist, so this branch is
            // skipped and eas-build.gradle keeps the EAS keystore here.
            if (nnKeystoreFile != null) {
                storeFile nnKeystoreFile
                storePassword nnKeystorePassword
                keyAlias nnKeyAlias
                keyPassword nnKeyPassword
            }
        }
    }
    buildTypes {
        release {
            if (nnKeystoreFile != null) {
                signingConfig signingConfigs.release
            } else {
                // Ensure local release builds never fall back to debug signing.
                signingConfig null
            }
        }
    }
}
if (nnKeystoreFile == null) {
    logger.lifecycle("[NiyamNetra] No NN_RELEASE_KEYSTORE* credentials configured: local release builds will remain unsigned. " +
        "Release builds will NEVER fall back to the Android debug keystore. To sign local release APKs, set " +
        "NN_RELEASE_KEYSTORE / _KEYSTORE_PASSWORD / _KEY_ALIAS / _KEY_PASSWORD. For cloud builds, EAS supplies managed credentials.")
}
// ${MARKER} — end
`;

/**
 * Append the signing block to android/app/build.gradle. Exported so it can be
 * checked on its own without running a prebuild.
 *
 * @param {string} contents app/build.gradle source
 * @returns {{contents: string, patched: boolean, reason?: string}}
 */
function patchAppGradle(contents) {
  let modified = contents;
  // If release buildType has signingConfigs.debug, eliminate it to prevent insecure fallback
  if (/release\s*\{[\s\S]*?signingConfig signingConfigs\.debug/.test(modified)) {
    modified = modified.replace(
      /(release\s*\{[\s\S]*?)signingConfig signingConfigs\.debug/,
      '$1signingConfig null'
    );
  }
  if (modified.includes(MARKER)) {
    return { contents: modified, patched: modified !== contents, reason: 'already patched' };
  }
  if (!modified.includes('dependencies {')) {
    return { contents: modified, patched: false, reason: 'dependencies block not found (unexpected app/build.gradle)' };
  }
  return { contents: `${modified.trimEnd()}\n${BLOCK}`, patched: true };
}

module.exports = function withAndroidReleaseSigning(config) {
  return withAppBuildGradle(config, (cfg) => {
    if (cfg.modResults.language !== 'groovy') {
      console.warn('[withAndroidReleaseSigning] app/build.gradle is not Groovy — skipping the signing patch.');
      return cfg;
    }
    const { contents, patched, reason } = patchAppGradle(cfg.modResults.contents);
    if (patched) {
      cfg.modResults.contents = contents;
      console.log('[withAndroidReleaseSigning] release signing: optional NN_RELEASE_* keystore wired; debug-key trap warning enabled.');
    } else if (reason !== 'already patched') {
      console.warn(`[withAndroidReleaseSigning] app/build.gradle left unchanged: ${reason}.`);
    }
    return cfg;
  });
};

module.exports.patchAppGradle = patchAppGradle;
module.exports.MARKER = MARKER;