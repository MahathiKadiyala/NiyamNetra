// offline/queue.js — Expo file-system queue. Never AsyncStorage + base64. 11 §2.4
import { Platform } from 'react-native';
// The classic FileSystem API moved to 'expo-file-system/legacy' in SDK 54
// (the default export is the new Directory/File/Paths API).
import * as FileSystem from 'expo-file-system/legacy';

// On web there is no file system (FileSystem.documentDirectory is null), and the
// app is used for browser preview only — the offline capture queue is a phone
// feature. Web callers persist to localStorage instead of silently dropping.
const isWeb = Platform.OS === 'web';
const WEB_KEY = 'nn_queue_web';

// NOTE: FileSystem.documentDirectory is null on web AND is only valid after
// the native module loads, so DIR/MANIFEST must be resolved lazily inside
// each function (evaluating them at import time crashes the web build).
// Never compute them at module scope.
function pendingDir() {
  return FileSystem.documentDirectory + 'pending/';
}

function manifestPath() {
  return pendingDir() + 'manifest.json';
}

// Monotonic counter backing BOTH file names and queue ids, so two items
// created in the same millisecond never collide (timestamps alone collide
// under burst capture and random suffixes can theoretically repeat).
let monotonic = 0;
function nextSeq() {
  monotonic += 1;
  return `${Date.now()}-${monotonic}`;
}
function uniqueName(prefix) {
  return `${prefix}-${nextSeq()}.jpg`;
}
function newId(prefix) {
  return `${prefix}-${nextSeq()}`;
}

// Ensure dir exists
async function ensureDir() {
  const DIR = pendingDir();
  const info = await FileSystem.getInfoAsync(DIR);
  if (!info.exists) await FileSystem.makeDirectoryAsync(DIR, { intermediates: true });
}

// ---- web localStorage fallback (preview only — nothing is silently lost) ---
function loadWebQueue() {
  try {
    const raw = globalThis.localStorage?.getItem(WEB_KEY);
    if (!raw) return [];
    const q = JSON.parse(raw);
    return Array.isArray(q) ? q : [];
  } catch {
    return [];
  }
}
function saveWebQueue(q) {
  try {
    globalThis.localStorage?.setItem(WEB_KEY, JSON.stringify(q));
  } catch { /* quota — best effort */ }
}

// Manifest is array of { id, type, inspectionId, scanId, fileUris[], files[],
// incompleteFiles?, body, result?, createdAt, is_synced, syncError? }
export async function loadQueue() {
  if (isWeb) return loadWebQueue();
  await ensureDir();
  const MANIFEST = manifestPath();
  const info = await FileSystem.getInfoAsync(MANIFEST);
  if (!info.exists) return [];
  const raw = await FileSystem.readAsStringAsync(MANIFEST);
  try {
    const q = JSON.parse(raw);
    return Array.isArray(q) ? q : [];
  } catch { return []; }
}

export async function saveQueue(q) {
  if (isWeb) {
    saveWebQueue(q);
    return;
  }
  await ensureDir();
  await FileSystem.writeAsStringAsync(manifestPath(), JSON.stringify(q));
}

// Copy helper shared by both enqueue paths. Panels travel WITH the uri so the
// sync pass can upload each file to POST /scans/{id}/images with its panel
// label instead of guessing.
async function copyIntoPending(uris, panels, idPrefix) {
  const DIR = isWeb ? null : pendingDir();
  const files = [];
  const incompleteFiles = [];
  const list = uris || [];
  for (let i = 0; i < list.length; i++) {
    const uri = list[i];
    const panel = (panels && panels[i]) || `extra-${i + 1}`;
    if (isWeb) {
      // No filesystem on web: keep the original uri (blob/object URL) plus
      // the panel label so the sync pass still knows what to upload.
      files.push({ uri, panel, original_uri: uri });
      continue;
    }
    const dest = DIR + uniqueName(idPrefix);
    try {
      await FileSystem.copyAsync({ from: uri, to: dest });
      files.push({ uri: dest, panel, original_uri: dest });
    } catch (e) {
      console.warn('[queue] copy failed, marking incomplete:', uri, e?.message || e);
      incompleteFiles.push(uri);
    }
  }
  return { files, incompleteFiles };
}

