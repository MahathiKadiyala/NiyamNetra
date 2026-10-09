import React, { useState, useRef, useEffect } from 'react';
import { View, Text, Pressable, ScrollView, Alert, Image, Platform, ActivityIndicator, StatusBar, StyleSheet } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { CameraView, useCameraPermissions } from 'expo-camera';
import { colors, spacing, typography, radius, shadows } from '../../theme';
import Header from '../../components/Header';
import PrimaryButton from '../../components/PrimaryButton';
import Card from '../../components/Card';
import Input from '../../components/Input';
import { enqueueInspection } from '../../offline/queue';
import { fetchStores } from '../../api/admin';
import { useAppLock } from '../../hooks/useAppLock';
import { useAllowScreenCapture } from '../../hooks/useAllowScreenCapture';
import { getItem, setItem } from '../../auth/secureStore';

// expo-location / FileSystem are optional at runtime: each import is guarded so
// a missing native module degrades to manual entry instead of a crash.
// 11 §2.4 — never crash on a missing optional dep.
let Location = null;
let LegacyFS = null;
try { Location = require('expo-location'); } catch { Location = null; }
try { LegacyFS = require('expo-file-system/legacy'); } catch { LegacyFS = null; }

let EVIDENCE_MIN_FREE_BYTES = 5 * 1024 ** 3;
try {
  // eslint-disable-next-line global-require
  const cfg = require('../../api/config');
  if (cfg?.EVIDENCE_MIN_FREE_BYTES) EVIDENCE_MIN_FREE_BYTES = cfg.EVIDENCE_MIN_FREE_BYTES;
} catch { /* literal fallback */ }

const transactionTypes = [
  // Must match Backend/schemas.py CreateInspectionRequest (7 types).
  // Previously used retail_package/loose_item/... which the API rejects (422).
  { key: 'retail_sale', label: 'Retail sale', icon: '🛒' },
  { key: 'wholesale', label: 'Wholesale', icon: '📦' },
  { key: 'institutional', label: 'Institutional', icon: '🏢' },
  { key: 'industrial', label: 'Industrial', icon: '🏭' },
  { key: 'packed_in_presence', label: 'Packed in presence', icon: '⚖️' },
  { key: 'export', label: 'Export', icon: '✈️' },
  { key: 'other', label: 'Other', icon: '📋' },
];

const CHECKS_COPY = 'Authoritative Statutory Checks (Rule Pack 2026.09.v1)';

