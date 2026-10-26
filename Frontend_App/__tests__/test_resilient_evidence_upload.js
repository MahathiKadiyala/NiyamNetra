// Frontend_App/__tests__/test_resilient_evidence_upload.js
// Regression test suite for Phase 2 Module 5: Make Evidence Uploads Resilient.

const assert = require('assert');
const path = require('path');
const fs = require('fs');

console.log('=== Running Module 5 Resilient Evidence Upload Tests ===\n');

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
  const resilientModule = await import('../api/resilientUpload.js');
  const {
    isTransientError,
    uploadWithBoundedRetry,
    uploadScanPanelsResiliently,
    canAssessPackage,
  } = resilientModule;

  // 1. All panel uploads succeed normally
  await itAsync('1. All panel uploads succeed normally and record server image IDs', async () => {
    const uploadCalls = [];
    const mockUploadFn = async (scanId, panel, uri) => {
      uploadCalls.push({ scanId, panel, uri });
      return { id: `img-${panel}-100`, sha256: `sha-${panel}`, replayed: false };
    };

    const panels = [
      { panelKey: 'front', uri: 'file:///data/front.jpg' },
      { panelKey: 'back', uri: 'file:///data/back.jpg' },
      { panelKey: 'mrp', uri: 'file:///data/mrp.jpg' },
    ];

    const outcome = await uploadScanPanelsResiliently({
      serverScanId: 42,
      panels,
      currentStatus: {},
      uploadFn: mockUploadFn,
      maxRetries: 2,
      baseDelayMs: 10,
    });

    assert.strictEqual(outcome.allUploaded, true, 'All panels should be marked as uploaded');
    assert.strictEqual(outcome.hasFailures, false, 'No failures expected');
    assert.strictEqual(outcome.uploadedCount, 3);
    assert.strictEqual(outcome.failedCount, 0);
    assert.strictEqual(uploadCalls.length, 3, 'Upload function should be called 3 times');
    assert.strictEqual(outcome.statusMap.front.imageId, 'img-front-100');
    assert.strictEqual(outcome.statusMap.back.imageId, 'img-back-100');
    assert.strictEqual(outcome.statusMap.mrp.imageId, 'img-mrp-100');
  });

  // 2. One panel fails temporarily while other panels upload successfully
  await itAsync('2. One panel fails temporarily (transient 503) while other panels upload successfully; independent outcomes preserved', async () => {
    let mrpAttempts = 0;
    const mockUploadFn = async (scanId, panel, uri) => {
      if (panel === 'mrp') {
        mrpAttempts++;
        const err = new Error('503 Service Unavailable');
        err.response = { status: 503, data: { detail: 'Storage node temporarily unreachable' } };
        throw err;
      }
      return { id: `img-${panel}-200`, sha256: `sha-${panel}` };
    };

    const panels = [
      { panelKey: 'front', uri: 'file:///data/front.jpg' },
      { panelKey: 'mrp', uri: 'file:///data/mrp.jpg' },
    ];

    const outcome = await uploadScanPanelsResiliently({
      serverScanId: 55,
      panels,
      currentStatus: {},
      uploadFn: mockUploadFn,
      maxRetries: 2, // 1 initial + 2 retries = 3 attempts
      baseDelayMs: 10,
    });

    assert.strictEqual(outcome.allUploaded, false, 'Package should not be all uploaded due to mrp failure');
    assert.strictEqual(outcome.hasFailures, true, 'Failures should be flagged');
    assert.strictEqual(outcome.uploadedCount, 1);
    assert.strictEqual(outcome.failedCount, 1);
    assert.deepStrictEqual(outcome.successfulPanels, ['front']);
    assert.deepStrictEqual(outcome.failedPanels, ['mrp']);
    assert.strictEqual(mrpAttempts, 3, 'Should retry transient error up to maxRetries (3 attempts total)');

    // Crucial: Front upload was NOT discarded or corrupted by MRP failure
    assert.strictEqual(outcome.statusMap.front.status, 'uploaded');
    assert.strictEqual(outcome.statusMap.front.imageId, 'img-front-200');
    assert.strictEqual(outcome.statusMap.mrp.status, 'failed');
    assert.strictEqual(outcome.statusMap.mrp.isTransient, true);
  });

  // 3. A retry of the failed panel preserves already-uploaded evidence
  await itAsync('3. Retry of failed panel skips already-uploaded panels and preserves prior evidence', async () => {
    const uploadedInSecondPass = [];
    const mockUploadFn = async (scanId, panel, uri) => {
      uploadedInSecondPass.push(panel);
      return { id: `img-${panel}-recovered`, sha256: `sha-${panel}-new` };
    };

    const existingStatus = {
      front: { status: 'uploaded', imageId: 'img-front-200', sha256: 'sha-front' },
      mrp: { status: 'failed', imageId: null, error: '503 Service Unavailable' },
    };

    const panels = [
      { panelKey: 'front', uri: 'file:///data/front.jpg' },
      { panelKey: 'mrp', uri: 'file:///data/mrp.jpg' },
    ];

    const outcome = await uploadScanPanelsResiliently({
      serverScanId: 55,
      panels,
      currentStatus: existingStatus,
      uploadFn: mockUploadFn,
      maxRetries: 2,
      baseDelayMs: 10,
    });

    assert.strictEqual(outcome.allUploaded, true, 'All panels should now be uploaded');
    assert.strictEqual(outcome.uploadedCount, 2);
    assert.strictEqual(outcome.failedCount, 0);

    // Front must NOT have been re-uploaded
    assert.deepStrictEqual(uploadedInSecondPass, ['mrp'], 'Only failed panel mrp should be uploaded');
    assert.strictEqual(outcome.statusMap.front.imageId, 'img-front-200', 'Original front imageId must be preserved');
    assert.strictEqual(outcome.statusMap.mrp.imageId, 'img-mrp-recovered');
  });

  // 4. A permanent validation error is not retried indefinitely
  await itAsync('4. Permanent validation/client errors (400, 413, 422) fail fast without retrying', async () => {
    let callCount = 0;
    const mockUploadFn = async () => {
      callCount++;
      const err = new Error('HTTP 422 Unprocessable Entity');
      err.response = { status: 422, data: { detail: 'Unsupported image color space' } };
      throw err;
    };

    try {
      await uploadWithBoundedRetry(mockUploadFn, { maxRetries: 3, baseDelayMs: 10 });
      assert.fail('Should have thrown permanent error');
    } catch (err) {
      assert.strictEqual(err.attemptsMade, 1, 'Permanent 422 error must fail on first attempt without retrying');
      assert.strictEqual(err.isTransient, false, 'isTransient should be false for 422');
      assert.strictEqual(callCount, 1, 'Upload function should only be invoked once');
    }

    // Verify error classification
    assert.strictEqual(isTransientError({ response: { status: 400 } }), false);
    assert.strictEqual(isTransientError({ response: { status: 401 } }), false);
    assert.strictEqual(isTransientError({ response: { status: 403 } }), false);
    assert.strictEqual(isTransientError({ response: { status: 404 } }), false);
    assert.strictEqual(isTransientError({ response: { status: 413 } }), false);
    assert.strictEqual(isTransientError({ response: { status: 422 } }), false);
    assert.strictEqual(isTransientError({ response: { status: 500 } }), true);
    assert.strictEqual(isTransientError({ response: { status: 502 } }), true);
    assert.strictEqual(isTransientError({ response: { status: 503 } }), true);
    assert.strictEqual(isTransientError({ response: { status: 504 } }), true);
    assert.strictEqual(isTransientError({ response: { status: 408 } }), true);
    assert.strictEqual(isTransientError({ response: { status: 429 } }), true);
    assert.strictEqual(isTransientError(new Error('Network request failed')), true);
    assert.strictEqual(isTransientError({ code: 'ECONNABORTED' }), true);
  });

  // 5. Offline interruption preserves pending evidence
  it('5. Offline interruption preserves pending evidence in scanItem without losing panels', () => {
    const sessionFile = path.join(__dirname, '../screens/inspector/InspectionSessionScreen.jsx');
    const sessionCode = fs.readFileSync(sessionFile, 'utf8');

    // Verify handleSaveCurrentPackageOffline preserves panelPhotos, panelEvidence, and panelUploadStatus
    assert.ok(sessionCode.includes('handleSaveCurrentPackageOffline'), 'Offline save handler must exist');
    assert.ok(sessionCode.includes('panelUploadStatus: { ...currentStatusMap }'), 'Must attach currentStatusMap to offline package');
    assert.ok(sessionCode.includes('is_offline: true'), 'Must mark scanItem as is_offline');
    assert.ok(sessionCode.includes('overall_result: \'not_assessed\''), 'Offline item must remain not_assessed');
  });

  // 6. Returning online resumes pending uploads without duplicating successful uploads
  it('6. Offline sync resumes pending uploads and skips already-uploaded files (f.is_uploaded || f.image_id)', () => {
    const syncFile = path.join(__dirname, '../offline/SyncProvider.js');
    const syncCode = fs.readFileSync(syncFile, 'utf8');

    // Both inspection-scans and standalone scan loops must skip uploaded files
    assert.ok(
      syncCode.includes('if (f.is_uploaded || f.image_id) continue;'),
      'SyncProvider must skip already uploaded files'
    );
    assert.ok(
      syncCode.includes('uploadWithBoundedRetry'),
      'SyncProvider must use uploadWithBoundedRetry'
    );
    assert.ok(
      syncCode.includes('saveQueue(currentQ)'),
      'SyncProvider must persist intermediate file upload progress to offline queue'
    );
  });

  // 7. Concurrent retries or repeated user taps do not cause duplicate operations
  it('7. Concurrent retries or repeated user taps are blocked by assessingRef guard', () => {
    const sessionFile = path.join(__dirname, '../screens/inspector/InspectionSessionScreen.jsx');
    const sessionCode = fs.readFileSync(sessionFile, 'utf8');

    assert.ok(
      sessionCode.includes('if (assessingRef.current) return;'),
      'handleAssessCurrentPackage must bail out if assessingRef.current is true'
    );
    assert.ok(
      sessionCode.includes('assessingRef.current = true;'),
      'handleAssessCurrentPackage must set assessingRef.current to true immediately'
    );
    assert.ok(
      sessionCode.includes('assessingRef.current = false;'),
      'handleAssessCurrentPackage must reset assessingRef.current in finally block'
    );
  });

  // 8. Assessment cannot complete while required evidence is still pending or failed
  it('8. Assessment gating strictly prevents assessment when evidence is pending or failed', () => {
    // Zero panels
    const gateZero = canAssessPackage([], {});
    assert.strictEqual(gateZero.canAssess, false);
    assert.ok(gateZero.reason.includes('At least one panel photograph is required'));

    // 2 captured panels, 1 uploaded, 1 still failed
    const captured = ['front', 'back'];
    const statusMapPartial = {
      front: { status: 'uploaded', imageId: 'img-1' },
      back: { status: 'failed', imageId: null, error: 'Connection reset' },
    };
    const gatePartial = canAssessPackage(captured, statusMapPartial);
    assert.strictEqual(gatePartial.canAssess, false);
    assert.deepStrictEqual(gatePartial.failedPanels, ['back']);
    assert.ok(gatePartial.reason.includes('failed upload'));

    // 2 captured panels, 1 uploaded, 1 idle/uploading
    const statusMapPending = {
      front: { status: 'uploaded', imageId: 'img-1' },
      back: { status: 'uploading', imageId: null },
    };
    const gatePending = canAssessPackage(captured, statusMapPending);
    assert.strictEqual(gatePending.canAssess, false);
    assert.deepStrictEqual(gatePending.pendingPanels, ['back']);

    // Both successfully uploaded with imageIds
    const statusMapAll = {
      front: { status: 'uploaded', imageId: 'img-1' },
      back: { status: 'uploaded', imageId: 'img-2' },
    };
    const gateAll = canAssessPackage(captured, statusMapAll);
    assert.strictEqual(gateAll.canAssess, true);
  });

  // 9. Successful recovery leads to normal assessment and finalization workflow
  it('9. Successful recovery leads to normal assessment and attaches assessedScan.images', () => {
    const sessionFile = path.join(__dirname, '../screens/inspector/InspectionSessionScreen.jsx');
    const sessionCode = fs.readFileSync(sessionFile, 'utf8');

    assert.ok(
      sessionCode.includes('images: assessedScan.images || []'),
      'Must attach server-returned assessedScan.images to scan item'
    );
    assert.ok(
      sessionCode.includes('serverScanIdRef.current = null'),
      'Must reset active scan ref after successful package assessment'
    );
    assert.ok(
      sessionCode.includes('setActiveServerScanId(null)'),
      'Must reset active server scan state after successful package assessment'
    );
    assert.ok(
      sessionCode.includes('setPanelUploadStatus({})'),
      'Must reset upload status map after package is successfully assessed'
    );
  });

  // 10. Navigation or stale responses do not overwrite newer upload state
  await itAsync('10. Lifecycle safety: cancelled or unmounted component aborts in-flight retry loop', async () => {
    let loopCancelled = false;
    let iterations = 0;

    const mockUpload = async () => {
      iterations++;
      const err = new Error('Socket timeout');
      err.code = 'ECONNABORTED';
      throw err;
    };

    try {
      await uploadWithBoundedRetry(mockUpload, {
        maxRetries: 5,
        baseDelayMs: 20,
        isCancelled: () => {
          if (iterations >= 2) {
            loopCancelled = true;
            return true;
          }
          return false;
        },
      });
      assert.fail('Should have aborted on cancellation');
    } catch (e) {
      assert.strictEqual(e.isCancelled, true);
      assert.strictEqual(loopCancelled, true);
      assert.strictEqual(iterations, 2, 'Should abort on iteration 2 instead of executing all 5 retries');
    }

    const sessionFile = path.join(__dirname, '../screens/inspector/InspectionSessionScreen.jsx');
    const sessionCode = fs.readFileSync(sessionFile, 'utf8');

    assert.ok(sessionCode.includes('isMountedRef.current = true'), 'Mount tracking must be initialized');
    assert.ok(sessionCode.includes('isMountedRef.current = false'), 'Unmount cleanup must be implemented');
    assert.ok(sessionCode.includes('isCancelled: () => !isMountedRef.current'), 'Upload orchestrator must check isMountedRef');
  });

  console.log(`\nResults: ${passed} passed, ${failed} failed\n`);
  if (failed > 0) process.exit(1);
}

runTests().catch((err) => {
  console.error('Test runner uncaught error:', err);
  process.exit(1);
});