// Enqueue a new inspection+scan atomically. Returns id.
// store_id is REQUIRED (backend 404s without it); latitude/longitude/accuracy
// ride along for the geofence. `result` is a provisional local hint only
// (e.g. 'queued') — the server verdict is authoritative; the sync pass sorts
// provisional violations first when the flag is present.
export async function enqueueInspection({ store_id, transaction_type, latitude, longitude, gps_accuracy_m, scans, result, signature_status, notes, is_synced = false }) {
  const q = await loadQueue();
  const id = newId('insp');
  const local_created_at = new Date().toISOString();

  // Process all packages in the inspection session
  const processedScans = [];
  const allFiles = [];
  const allIncomplete = [];

  const scanList = Array.isArray(scans) && scans.length > 0 ? scans : [{}];
  for (let sIdx = 0; sIdx < scanList.length; sIdx++) {
    const s = scanList[sIdx];
    const sId = `${id}-scan-${sIdx + 1}`;
    let panelUris = [];
    let panels = [];
    if (s.panelEvidence && Object.keys(s.panelEvidence).length > 0) {
      // Authoritative original camera captures take absolute priority over analysis images
      panels = Object.keys(s.panelEvidence);
      panelUris = panels.map((p) => s.panelEvidence[p]?.original_uri || s.panelPhotos?.[p]);
    } else if (s.files && s.files.length > 0 && s.files[0]?.original_uri) {
      panels = s.files.map((f) => f.panel);
      panelUris = s.files.map((f) => f.original_uri || f.uri);
    } else {
      panelUris = s.panelUris || (s.panelPhotos ? Object.values(s.panelPhotos) : []);
      panels = s.panels || (s.panelPhotos ? Object.keys(s.panelPhotos) : panelUris.map((_, i) => (
        ['front', 'back', 'mrp', 'batch'][i] || `extra-${i - 3}`
      )));
    }
    const { files, incompleteFiles } = await copyIntoPending(panelUris, panels, sId);
    allFiles.push(...files);
    allIncomplete.push(...incompleteFiles);

    // Honest geometry without fabricated dimensions (scale_source: none)
    const geometry = s.geometry || {
      panel_shape: 'rectangular',
      panel_height_mm: null,
      panel_width_mm: null,
      is_blown_moulded: false,
      scale_source: 'none',
    };

    processedScans.push({
      commodity_generic: s.commodity_generic || null,
      brand_name: s.brand_name || null,
      batch_number: s.batch_number || null,
      geometry,
      is_imported: s.is_imported,
      is_perishable: s.is_perishable,
      has_sticker: s.has_sticker,
      files,
      fileUris: files.map((f) => f.uri),
      incompleteFiles,
    });
  }

  q.push({
    id,
    type: 'inspection',
    body: { store_id, transaction_type, latitude, longitude, gps_accuracy_m, local_created_at, notes: notes || null },
    signature_status: signature_status || 'signed',
    notes: notes || null,
    scans: processedScans,
    // Keep top-level files & fileUris for backward compatibility
    files: allFiles,
    fileUris: allFiles.map((f) => f.uri),
    incompleteFiles: allIncomplete,
    createdAt: local_created_at,
    is_synced: Boolean(is_synced),
    ...(result ? { result } : {}),
  });
  await saveQueue(q);
  return id;
}

