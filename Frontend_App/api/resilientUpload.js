// api/resilientUpload.js — Resilient Panel Evidence Upload Orchestrator

/**
 * Classifies whether an error from an upload/network operation is transient
 * (eligible for bounded retry) or permanent (client/validation/auth error).
 *
 * Transient:
 *  - Network disconnections, dropped sockets, DNS failures (no response object)
 *  - Request timeouts (ECONNABORTED, timeout in message, status 408)
 *  - Rate limiting (HTTP 429)
 *  - Server errors (HTTP 500, 502, 503, 504)
 *
 * Permanent (fail fast, do not retry blindly):
 *  - Client input errors: HTTP 400 (Bad Request), 422 (Unprocessable Entity)
 *  - Authentication & permission: HTTP 401 (Unauthorized), 403 (Forbidden)
 *  - Not found: HTTP 404 (Scan or Inspection deleted)
 *  - Entity too large: HTTP 413 (Exceeds size ceiling)
 *  - Conflict: HTTP 409 (Inspection already submitted)
 */
export function isTransientError(error) {
  if (!error) return false;

  const status = error.status ?? error.response?.status;
  const code = error.code ?? error.response?.data?.code;
  const msg = String(error.message || error.response?.data?.detail || '').toLowerCase();

  // If no HTTP response was received (network drop, offline, abort, timeout)
  if (!error.response && (status == null || status === 0)) {
    return true;
  }

  // Explicit timeout / network error codes
  if (code === 'ECONNABORTED' || code === 'ETIMEDOUT' || code === 'ENOTFOUND' || code === 'ECONNRESET') {
    return true;
  }

  if (msg.includes('timeout') || msg.includes('network error') || msg.includes('network request failed')) {
    return true;
  }

  // HTTP status checks
  if (status === 408 || status === 429) {
    return true;
  }

  // 5xx Server errors are transient
  if (typeof status === 'number' && status >= 500 && status < 600) {
    return true;
  }

  // Permanent client errors (4xx except 408/429)
  if (typeof status === 'number' && status >= 400 && status < 500) {
    return false;
  }

  return false;
}

/**
 * Executes an async upload operation with bounded retries and exponential backoff.
 *
 * @param {Function} uploadFn - Async function returning a promise
 * @param {object} options
 * @param {number} [options.maxRetries=2] - Number of retries after initial attempt (total attempts = maxRetries + 1)
 * @param {number} [options.baseDelayMs=400] - Base backoff delay in ms
 * @param {number} [options.backoffFactor=1.5] - Multiplier for each subsequent retry
 * @param {Function} [options.onRetry] - Callback invoked before each retry: ({ attempt, error, delayMs })
 * @param {Function} [options.isCancelled] - Function returning true if the operation should abort
 * @returns {Promise<any>}
 */
export async function uploadWithBoundedRetry(uploadFn, options = {}) {
  const {
    maxRetries = 2,
    baseDelayMs = 400,
    backoffFactor = 1.5,
    onRetry = null,
    isCancelled = null,
  } = options;

  let attempt = 0;
  while (true) {
    attempt++;
    if (isCancelled && isCancelled()) {
      const abortErr = new Error('Upload cancelled');
      abortErr.isCancelled = true;
      throw abortErr;
    }

    try {
      return await uploadFn();
    } catch (err) {
      if (isCancelled && isCancelled()) {
        const abortErr = new Error('Upload cancelled');
        abortErr.isCancelled = true;
        throw abortErr;
      }

      const transient = isTransientError(err);
      if (!transient || attempt > maxRetries) {
        // Permanent error or retries exhausted: attach attempt metadata and rethrow
        err.attemptsMade = attempt;
        err.isTransient = transient;
        throw err;
      }

      // Compute backoff with slight jitter to prevent synchronized retry storms
      const jitter = 0.8 + Math.random() * 0.4; // 0.8x - 1.2x
      const delayMs = Math.round(baseDelayMs * Math.pow(backoffFactor, attempt - 1) * jitter);

      if (onRetry) {
        try {
          onRetry({ attempt, error: err, delayMs });
        } catch {
          // ignore callback error
        }
      }

      // Bounded wait
      await new Promise((resolve) => setTimeout(resolve, delayMs));
    }
  }
}

/**
 * Uploads all captured panels for a scan independently and concurrently.
 *
 * Skips panels that are already successfully uploaded.
 * Tracks outcomes per-panel without letting a single panel failure cancel
 * or invalidate successfully uploaded panels.
 *
 * @param {object} params
 * @param {number|string} params.serverScanId - Server scan ID
 * @param {Array<{ panelKey: string, uri: string, evidence?: object }>} params.panels - Panels to upload
 * @param {object} [params.currentStatus={}] - Map of panelKey -> status object
 * @param {Function} [params.uploadFn=uploadScanImage] - Upload implementation
 * @param {number} [params.maxRetries=2] - Max retries per panel
 * @param {number} [params.baseDelayMs=400] - Base delay in ms
 * @param {Function} [params.onPanelStatusChange] - (panelKey, newStatus) => void
 * @param {Function} [params.isCancelled] - () => boolean
 * @returns {Promise<{
 *   allUploaded: boolean,
 *   hasFailures: boolean,
 *   statusMap: object,
 *   uploadedCount: number,
 *   failedCount: number,
 *   successfulPanels: Array<string>,
 *   failedPanels: Array<string>,
 *   firstError: string|null,
 * }>}
 */