export default function ScanScreen({ navigation }) {
  const insets = useSafeAreaInsets();
  const topClearance = Math.max(
    insets.top || 0,
    Platform.OS === 'android' ? (StatusBar.currentHeight || 0) : 0,
    Platform.OS === 'ios' ? 44 : 24
  );
  const bottomClearance = Math.max(insets.bottom || 0, 24);

  const [permission, requestPermission] = useCameraPermissions();
  const [step, setStep] = useState('scope');
  const [transactionType, setTransactionType] = useState(null);
  const [photos, setPhotos] = useState([]);
  const [capturing, setCapturing] = useState(false);
  // Viewfinder status. The preview surface is black by nature, so before the
  // first frame — or when the OS/another app refuses the camera — the officer
  // (and the recorded demo) would stare at a featureless black screen with no
  // explanation. Same pattern as InspectionSessionScreen / NewInspectionScreen.
  const [cameraReady, setCameraReady] = useState(false);
  const [cameraError, setCameraError] = useState(null);
  // Never invent a pass/fail verdict on-device. The server assesses the
  // inspection; locally we only track whether the capture was queued.
  // 'queued' means the inspection is stored and awaiting a server verdict.
  const [result, setResult] = useState(null); // 'queued' | null
  const [submitting, setSubmitting] = useState(false);

  // Store picker (required): server refuses inspections without store_id.
  const [stores, setStores] = useState([]);
  const [storeId, setStoreId] = useState(null);
  const [storesLoading, setStoresLoading] = useState(true);
  const [storesError, setStoresError] = useState(false);

  // GPS (advisory, not enforcement): captured at scan start for the geofence.
  const [coords, setCoords] = useState(null); // { latitude, longitude, accuracy }
  const [locStatus, setLocStatus] = useState('idle'); // idle|locating|ready|denied|unavailable|manual
  const [manualLat, setManualLat] = useState('');
  const [manualLong, setManualLong] = useState('');

  const cameraRef = useRef(null);
  useAppLock({ enabled: true });
  // Capture + review surfaces must be screen-recordable (SIH demo) — this
  // re-clears FLAG_SECURE and never sets it. See hooks/useAllowScreenCapture.js.
  useAllowScreenCapture();

  // Load stores for the picker.
  useEffect(() => {
    let mounted = true;
    (async () => {
      setStoresLoading(true);
      setStoresError(false);
      try {
        const data = await fetchStores();
        const list = Array.isArray(data) ? data : data?.items || data?.results || [];
        if (mounted && list.length > 0) {
          setStores(list);
          await setItem('nn_cached_stores', JSON.stringify(list));
        } else if (mounted) {
          const cached = await getItem('nn_cached_stores');
          const parsed = cached ? JSON.parse(cached) : [];
          setStores(parsed);
          if (parsed.length === 0) setStoresError(true);
        }
      } catch {
        if (mounted) {
          try {
            const cached = await getItem('nn_cached_stores');
            const parsed = cached ? JSON.parse(cached) : [];
            setStores(parsed);
            if (parsed.length === 0) setStoresError(true);
          } catch {
            setStores([]);
            setStoresError(true);
          }
        }
      } finally {
        if (mounted) setStoresLoading(false);
      }
    })();
    return () => { mounted = false; };
  }, []);

  // GPS fix at scan start (advisory only — a missing fix never blocks capture;
  // the officer can type coordinates manually below).
  const acquireLocation = async () => {
    if (!Location?.requestForegroundPermissionsAsync) {
      setLocStatus('unavailable');
      return;
    }
    setLocStatus('locating');
    try {
      const { status } = await Location.requestForegroundPermissionsAsync();
      if (status !== 'granted') {
        setLocStatus('denied');
        return;
      }
      const pos = await Location.getCurrentPositionAsync({ accuracy: Location.Accuracy?.Balanced || 3 });
      setCoords({
        latitude: pos?.coords?.latitude ?? null,
        longitude: pos?.coords?.longitude ?? null,
        accuracy: pos?.coords?.accuracy ?? null,
      });
      setLocStatus('ready');
    } catch {
      setLocStatus('unavailable');
    }
  };

  useEffect(() => {
    if (step === 'camera' && locStatus === 'idle') acquireLocation();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [step]);

  // Panel names per capture order — a photo is evidence of a named panel,
  // not "photo #3". Matches Backend ALLOWED_PANELS {front,back,side,mrp,batch,other}.
  const PANELS = ['front', 'back', 'side', 'mrp', 'batch', 'other'];
  const nextPanel = (count) => (count < PANELS.length ? PANELS[count] : `extra-${count - PANELS.length + 1}`);

  async function storageOk() {
    try {
      if (!LegacyFS?.getFreeDiskStorageAsync) return true; // unknown — do not block
      const free = await LegacyFS.getFreeDiskStorageAsync();
      if (free == null) return true;
      if (free < EVIDENCE_MIN_FREE_BYTES || free < 0.5 * 1024 ** 3) {
        const gb = (free / 1024 ** 3).toFixed(1);
        Alert.alert(
          'Storage low',
          `Only ${gb}GB free — capture is blocked to protect evidence. Sync now or free space (needs 5GB).`,
        );
        return false;
      }
      return true;
    } catch {
      return true;
    }
  }

  // Honest submit: persist the capture to the offline queue (which syncs via
  // SyncProvider when online). The verdict comes back from the server — this
  // screen never fabricates one.
  const submitInspection = async ({ draft = false } = {}) => {
    if (photos.length === 0 || submitting) return;
    if (storeId == null) {
      Alert.alert('Store required', 'Select the store being inspected before submitting.');
      return;
    }
    if (!(await storageOk())) return;
    setSubmitting(true);
    try {
      const lat = coords?.latitude ?? (manualLat !== '' ? Number(manualLat) : null);
      const lon = coords?.longitude ?? (manualLong !== '' ? Number(manualLong) : null);
      await enqueueInspection({
        store_id: storeId,
        transaction_type: transactionType,
        latitude: Number.isFinite(lat) ? lat : null,
        longitude: Number.isFinite(lon) ? lon : null,
        gps_accuracy_m: coords?.accuracy ?? null,
        scans: [{
          panelUris: photos.map((p) => p.uri),
          panels: photos.map((p) => p.panel),
        }],
      });
      setResult('queued');
      setStep('review');
      if (draft) Alert.alert('Inspection saved', 'Saved as In Progress on this device — will sync when online.');
    } catch (e) {
      Alert.alert(draft ? 'Save failed' : 'Submit failed', 'Could not queue the inspection. Please try again.');
    } finally {
      setSubmitting(false);
    }
  };

  if (!permission) return <View style={{ flex: 1, backgroundColor: colors.background }} />;

  if (!permission.granted) {
    return (
      <View style={{ flex: 1, backgroundColor: colors.background }}>
        <Header title="Camera Access" />
        <View style={{ flex: 1, justifyContent: 'center', alignItems: 'center', padding: spacing.xxl }}>
          <Text style={{ fontSize: 48, marginBottom: spacing.lg }}>📷</Text>
          <Text style={typography.h3}>Camera permission needed</Text>
          <Text style={{ ...typography.bodySecondary, textAlign: 'center', marginTop: spacing.sm, marginBottom: spacing.xl }}>
            NiyamNetra needs camera access to capture package photos.
          </Text>
          <PrimaryButton title="Grant Permission" onPress={requestPermission} />
        </View>
      </View>
    );
  }

  // Scope selection step — store is required before Continue.
  if (step === 'scope') {
    const canContinue = !!transactionType && storeId != null;
    return (
      <View style={{ flex: 1, backgroundColor: colors.background }}>
        <Header title="New Inspection" subtitle="Step 1 of 3 — Select type" />
        <ScrollView contentContainerStyle={{ padding: spacing.lg, paddingBottom: spacing.xxxl + 64 }}>
          <Text style={{ ...typography.body, marginBottom: spacing.lg }}>What are you assessing?</Text>
          <Card padding="lg">
            {transactionTypes.map((tt, idx) => (
              <Pressable key={tt.key} onPress={() => setTransactionType(tt.key)}
                accessibilityRole="radio"
                accessibilityState={{ checked: transactionType === tt.key }}
                accessibilityLabel={tt.label}
                style={{ flexDirection: 'row', alignItems: 'center', padding: spacing.md, borderRadius: radius.md,
                  backgroundColor: transactionType === tt.key ? colors.niyamBlue + '10' : 'transparent',
                  borderColor: transactionType === tt.key ? colors.niyamBlue : 'transparent',
                  borderWidth: transactionType === tt.key ? 2 : 0,
                  marginBottom: idx < transactionTypes.length - 1 ? spacing.sm : 0 }}>
                <Text style={{ fontSize: 24, marginRight: spacing.md }}>{tt.icon}</Text>
                <Text style={{ fontSize: 15, fontWeight: '600', color: colors.text, flex: 1 }}>{tt.label}</Text>
                {transactionType === tt.key && <Text style={{ color: colors.netraTeal, fontSize: 18, fontWeight: '700' }}>✓</Text>}
              </Pressable>
            ))}
          </Card>

          <Card title="Store (required)" padding="md" style={{ marginTop: spacing.md }}>
            {storesLoading ? (
              <ActivityIndicator color={colors.netraTeal} />
            ) : storesError ? (
              <Text style={{ fontSize: 13, color: colors.error }}>
                Could not load stores. Check your connection and restart this screen.
              </Text>
            ) : stores.length === 0 ? (
              <Text style={{ fontSize: 13, color: colors.textMuted }}>No stores available.</Text>
            ) : (
              <ScrollView style={{ maxHeight: 220 }} nestedScrollEnabled>
                {stores.map((s) => {
                  const selected = storeId === s.id;
                  return (
                    <Pressable
                      key={String(s.id)}
                      onPress={() => setStoreId(s.id)}
                      accessibilityRole="radio"
                      accessibilityState={{ checked: selected }}
                      accessibilityLabel={`Store ${s.name}`}
                      style={{
                        flexDirection: 'row', alignItems: 'center', padding: spacing.sm,
                        borderRadius: radius.sm, marginBottom: spacing.xs,
                        backgroundColor: selected ? colors.niyamBlue + '10' : 'transparent',
                        borderWidth: selected ? 2 : 0, borderColor: selected ? colors.niyamBlue : 'transparent',
                      }}
                    >
                      <Text style={{ fontSize: 14, fontWeight: selected ? '700' : '500', color: colors.text, flex: 1 }}>
                        {s.name}
                      </Text>
                      {selected && <Text style={{ color: colors.netraTeal, fontWeight: '700' }}>✓</Text>}
                    </Pressable>
                  );
                })}
              </ScrollView>
            )}
            {!storeId && !storesLoading && (
              <Text style={{ fontSize: 12, color: colors.warning, marginTop: spacing.sm }}>
                Select a store to continue — the server requires it.
              </Text>
            )}
          </Card>

          <PrimaryButton title="Continue to Camera" onPress={() => canContinue && setStep('camera')} disabled={!canContinue} style={{ marginTop: spacing.xl }} />
        </ScrollView>
      </View>
    );
  }


  const takePhoto = async () => {
    if (!cameraRef.current) return;
    if (cameraError) {
      Alert.alert('Camera unavailable', cameraError);
      return;
    }
    if (!cameraReady) {
      Alert.alert('Camera starting', 'The camera is still starting up. Please try again in a moment.');
      return;
    }
    if (!(await storageOk())) return;
    setCapturing(true);
    try {
      // No `skipProcessing`. Skipping the camera's processing pipeline drops the
      // EXIF-orientation step as well as `quality`, and the server's OCR has no
      // orientation detection: a photo that arrives rotated 90° is read as
      // sideways text. expo-camera applies the rotation physically when this is
      // left off, so the bytes that reach the evidence store are already upright.
      const photo = await cameraRef.current.takePictureAsync({ quality: 0.8 });
      const panel = nextPanel(photos.length);
      setPhotos((prev) => [...prev, { uri: photo.uri, panel }]);
    } catch (e) {
      Alert.alert('Capture failed', 'Please try again');
    }
    setCapturing(false);
  };

  if (step === 'camera') {
    const lat = coords?.latitude;
    const lon = coords?.longitude;
    return (
      <View style={{ flex: 1, backgroundColor: '#000' }}>
        <CameraView
          ref={cameraRef}
          style={{ flex: 1 }}
          facing="back"
          onCameraReady={() => setCameraReady(true)}
          onMountError={(e) => setCameraError(e?.message || 'The camera could not be started on this device.')}
        >
          <View style={{ flex: 1, justifyContent: 'space-between' }}>
            {!cameraReady && !cameraError && (
              <View
                pointerEvents="none"
                style={{ ...StyleSheet.absoluteFillObject, alignItems: 'center', justifyContent: 'center' }}
              >
                <ActivityIndicator color={colors.saffron} />
                <Text style={{ color: colors.white, fontSize: 13, fontWeight: '600', marginTop: 8 }}>
                  Starting camera…
                </Text>
              </View>
            )}
            {!!cameraError && (
              <View style={{ ...StyleSheet.absoluteFillObject, alignItems: 'center', justifyContent: 'center', paddingHorizontal: 24, backgroundColor: 'rgba(0,0,0,0.6)' }}>
                <Text style={{ fontSize: 30, marginBottom: 8 }}>⚠️</Text>
                <Text style={{ color: colors.white, fontSize: 13, fontWeight: '600', textAlign: 'center' }}>{cameraError}</Text>
                <Text style={{ color: 'rgba(255,255,255,0.75)', fontSize: 10.5, textAlign: 'center', marginTop: 6 }}>
                  Close other camera apps and tap Back to retry.
                </Text>
              </View>
            )}
            <View style={{ backgroundColor: 'rgba(15,42,68,0.9)', paddingTop: topClearance + 12, paddingBottom: spacing.md, paddingHorizontal: spacing.lg }}>
              <Text style={{ color: colors.white, fontSize: 16, fontWeight: '700' }}>Capture package panels</Text>
              <Text style={{ color: 'rgba(255,255,255,0.7)', fontSize: 12 }}>Photo {photos.length + 1}</Text>
              <Text
                accessible
                accessibilityRole="text"
                accessibilityLiveRegion="polite"
                style={{ color: 'rgba(255,255,255,0.7)', fontSize: 11, marginTop: 2 }}
              >
                {locStatus === 'ready' && lat != null
                  ? `GPS ${Number(lat).toFixed(5)}, ${Number(lon).toFixed(5)}${coords?.accuracy != null ? ` ±${Math.round(coords.accuracy)}m` : ''} — advisory only, not enforcement`
                  : locStatus === 'locating'
                    ? 'Locating… (advisory only)'
                    : locStatus === 'denied'
                      ? 'Location permission denied — enter coordinates manually on review (advisory only)'
                      : 'No GPS fix — enter coordinates manually on review (advisory only)'}
              </Text>
            </View>
            <View style={{ flex: 1, justifyContent: 'center', alignItems: 'center' }}>
              <View style={{ width: '75%', aspectRatio: 0.7 }}>
                <View style={{ position: 'absolute', top: -2, left: -2, width: 30, height: 30, borderTopWidth: 3, borderLeftWidth: 3, borderColor: colors.saffron }} />
                <View style={{ position: 'absolute', top: -2, right: -2, width: 30, height: 30, borderTopWidth: 3, borderRightWidth: 3, borderColor: colors.saffron }} />
                <View style={{ position: 'absolute', bottom: -2, left: -2, width: 30, height: 30, borderBottomWidth: 3, borderLeftWidth: 3, borderColor: colors.saffron }} />
                <View style={{ position: 'absolute', bottom: -2, right: -2, width: 30, height: 30, borderBottomWidth: 3, borderRightWidth: 3, borderColor: colors.saffron }} />
              </View>
            </View>
            <View style={{ backgroundColor: 'rgba(15,42,68,0.9)', paddingTop: spacing.lg, paddingBottom: bottomClearance + 20, paddingHorizontal: spacing.lg, alignItems: 'center' }}>
              <View style={{ flexDirection: 'row', alignItems: 'center' }}>
                <Pressable
                  onPress={() => setStep('scope')}
                  accessibilityRole="button"
                  accessibilityLabel="Back to type selection"
                  hitSlop={8}
                  style={{ marginRight: spacing.xxl, minWidth: 44, minHeight: 44, justifyContent: 'center' }}
                >
                  <Text style={{ color: 'rgba(255,255,255,0.8)', fontSize: 14 }}>← Back</Text>
                </Pressable>
                <Pressable
                  onPress={takePhoto}
                  accessibilityRole="button"
                  accessibilityLabel={capturing ? 'Capturing photo' : `Capture photo ${photos.length + 1}`}
                  accessibilityState={{ busy: capturing }}
                  hitSlop={8}
                  style={{ width: 72, height: 72, borderRadius: 36, backgroundColor: 'rgba(255,255,255,0.2)', borderWidth: 4, borderColor: colors.white, justifyContent: 'center', alignItems: 'center' }}
                >
                  {capturing ? <ActivityIndicator color={colors.white} /> : <View style={{ width: 56, height: 56, borderRadius: 28, backgroundColor: colors.white }} />}
                </Pressable>
                <Pressable
                  onPress={() => photos.length >= 1 && setStep('review')}
                  accessibilityRole="button"
                  accessibilityLabel="Next to review"
                  accessibilityState={{ disabled: photos.length < 1 }}
                  hitSlop={8}
                  style={{ marginLeft: spacing.xxl, opacity: photos.length >= 1 ? 1 : 0.4, minWidth: 44, minHeight: 44, justifyContent: 'center' }}
                >
                  <Text style={{ color: photos.length >= 1 ? colors.saffron : 'rgba(255,255,255,0.4)', fontSize: 14, fontWeight: '700' }}>Next →</Text>
                </Pressable>
              </View>
              <Text style={{ color: 'rgba(255,255,255,0.7)', fontSize: 12, marginTop: spacing.sm, fontWeight: '500' }}>
                {photos.length === 0 ? 'Capture Principal Display Panel (PDP)' : `${photos.length} photo(s) captured — tap Next →`}
              </Text>
              <Text style={{ color: 'rgba(255,255,255,0.5)', fontSize: 11, marginTop: 2, textAlign: 'center' }}>
                Photo quality is assessed by the server after upload — a blurry capture is kept, never auto-deleted.
              </Text>
            </View>
          </View>
        </CameraView>
      </View>
    );
  }

  return (
    <View style={{ flex: 1, backgroundColor: colors.background }}>
      <Header title="Review & Submit" subtitle={`${photos.length} photos captured`} />
      <ScrollView contentContainerStyle={{ padding: spacing.lg, paddingBottom: spacing.xxxl + 64 }}>
        <Card title="Inspection Summary" padding="lg" style={{ marginBottom: spacing.md }}>
          <View style={{ flexDirection: 'row', marginBottom: spacing.sm }}>
            <Text style={{ ...typography.label, width: 100 }}>Type</Text>
            <Text style={typography.body}>{transactionTypes.find(t => t.key === transactionType)?.label}</Text>
          </View>
          <View style={{ flexDirection: 'row', marginBottom: spacing.sm }}>
            <Text style={{ ...typography.label, width: 100 }}>Photos</Text>
            <Text style={typography.body}>{photos.length} panels</Text>
          </View>
          <View style={{ flexDirection: 'row' }}>
            <Text style={{ ...typography.label, width: 100 }}>Checks</Text>
            <Text style={typography.body}>{CHECKS_COPY}</Text>
          </View>
          <Text style={{ fontSize: 12, color: colors.textMuted, marginTop: spacing.sm }}>
            The assessed count per package comes back from the server after sync — nothing is decided on this device.
          </Text>
        </Card>
        {(locStatus !== 'ready' || coords?.latitude == null) && (
          <Card title="Location (manual fallback)" padding="md" style={{ marginBottom: spacing.md }}>
            <Text style={{ fontSize: 12, color: colors.textMuted, marginBottom: spacing.sm }}>
              No GPS fix ({locStatus}). Type the coordinates if known — advisory for the geofence, never a blocker.
            </Text>
            <View style={{ flexDirection: 'row' }}>
              <View style={{ flex: 1, marginRight: spacing.sm }}>
                <Input label="Latitude" value={manualLat} onChangeText={setManualLat} placeholder="e.g. 17.3850" keyboardType="numbers-and-punctuation" />
              </View>
              <View style={{ flex: 1 }}>
                <Input label="Longitude" value={manualLong} onChangeText={setManualLong} placeholder="e.g. 78.4867" keyboardType="numbers-and-punctuation" />
              </View>
            </View>
          </Card>
        )}
        <Card title="Captured Photos" padding="md" style={{ marginBottom: spacing.md }}>
          <View style={{ flexDirection: 'row', flexWrap: 'wrap' }}>
            {photos.map((p, i) => (
              <View key={`${p.panel}-${i}`} style={{ width: 80, margin: spacing.xs, alignItems: 'center' }}>
                <Image
                  source={{ uri: p.uri }}
                  style={{ width: 80, height: 80, borderRadius: radius.sm, borderWidth: 2, borderColor: colors.saffron, backgroundColor: colors.borderLight }}
                />
                <Text style={{ fontSize: 10, color: colors.textMuted, marginTop: 2 }}>{p.panel}</Text>
              </View>
            ))}
          </View>
        </Card>
        <View style={{ backgroundColor: colors.info.fill, borderRadius: radius.md, padding: spacing.md, borderColor: colors.info.border, borderWidth: 1, marginBottom: spacing.lg }}>
          <Text style={{ color: colors.info.text, fontSize: 12 }}>Will submit immediately when you tap Submit.</Text>
        </View>
        {result === 'queued' && (
          <Card
            padding="lg"
            style={{
              marginBottom: spacing.md,
              borderColor: colors.info.border,
              borderWidth: 2,
              backgroundColor: colors.info.fill,
            }}
          >
            <View style={{ flexDirection: 'row', alignItems: 'center' }}>
              <Text style={{ fontSize: 32, marginRight: spacing.md }}>⏳</Text>
              <View style={{ flex: 1 }}>
                <Text style={{ fontSize: 18, fontWeight: '700', color: colors.info.text }}>
                  Queued for assessment — server verdict pending
                </Text>
                <Text style={{ fontSize: 13, color: colors.textSecondary, marginTop: 4 }}>
                  Photos are stored on this device and will sync when online. No pass/fail is decided locally.
                  Server assessment is pending — view findings under Violations or Pass once synced.
                </Text>
                <View style={{ flexDirection: 'row', marginTop: spacing.md }}>
                  <Pressable
                    onPress={() => navigation?.navigate?.('Violations')}
                    accessibilityRole="button"
                    accessibilityLabel="View server findings, violations"
                    hitSlop={8}
                    style={{ marginRight: spacing.md, paddingVertical: 10, paddingHorizontal: 14, borderRadius: radius.md, backgroundColor: colors.niyamBlue, minHeight: 44, justifyContent: 'center' }}
                  >
                    <Text style={{ color: colors.white, fontSize: 13, fontWeight: '700' }}>View findings (Violations)</Text>
                  </Pressable>
                  <Pressable
                    onPress={() => navigation?.navigate?.('Pass')}
                    accessibilityRole="button"
                    accessibilityLabel="View server findings, passes"
                    hitSlop={8}
                    style={{ paddingVertical: 10, paddingHorizontal: 14, borderRadius: radius.md, borderWidth: 1.5, borderColor: colors.niyamBlue, minHeight: 44, justifyContent: 'center' }}
                  >
                    <Text style={{ color: colors.niyamBlue, fontSize: 13, fontWeight: '700' }}>Pass list</Text>
                  </Pressable>
                </View>
              </View>
            </View>
          </Card>
        )}
        <PrimaryButton
          title={result ? 'New Inspection' : submitting ? 'Queueing…' : 'Submit Inspection'}
          onPress={() => {
            if (result) {
              setStep('scope'); setTransactionType(null); setPhotos([]); setResult(null); setStoreId(null);
            } else {
              submitInspection();
            }
          }}
          disabled={submitting}
          style={{ marginBottom: spacing.md }}
        />
        <PrimaryButton title="Save as In Progress" variant="outline" onPress={() => submitInspection({ draft: true })} disabled={submitting || photos.length === 0} />
      </ScrollView>
    </View>
  );
}