// Enqueue a scan with panels
export async function enqueueScan(inspectionLocalId, { commodity_generic, brand_name, batch_number, geometry, panelUris, panels, is_imported, is_perishable, is_tobacco, has_sticker, result, violationSuspected }) {
  const q = await loadQueue();
  const id = newId('scan');
  const geom = geometry || {
    panel_shape: 'rectangular',
    panel_height_mm: null,
    panel_width_mm: null,
    is_blown_moulded: false,
    scale_source: 'none',
  };
  const { files, incompleteFiles } = await copyIntoPending(
    panelUris,
    panels || (panelUris || []).map((_, i) => (['front', 'back', 'mrp', 'batch'][i] || `extra-${i - 3}`)),
    id,
  );
  q.push({
    id,
    type: 'scan',
    parentId: inspectionLocalId,
    body: {
      commodity_generic,
      brand_name,
      batch_number,
      geometry: geom,
      is_imported,
      is_perishable,
      is_tobacco,
      has_sticker,
    },
    files,
    fileUris: files.map((f) => f.uri),
    incompleteFiles,
    createdAt: new Date().toISOString(),
    is_synced: false,
    ...(result ? { result } : {}),
    ...(violationSuspected ? { violationSuspected: true } : {}),
  });
  await saveQueue(q);
  return id;
}

export async function markSynced(id) {
  const q = await loadQueue();
  const idx = q.findIndex(x => x.id === id);
  if (idx >= 0) {
    q[idx].is_synced = true;
    delete q[idx].syncError;
    try {
      await saveQueue(q);
    } finally {
      // try/finally so a storage failure after the in-memory mark cannot
      // leave the caller believing the mark was persisted — the error still
      // propagates after the write was attempted.
    }
  }
}

// Mark an item as permanently failed (4xx dead-letter): it stays in the queue
// for inspection but is_syncing skips it. Never retried, never purged blindly.
export async function markFailed(id, error) {
  const q = await loadQueue();
  const idx = q.findIndex(x => x.id === id);
  if (idx >= 0) {
    q[idx].syncFailed = true;
    q[idx].syncError = String(error?.message || error || 'rejected by server').slice(0, 300);
    try {
      await saveQueue(q);
    } finally { /* mark attempted; propagate nothing */ }
  }
}

// Remove synced items and delete their files after the server confirms
// receipt. TODO: verify sha256 against the server before purging — current
// purge only confirms the POST succeeded, not byte-level integrity.
// Idempotent: safe to call on startup — any leftover is_synced rows (e.g.
// from a crash between markSynced and purge) are collected here.
export async function purgeSynced() {
  const q = await loadQueue();
  const remaining = [];
  for (const item of q) {
    if (item.is_synced && !item.syncFailed) {
      if (!isWeb) {
        const uris = (item.files || []).map((f) => f.uri).concat(item.fileUris || []);
        for (const uri of new Set(uris)) {
          try { await FileSystem.deleteAsync(uri, { idempotent: true }); } catch {}
        }
      }
    } else remaining.push(item);
  }
  try {
    await saveQueue(remaining);
  } finally { /* purge attempted */ }
}

// On startup, collect any is_synced leftovers from a crash between
// markSynced and purgeSynced. Idempotent — call once at boot.
export async function purgeSyncedOnBoot() {
  try {
    await purgeSynced();
  } catch { /* best effort on boot */ }
}

export async function getFreeDiskStorageAsync() {
  try { return await FileSystem.getFreeDiskStorageAsync(); } catch { return null; }
}

export async function queueSize() {
  const q = await loadQueue();
  return q.filter(x => !x.is_synced && !x.syncFailed).length;
}

export async function syncFailedCount() {
  const q = await loadQueue();
  return q.filter(x => x.syncFailed).length;
}

export async function retryFailed(id) {
  const q = await loadQueue();
  if (id) {
    const item = q.find(x => x.id === id);
    if (item) {
      delete item.syncFailed;
      delete item.syncError;
      item.is_synced = false;
    }
  } else {
    for (const item of q) {
      if (item.syncFailed) {
        delete item.syncFailed;
        delete item.syncError;
        item.is_synced = false;
      }
    }
  }
  await saveQueue(q);
}

export async function enqueueAdjudication({
  inspection_id,
  action,
  reason,
  resolution_notes,
  finding_id,
  human_verdict,
}) {
  const q = await loadQueue();
  const id = newId('adjudication');
  q.push({
    id,
    type: 'adjudication',
    inspectionId: inspection_id,
    body: {
      inspection_id,
      action,
      reason,
      resolution_notes,
      finding_id,
      human_verdict,
    },
    createdAt: new Date().toISOString(),
    is_synced: false,
  });
  await saveQueue(q);
  return id;
}

