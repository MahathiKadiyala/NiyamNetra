import React, { createContext, useContext, useEffect, useState, useCallback, useRef } from 'react';
import { AppState, Platform } from 'react-native';
import * as Network from 'expo-network';
import { api } from '../api/client';
import {
  loadQueue,
  saveQueue,
  markSynced,
  markFailed,
  purgeSynced,
  purgeSyncedOnBoot,
  queueSize,
  syncFailedCount,
  retryFailed,
  invalidateReviewQueue,
} from './queue';
import { uploadWithBoundedRetry } from '../api/resilientUpload';

const SyncContext = createContext({
  pending: 0,
  failedCount: 0,
  isSyncing: false,
  syncNow: async () => {},
  retryFailedSync: async () => {},
  lastSync: null,
});
export const useSync = () => useContext(SyncContext);

/**
 * Connectivity check that cannot throw. expo-network is not reliable on the web
 * build, and an unguarded reject here used to surface as an unhandled rejection
 * on a screen that showed nothing — so web uses navigator.onLine instead.
 */
async function isOnline() {
  try {
    if (Platform.OS === 'web') {
      return typeof navigator === 'undefined' ? true : navigator.onLine !== false;
    }
    const st = await Network.getNetworkStateAsync();
    return !!st.isConnected;
  } catch {
    return false;
  }
}

async function isReachable() {
  try {
    const net = await isOnline();
    if (!net) return false;
    const res = await api.get('/health', { timeout: 4000 });
    return res?.status >= 200 && res?.status < 300;
  } catch {
    return false;
  }
}

// 4xx (except 408/429) means the server understood and refused: retrying the
// identical body will refuse identically forever. Dead-letter it.
function isDeadLetter(status, code) {
  if (status === 408 || status === 429) return false;
  if (code === 'ECONNABORTED') return false;
  if (status == null) return false; // network/timeout — retryable
  return status === 400 || status === 403 || status === 404 || status === 409
    || status === 413 || status === 422 || (status >= 400 && status < 500);
}

function statusOf(e) {
  return e?.status ?? e?.response?.status ?? null;
}

function guessMime(uri) {
  const u = String(uri || '').toLowerCase();
  if (u.endsWith('.png')) return 'image/png';
  if (u.endsWith('.webp')) return 'image/webp';
  return 'image/jpeg';
}

// Upload one evidence file to POST /scans/{serverScanId}/images as multipart
// (panel, file). Backend: routers/scans.py upload_image — panel must be one of
// front/back/side/mrp/batch/other; anything else 422s, so unknown labels map
// to "other" rather than failing the whole inspection.
async function uploadEvidenceFile(serverScanId, file, index) {
  const rawPanel = String(file?.panel || (index === 0 ? 'front' : 'other')).trim().toLowerCase();
  const allowed = new Set(['front', 'back', 'side', 'mrp', 'batch', 'other']);
  const panel = allowed.has(rawPanel) ? rawPanel : 'other';
  const uri = file?.uri;
  const form = new FormData();
  form.append('panel', panel);
  form.append('file', {
    uri,
    name: `panel-${panel}-${index}.jpg`,
    type: guessMime(uri),
  });
  // Do NOT set Content-Type here. Letting axios/React Native build it is the
  // only way the multipart boundary is generated; an explicit value arrives
  // without a boundary on some RN builds and the backend cannot split parts.
  return api.post(`/scans/${serverScanId}/images`, form, { timeout: 60000 });
}