export async function uploadScanPanelsResiliently({
  serverScanId,
  panels = [],
  currentStatus = {},
  uploadFn = null,
  maxRetries = 2,
  baseDelayMs = 400,
  onPanelStatusChange = null,
  isCancelled = null,
}) {
  if (!serverScanId) {
    throw new Error('uploadScanPanelsResiliently: serverScanId is required');
  }

  let activeUploadFn = uploadFn;
  if (!activeUploadFn) {
    try {
      const insp = await import('./inspections.js');
      activeUploadFn = insp.uploadScanImage;
    } catch {
      throw new Error('uploadScanPanelsResiliently: uploadFn is required');
    }
  }

  const statusMap = { ...currentStatus };

  const notify = (panelKey, statusObj) => {
    statusMap[panelKey] = statusObj;
    if (onPanelStatusChange) {
      try {
        onPanelStatusChange(panelKey, statusObj);
      } catch {
        // ignore callback error
      }
    }
  };

  const tasks = panels.map(async ({ panelKey, uri, evidence }) => {
    // 1. Skip already successfully uploaded panels (duplicate protection)
    const existing = statusMap[panelKey];
    if (existing && (existing.status === 'uploaded' || existing.state === 'uploaded') && existing.imageId) {
      return { panelKey, ...existing };
    }

    // 2. Mark panel as uploading
    notify(panelKey, {
      status: 'uploading',
      state: 'uploading',
      imageId: existing?.imageId || null,
      sha256: existing?.sha256 || null,
      error: null,
      attempts: 0,
      uri: uri || evidence?.analysis_uri || evidence?.original_uri,
    });

    const uploadTargetUri = uri || evidence?.analysis_uri || evidence?.original_uri;
    if (!uploadTargetUri) {
      const errState = {
        status: 'failed',
        state: 'failed',
        imageId: null,
        error: 'No image URI available for upload',
        isTransient: false,
        attempts: 1,
      };
      notify(panelKey, errState);
      return { panelKey, ...errState };
    }

    try {
      let attemptsCount = 0;
      const res = await uploadWithBoundedRetry(
        async () => {
          attemptsCount++;
          return await activeUploadFn(serverScanId, panelKey, uploadTargetUri);
        },
        {
          maxRetries,
          baseDelayMs,
          onRetry: ({ attempt, error, delayMs }) => {
            notify(panelKey, {
              status: 'uploading',
              state: 'uploading',
              imageId: null,
              retrying: true,
              retryAttempt: attempt,
              delayMs,
              error: error?.message || 'Retrying transient error',
              attempts: attempt,
              uri: uploadTargetUri,
            });
          },
          isCancelled,
        }
      );

      const successState = {
        status: 'uploaded',
        state: 'uploaded',
        imageId: res?.id ?? res?.image_id,
        sha256: res?.sha256,
        replayed: Boolean(res?.replayed),
        error: null,
        attempts: attemptsCount,
        uri: uploadTargetUri,
        uploadedAt: new Date().toISOString(),
      };
      notify(panelKey, successState);
      return { panelKey, ...successState };
    } catch (uploadErr) {
      const errMsg =
        uploadErr?.response?.data?.detail ||
        uploadErr?.message ||
        'Upload failed';
      const failState = {
        status: 'failed',
        state: 'failed',
        imageId: null,
        error: errMsg,
        isTransient: isTransientError(uploadErr),
        attempts: uploadErr.attemptsMade || 1,
        uri: uploadTargetUri,
      };
      notify(panelKey, failState);
      return { panelKey, ...failState };
    }
  });

  // Use Promise.allSettled so every panel outcome is captured without early termination
  const results = await Promise.allSettled(tasks);

  const successfulPanels = [];
  const failedPanels = [];
  let firstError = null;

  panels.forEach(({ panelKey }) => {
    const st = statusMap[panelKey];
    if (st && st.status === 'uploaded') {
      successfulPanels.push(panelKey);
    } else {
      failedPanels.push(panelKey);
      if (!firstError && st?.error) {
        firstError = st.error;
      }
    }
  });

  const totalRequired = panels.length;
  const uploadedCount = successfulPanels.length;
  const failedCount = failedPanels.length;
  const allUploaded = totalRequired > 0 && uploadedCount === totalRequired;

  return {
    allUploaded,
    hasFailures: failedCount > 0,
    statusMap,
    uploadedCount,
    failedCount,
    successfulPanels,
    failedPanels,
    firstError,
  };
}

/**
 * Strict Assessment Gating:
 * Verifies that all captured panels have completed upload successfully
 * before triggering authoritative server assessment.
 *
 * @param {Array<string>} capturedPanelKeys - Keys of panels captured by inspector
 * @param {object} statusMap - Map of panelKey -> status object
 * @returns {{ canAssess: boolean, reason?: string, pendingPanels?: string[], failedPanels?: string[] }}
 */
export function canAssessPackage(capturedPanelKeys = [], statusMap = {}) {
  if (!capturedPanelKeys || capturedPanelKeys.length === 0) {
    return { canAssess: false, reason: 'At least one panel photograph is required before assessment.' };
  }

  const pending = [];
  const failed = [];

  for (const k of capturedPanelKeys) {
    const st = statusMap[k];
    const s = st?.status || st?.state || 'idle';
    if (s === 'failed') {
      failed.push(k);
    } else if (s === 'uploaded') {
      if (!st.imageId) {
        pending.push(k);
      }
    } else {
      pending.push(k);
    }
  }

  if (failed.length > 0) {
    return {
      canAssess: false,
      reason: `Cannot assess package: ${failed.length} panel(s) failed upload. Please retry the failed uploads.`,
      pendingPanels: pending,
      failedPanels: failed,
    };
  }

  if (pending.length > 0) {
    return {
      canAssess: false,
      reason: `Cannot assess package: ${pending.length} panel(s) are still pending upload.`,
      pendingPanels: pending,
      failedPanels: failed,
    };
  }

  return { canAssess: true };
}