export async function enqueueConflictResolution({
  inspection_id,
  conflict_id,
  resolution,
  resolved_value,
  reason,
}) {
  const q = await loadQueue();
  const id = newId('conflict');
  q.push({
    id,
    type: 'conflict_resolution',
    inspectionId: inspection_id,
    body: {
      inspection_id,
      conflict_id,
      resolution,
      resolved_value,
      reason,
    },
    createdAt: new Date().toISOString(),
    is_synced: false,
  });
  await saveQueue(q);
  return id;
}

const REVIEW_CACHE_KEY = 'nn_review_queue_cache';

export async function cacheReviewQueue(items, filterKey = 'ALL') {
  try {
    const suffix = filterKey && filterKey !== 'ALL' ? `_${filterKey}` : '';
    const key = `${REVIEW_CACHE_KEY}${suffix}`;
    if (isWeb) {
      globalThis.localStorage?.setItem(key, JSON.stringify(items || []));
      return;
    }
    await ensureDir();
    const cachePath = pendingDir() + `review_queue_cache${suffix}.json`;
    await FileSystem.writeAsStringAsync(cachePath, JSON.stringify(items || []));
  } catch {}
}

export async function loadCachedReviewQueue(filterKey = 'ALL') {
  try {
    const suffix = filterKey && filterKey !== 'ALL' ? `_${filterKey}` : '';
    const key = `${REVIEW_CACHE_KEY}${suffix}`;
    if (isWeb) {
      const raw = globalThis.localStorage?.getItem(key);
      if (!raw) return filterKey && filterKey !== 'ALL' ? null : [];
      return JSON.parse(raw);
    }
    await ensureDir();
    const cachePath = pendingDir() + `review_queue_cache${suffix}.json`;
    const info = await FileSystem.getInfoAsync(cachePath);
    if (!info.exists) {
      return filterKey && filterKey !== 'ALL' ? null : [];
    }
    const raw = await FileSystem.readAsStringAsync(cachePath);
    return JSON.parse(raw);
  } catch {
    return filterKey && filterKey !== 'ALL' ? null : [];
  }
}

const REVIEW_SUMMARY_CACHE_KEY = 'nn_review_summary_cache';

export async function cacheReviewSummary(summary) {
  try {
    if (isWeb) {
      globalThis.localStorage?.setItem(REVIEW_SUMMARY_CACHE_KEY, JSON.stringify(summary || null));
      return;
    }
    await ensureDir();
    const cachePath = pendingDir() + 'review_summary_cache.json';
    await FileSystem.writeAsStringAsync(cachePath, JSON.stringify(summary || null));
  } catch {}
}

export async function loadCachedReviewSummary() {
  try {
    if (isWeb) {
      const raw = globalThis.localStorage?.getItem(REVIEW_SUMMARY_CACHE_KEY);
      return raw ? JSON.parse(raw) : null;
    }
    await ensureDir();
    const cachePath = pendingDir() + 'review_summary_cache.json';
    const info = await FileSystem.getInfoAsync(cachePath);
    if (!info.exists) return null;
    const raw = await FileSystem.readAsStringAsync(cachePath);
    return JSON.parse(raw);
  } catch {
    return null;
  }
}

export async function enqueueRecaptureTaskFulfillment({
  taskId,
  inspectionId,
  scanImageId,
  notes,
  skipReason,
}) {
  const q = await loadQueue();
  const id = newId('recapture_fulfill');
  q.push({
    id,
    type: 'recapture_fulfillment',
    taskId,
    inspectionId,
    body: {
      inspection_id: inspectionId,
      scan_image_id: scanImageId || null,
      notes: notes || null,
      skip_reason: skipReason || null,
    },
    createdAt: new Date().toISOString(),
    is_synced: false,
  });
  await saveQueue(q);
  return id;
}