export function SyncProvider({ children }) {
  const [pending, setPending] = useState(0);
  const [failedCount, setFailedCount] = useState(0);
  const [isSyncing, setIsSyncing] = useState(false);
  const [lastSync, setLastSync] = useState(null);
  // A ref, not the state value: syncNow is held by a 30s interval, and reading
  // isSyncing from a stale closure let two passes overlap.
  const busy = useRef(false);
  // After a pass where nothing synced, park the automatic 30s timer for a
  // while so a downed backend is not polled forever. Progressive: 60s, 2min,
  // 4min … capped at 10min. Manual syncNow() calls are never gated.
  const backoffUntil = useRef(0);
  const consecutiveFailures = useRef(0);

  const refreshCount = useCallback(async () => {
    try {
      setPending(await queueSize());
      setFailedCount(await syncFailedCount());
    } catch { /* queue unavailable (web) */ }
  }, []);

  // Local inspection id → server inspection id, learned from POST /inspections
  // responses. Scan items only hold the LOCAL parent id, so without this map
  // there is no correct URL to post them to. In-memory only: after a restart,
  // inspections sync first in the same pass and re-populate it before their
  // child scans are attempted.
  const remoteIdByLocal = useRef({});

  const syncNow = useCallback(async () => {
    if (busy.current) return;
    let toSync = [];
    try {
      const q = await loadQueue();
      // Skip dead-lettered rows: they were refused (400/403/404/422) and
      // retrying them would loop forever on the identical body.
      toSync = q.filter((x) => !x.is_synced && !x.syncFailed);
    } catch {
      return; // no queue on this platform
    }
    if (toSync.length === 0) { await refreshCount(); return; }

    // Violations first: an item opts in via body.result/item.result ===
    // 'violation' (provisional officer flag) or body.violationSuspected. This
    // is a nicety only — without the flag the pass is strict FIFO with parent
    // inspections before their child scans so the id map is populated in time.
    const prio = (x) => (
      x.result === 'violation' || x.body?.result === 'violation' || x.violationSuspected || x.body?.violationSuspected ? 0 : 1
    );
    toSync.sort((a, b) =>
      prio(a) - prio(b) ||
      (a.type === b.type ? 0 : a.type === 'inspection' ? -1 : 1) ||
      String(a.createdAt || '').localeCompare(String(b.createdAt || '')),
    );

    busy.current = true;
    setIsSyncing(true);
    let anySuccess = false;
    try {
      for (const item of toSync) {
        let delay = 1000;
        let done = false;
        for (let attempt = 0; attempt < 3 && !done; attempt++) {
          try {
            if (item.type === 'inspection') {
              // POST /inspections — no trailing slash. With a slash Starlette
              // answers 307 and the redirected request needs its own CORS
              // preflight for Idempotency-Key on the web build.
              const resp = await api.post(
                '/inspections',
                { ...item.body, local_created_at: item.createdAt },
                { headers: { 'Idempotency-Key': item.id } },
              );
              const serverId = resp?.data?.id ?? resp?.data?.inspection_id;
              if (serverId) remoteIdByLocal.current[item.id] = serverId;

              // --- evidence upload (multipart) ---------------------------------
              // The inspection POST carries JSON metadata only. Every captured
              // file must reach POST /scans/{scanId}/images or the photos are
              // lost the moment we purge. So: create scans under the new
              // inspection, upload each file to it, and only then mark synced.
              if (serverId) {
                const scanItems = Array.isArray(item.scans) && item.scans.length > 0
                  ? item.scans
                  : [{
                      commodity_generic: item.scanBody?.commodity_generic || null,
                      brand_name: item.scanBody?.brand_name || null,
                      batch_number: item.scanBody?.batch_number || null,
                      geometry: item.scanBody?.geometry,
                      files: item.files?.length
                        ? item.files
                        : (item.fileUris || []).map((uri, i) => ({ uri, panel: i === 0 ? 'front' : 'other' })),
                    }];

                for (let sIdx = 0; sIdx < scanItems.length; sIdx++) {
                  const s = scanItems[sIdx];
                  const files = s.files?.length
                    ? s.files
                    : (s.fileUris || []).map((uri, i) => ({ uri, panel: i === 0 ? 'front' : 'other' }));

                  const geometry = s.geometry || {
                    panel_shape: 'rectangular',
                    panel_height_mm: null,
                    panel_width_mm: null,
                    is_blown_moulded: false,
                    scale_source: 'none',
                  };

                  const scanResp = await api.post(
                    `/inspections/${serverId}/scans`,
                    {
                      commodity_generic: s.commodity_generic || null,
                      brand_name: s.brand_name || null,
                      batch_number: s.batch_number || null,
                      geometry,
                    },
                    { headers: { 'Idempotency-Key': `${item.id}-scan-${sIdx}` } },
                  );
                  const serverScanId = scanResp?.data?.scan_id ?? scanResp?.data?.id;
                  if (!serverScanId) throw new Error('Scan created but returned no id');

                  // Patch scope flags if present
                  try {
                    await api.patch(`/scans/${serverScanId}`, {
                      is_imported: s.is_imported ?? null,
                      is_perishable: s.is_perishable ?? null,
                      has_sticker: s.has_sticker ?? null,
                    });
                  } catch (patchErr) {
                    if (__DEV__) console.warn('[sync] patch scope failed:', patchErr?.message);
                  }

                  for (let i = 0; i < files.length; i++) {
                    const f = files[i];
                    if (f.is_uploaded || f.image_id) continue;
                    // eslint-disable-next-line no-await-in-loop
                    const uploadRes = await uploadWithBoundedRetry(
                      () => uploadEvidenceFile(serverScanId, f, i),
                      { maxRetries: 2, baseDelayMs: 400 }
                    );
                    f.is_uploaded = true;
                    f.image_id = uploadRes?.data?.image_id ?? uploadRes?.data?.id;
                    try {
                      const currentQ = await loadQueue();
                      const qIdx = currentQ.findIndex((x) => x.id === item.id);
                      if (qIdx >= 0) {
                        currentQ[qIdx] = item;
                        await saveQueue(currentQ);
                      }
                    } catch {}
                  }

                  // Run statutory assessment across all 19 rules
                  try {
                    await api.post(`/scans/${serverScanId}/assess`, undefined, { timeout: 60000 });
                  } catch (e) {
                    if (__DEV__) console.warn('[sync] assess failed (evidence kept):', e?.message || e);
                  }
                }

                // Transition inspection from 'draft' to 'submitted'
                try {
                  const sigStatus = item.signature_status || item.body?.signature_status || 'signed';
                  const notes = item.notes || item.body?.notes || '';
                  await api.post(`/inspections/${serverId}/submit`, {
                    signature_status: sigStatus,
                    notes,
                  });
                } catch (submitErr) {
                  if (submitErr?.response?.status !== 409 && __DEV__) {
                    console.warn('[sync] inspection submit warning:', submitErr?.message || submitErr);
                  }
                }
              }
              // Only purge files AFTER all uploads succeed: markSynced is the
              // gate purgeSynced reads, so reaching here means the bytes are
              // server-side.
              await markSynced(item.id);
              anySuccess = true;
              done = true;
            } else if (item.type === 'scan') {
              // A scan is created at POST /inspections/{server_id}/scans, so
              // it needs the parent's SERVER id. Resolve it from this pass's
              // inspection responses; if the parent hasn't synced yet, leave
              // this scan queued.
              const parentServerId = remoteIdByLocal.current[item.parentId];
              if (parentServerId == null) break;

              const scanBody = { ...item.body };
              if (!scanBody.geometry) {
                scanBody.geometry = {
                  panel_shape: 'rectangular',
                  panel_height_mm: null,
                  panel_width_mm: null,
                  is_blown_moulded: false,
                  scale_source: 'none',
                };
              }

              const scanResp = await api.post(
                `/inspections/${parentServerId}/scans`,
                scanBody,
                { headers: { 'Idempotency-Key': item.id } },
              );
              const serverScanId = scanResp?.data?.scan_id ?? scanResp?.data?.id;

              if (item.body?.is_imported !== undefined || item.body?.has_sticker !== undefined) {
                try {
                  await api.patch(`/scans/${serverScanId}`, {
                    is_imported: item.body.is_imported ?? null,
                    is_perishable: item.body.is_perishable ?? null,
                    has_sticker: item.body.has_sticker ?? null,
                  });
                } catch {}
              }

              const files = item.files?.length
                ? item.files
                : (item.fileUris || []).map((uri, i) => ({ uri, panel: i === 0 ? 'front' : 'other' }));
              if (serverScanId && files.length > 0) {
                for (let i = 0; i < files.length; i++) {
                  const f = files[i];
                  if (f.is_uploaded || f.image_id) continue;
                  // eslint-disable-next-line no-await-in-loop
                  const uploadRes = await uploadWithBoundedRetry(
                    () => uploadEvidenceFile(serverScanId, f, i),
                    { maxRetries: 2, baseDelayMs: 400 }
                  );
                  f.is_uploaded = true;
                  f.image_id = uploadRes?.data?.image_id ?? uploadRes?.data?.id;
                  try {
                    const currentQ = await loadQueue();
                    const qIdx = currentQ.findIndex((x) => x.id === item.id);
                    if (qIdx >= 0) {
                      currentQ[qIdx] = item;
                      await saveQueue(currentQ);
                    }
                  } catch {}
                }
                try {
                  await api.post(`/scans/${serverScanId}/assess`, undefined, { timeout: 60000 });
                } catch (e) {
                  if (__DEV__) console.warn('[sync] assess failed (evidence kept):', e?.message || e);
                }
              }
              await markSynced(item.id);
              anySuccess = true;
              done = true;
            } else if (item.type === 'adjudication') {
              const inspId = item.inspectionId || item.body?.inspection_id;
              await api.post(`/review/inspections/${inspId}/adjudicate`, item.body, {
                headers: { 'Idempotency-Key': item.id },
              });
              await markSynced(item.id);
              anySuccess = true;
              done = true;
            } else if (item.type === 'conflict_resolution') {
              const inspId = item.inspectionId || item.body?.inspection_id;
              const conflictId = item.body?.conflict_id;
              await api.post(`/review/inspections/${inspId}/conflicts/${conflictId}/resolve`, item.body, {
                headers: { 'Idempotency-Key': item.id },
              });
              await markSynced(item.id);
              anySuccess = true;
              done = true;
            } else if (item.type === 'recapture_fulfillment') {
              const taskId = item.taskId;
              const inspId = item.inspectionId || item.body?.inspection_id;
              await api.post(`/recapture/inspections/${inspId}/tasks/${taskId}/fulfill`, item.body, {
                headers: { 'Idempotency-Key': item.id },
              });
              await markSynced(item.id);
              anySuccess = true;
              done = true;
            } else if (item.type === 'reassessment_trigger') {
              const inspId = item.inspectionId || item.body?.inspection_id;
              await api.post(`/recapture/inspections/${inspId}/reassess`, item.body, {
                headers: { 'Idempotency-Key': item.id },
              });
              await markSynced(item.id);
              anySuccess = true;
              done = true;
            } else {
              done = true; // unknown type — leave queued, do not spin
            }
          } catch (e) {
            const status = statusOf(e);
            if (isDeadLetter(status, e?.code)) {
              // Refused, not failed: record the error and stop retrying this
              // item. The row stays visible (syncFailed) instead of looping.
              try { await markFailed(item.id, e?.response?.data?.detail || e); } catch {}
              done = true;
              break;
            }
            if (attempt === 2) break; // 5xx/network/timeout: max 3 tries
            await new Promise((r) => setTimeout(r, delay));
            delay *= 2;
          }
        }
      }
      try { await purgeSynced(); } catch { /* best effort */ }
      await refreshCount();
      // Only advertise a sync time when at least one item actually synced —
      // otherwise a failed pass looks like a successful one.
      if (anySuccess) {
        setLastSync(new Date().toISOString());
        consecutiveFailures.current = 0;
        backoffUntil.current = 0;
        try {
          await invalidateReviewQueue(null, 'offline_sync');
        } catch {}
      } else {
        // Park the automatic timer (see backoffUntil). A manual pull is not
        // affected — the gate lives in maybeSync below, not in syncNow.
        consecutiveFailures.current += 1;
        const waitMs = Math.min(60000 * 2 ** (consecutiveFailures.current - 1), 600000);
        backoffUntil.current = Date.now() + waitMs;
      }
    } finally {
      busy.current = false;
      setIsSyncing(false);
    }
  }, [refreshCount]);

  const retryFailedSync = useCallback(async (id) => {
    await retryFailed(id);
    await refreshCount();
    await syncNow();
  }, [refreshCount, syncNow]);

  useEffect(() => {
    // Idempotent startup purge: collect is_synced leftovers from a crash
    // between markSynced and purgeSynced, then count what is truly pending.
    purgeSyncedOnBoot().finally(() => refreshCount());
    // Automatic attempts only: skip while the failure backoff is running.
    // Manual syncNow() from SyncStrip always runs immediately.
    const maybeSync = () => {
      if (Date.now() < backoffUntil.current) return;
      isReachable().then((ok) => { if (ok) syncNow(); }).catch(() => {});
    };
    const sub = AppState.addEventListener('change', (s) => {
      if (s !== 'active') return;
      maybeSync();
    });
    const interval = setInterval(maybeSync, 30000);
    return () => { sub.remove(); clearInterval(interval); };
  }, [refreshCount, syncNow]);

  return (
    <SyncContext.Provider value={{ pending, failedCount, isSyncing, syncNow, retryFailedSync, refreshCount, lastSync }}>
      {children}
    </SyncContext.Provider>
  );
}
