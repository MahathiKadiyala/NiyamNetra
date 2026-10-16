# APK build & install — why a phone says "App not installed"

This is the checklist for handing the NiyamNetra APK to a field phone. Every item
below was measured on a real artifact from this project, not guessed.

---

## 1. Build the APK (EAS — the supported path)

```powershell
cd c:\Skills\Projects\NiyamNetra\Frontend_App
npx eas build --platform android --profile preview      # APK, cloud backend
```

`preview` and `production` in `eas.json` are both `"buildType": "apk"`.

After installing, confirm the build you meant to ship: **Settings → Apps →
NiyamNetra** should read version `1.0.3` (versionCode ≥ 4). `cli.appVersionSource`
is `remote` and `autoIncrement` is on, so every EAS build gets a strictly higher
versionCode — Android never sees a "downgrade".

---

## 2. The five things that actually block an install

| # | Cause | Symptom | Fix |
|---|-------|---------|-----|
| 1 | **Different signing certificate** than the installed copy | "App not installed", instantly, only on phones that already have the app | Uninstall once (`adb uninstall com.niyamnetra.app`, or Settings → Apps → Uninstall), then install. See §3. |
| 2 | **APK too large / not enough free storage** | "App not installed" on budget phones | Fixed: the APK now carries only `arm64-v8a` + `armeabi-v7a` (≈ half the size). See §4. |
| 3 | **Phone runs Android 6 or older** | "App not installed" / "not compatible" | Not fixable — Expo SDK 54 / React Native 0.81 require **minSdkVersion 24 (Android 7.0)**. |
| 4 | **OEM install lock** | "Install blocked", Play Protect warning, or nothing happens | Allow "Install unknown apps" for the app you install *from* (Files/Chrome/WhatsApp). On MIUI also disable *MIUI optimization* for that file manager; on Vivo/Oppo enable "Install via USB"/allow unknown sources. |
| 5 | **Incomplete transfer** | "Package appears to be invalid" | Re-copy the APK; verify it with the checks in §5. |

---

## 3. Signing: the one that usually explains "only some phones"

- A **local** release build (`gradlew assembleRelease`, `expo run:android
  --variant release`) is signed with the Android **debug** keystore — the Expo
  template does that by design:
  `buildTypes { release { signingConfig signingConfigs.debug } }`.
- An **EAS** build is signed with the project's **EAS-managed** keystore.
- Android will not replace an installed app whose signature differs: that is an
  immediate "App not installed", with no explanation on screen.

So if a phone has ever carried the other variant, the new APK is refused there
and installs fine everywhere else — exactly "some phone models".

Two ways to stop mixing them:

```powershell
# Preferred: distribute EAS builds only (one keystore, stable across builds).
npx eas build --platform android --profile preview

# Optional: make LOCAL release builds use the same key,
# then both are interchangeable on every phone.
npx eas credentials           # Android -> download keystore
$env:NN_RELEASE_KEYSTORE="C:\path\to\keystore.jks"
$env:NN_RELEASE_KEYSTORE_PASSWORD="..."
$env:NN_RELEASE_KEY_ALIAS="..."
$env:NN_RELEASE_KEY_PASSWORD="..."
cd Frontend_App\android; .\gradlew.bat :app:assembleRelease
```

The build log says which of the two you are getting:

```
[NiyamNetra] No NN_RELEASE_KEYSTORE* env vars: this release APK is signed with the DEBUG keystore…
```

### Signature schemes (measured, not configurable)

With `minSdkVersion: 24`, AGP 8.11 signs the APK with the **v2 scheme only** and
ignores the deprecated `enableV1Signing` switch — verified here: the property
read back `true` from the signing config, yet the produced APK still verified as
`v1: false`. Android 7.0 and newer accept v2-only APKs, and 7.0 is this app's
floor, so no supported device is blocked by it.

If one specific OEM installer ever insists on the v1 (JAR) signature, re-sign the
finished APK with the build-tools' `apksigner` (same keystore, both schemes):

```powershell
& "<build-tools>\apksigner.bat" sign `
  --ks C:\path\to\keystore.jks --ks-key-alias <alias> `
  --v1-signing-enabled true --v2-signing-enabled true `
  --out app-release-v1signed.apk app-release.apk
```

Only do this when a phone provably demands it — an APK re-signed outside the
build must then be the one you distribute everywhere.

---

## 4. Size and ABIs

Measured on the previous release APK (93.7 MB):

```
x86          22.6 MB   <- emulator only, no phone needs it
x86_64       22.1 MB   <- emulator only, no phone needs it
arm64-v8a    20.8 MB   <- every current phone
armeabi-v7a  14.1 MB   <- older 32-bit phones
```

`plugins/withAndroidAbis.js` builds **arm64-v8a + armeabi-v7a** only, which drops
~44 MB of native libraries no phone can use. Every real device still installs.

Need an emulator build (x86_64)? Override the list for that one command, or use the
`development` profile, which already sets it:

```powershell
$env:NN_BUILD_ABIS="arm64-v8a,armeabi-v7a,x86_64"; npx expo prebuild -p android
```

---

## 5. Verify an APK before shipping it

`<build-tools>` is e.g. `%LOCALAPPDATA%\Android\Sdk\build-tools\36.1.0`.

```powershell
# versionCode / versionName / minSdk / targetSdk / ABIs
& "<build-tools>\aapt2.exe" dump badging app-release.apk | Select-String 'package:|sdkVersion|native-code'

# who signed it, and with which schemes (want: v2 true; v1 is off for minSdk 24+)
& "<build-tools>\apksigner.bat" verify --print-certs -v app-release.apk

# 16 KB page-size compatibility (needed by Android 15+ devices)
& "<build-tools>\zipalign.exe" -c -P 16 -v 4 app-release.apk
```

Expected for a good build: `native-code: 'arm64-v8a' 'armeabi-v7a'`,
`Verified using v2 scheme: true`, and `Verification successful`.

---

## 6. If a phone still refuses the APK

```powershell
# What is on the phone right now, and why it was refused:
adb shell dumpsys package com.niyamnetra.app | Select-String 'versionCode|signatures'
adb uninstall com.niyamnetra.app
adb install -r -d app-release.apk     # -d allows a versionCode downgrade
```

`INSTALL_FAILED_UPDATE_INCOMPATIBLE` = signature mismatch (cause 1, §3).
`INSTALL_FAILED_VERSION_DOWNGRADE` = the installed copy is newer — impossible with
`autoIncrement` enabled, unless the APK came from an older local build.

---

## 7. Authoritative References

- [13_RELEASE_AND_OPERATIONS.md](../documents/13_RELEASE_AND_OPERATIONS.md) — Release verification gates, checksums, and deployment operations.
- [03_INSPECTOR_WORKFLOW.md](../documents/03_INSPECTOR_WORKFLOW.md) — Field inspector operational guide.
- [09_OFFLINE_SYNC_AND_EVIDENCE.md](../documents/09_OFFLINE_SYNC_AND_EVIDENCE.md) — Offline SQLite storage and synchronization architecture.