export async function enqueueReassessmentTrigger({
  inspectionId,
  scanIds,
}) {
  const q = await loadQueue();
  const id = newId('reassess');
  q.push({
    id,
    type: 'reassessment_trigger',
    inspectionId,
    body: {
      inspection_id: inspectionId,
      scan_ids: scanIds,
    },
    createdAt: new Date().toISOString(),
    is_synced: false,
  });
  await saveQueue(q);
  return id;
}

const RECAPTURE_CACHE_PREFIX = 'nn_recapture_tasks_';

export async function cacheRecaptureTasks(inspectionId, tasks) {
  try {
    const key = `${RECAPTURE_CACHE_PREFIX}${inspectionId}`;
    if (isWeb) {
      globalThis.localStorage?.setItem(key, JSON.stringify(tasks || []));
      return;
    }
    await ensureDir();
    const cachePath = pendingDir() + `recapture_tasks_${inspectionId}.json`;
    await FileSystem.writeAsStringAsync(cachePath, JSON.stringify(tasks || []));
  } catch {}
}

export async function loadCachedRecaptureTasks(inspectionId) {
  try {
    const key = `${RECAPTURE_CACHE_PREFIX}${inspectionId}`;
    if (isWeb) {
      const raw = globalThis.localStorage?.getItem(key);
      return raw ? JSON.parse(raw) : [];
    }
    await ensureDir();
    const cachePath = pendingDir() + `recapture_tasks_${inspectionId}.json`;
    const info = await FileSystem.getInfoAsync(cachePath);
    if (!info.exists) return [];
    const raw = await FileSystem.readAsStringAsync(cachePath);
    return JSON.parse(raw);
  } catch {
    return [];
  }
}

const REPORT_CACHE_PREFIX = 'nn_report_summary_';
const DOSSIER_CACHE_PREFIX = 'nn_violation_dossier_';

export async function cacheReportSummary(inspectionId, summary) {
  try {
    const key = `${REPORT_CACHE_PREFIX}${inspectionId}`;
    if (isWeb) {
      globalThis.localStorage?.setItem(key, JSON.stringify(summary || null));
      return;
    }
    await ensureDir();
    const cachePath = pendingDir() + `report_summary_${inspectionId}.json`;
    await FileSystem.writeAsStringAsync(cachePath, JSON.stringify(summary || null));
  } catch {}
}

export async function loadCachedReportSummary(inspectionId) {
  try {
    const key = `${REPORT_CACHE_PREFIX}${inspectionId}`;
    if (isWeb) {
      const raw = globalThis.localStorage?.getItem(key);
      return raw ? JSON.parse(raw) : null;
    }
    await ensureDir();
    const cachePath = pendingDir() + `report_summary_${inspectionId}.json`;
    const info = await FileSystem.getInfoAsync(cachePath);
    if (!info.exists) return null;
    const raw = await FileSystem.readAsStringAsync(cachePath);
    return JSON.parse(raw);
  } catch {
    return null;
  }
}

export async function cacheViolationDossier(identifier, dossier) {
  try {
    const key = `${DOSSIER_CACHE_PREFIX}${identifier}`;
    if (isWeb) {
      globalThis.localStorage?.setItem(key, JSON.stringify(dossier || null));
      return;
    }
    await ensureDir();
    const cachePath = pendingDir() + `dossier_${identifier}.json`;
    await FileSystem.writeAsStringAsync(cachePath, JSON.stringify(dossier || null));
  } catch {}
}

export async function loadCachedViolationDossier(identifier) {
  try {
    const key = `${DOSSIER_CACHE_PREFIX}${identifier}`;
    if (isWeb) {
      const raw = globalThis.localStorage?.getItem(key);
      return raw ? JSON.parse(raw) : null;
    }
    await ensureDir();
    const cachePath = pendingDir() + `dossier_${identifier}.json`;
    const info = await FileSystem.getInfoAsync(cachePath);
    if (!info.exists) return null;
    const raw = await FileSystem.readAsStringAsync(cachePath);
    return JSON.parse(raw);
  } catch {
    return null;
  }
}

