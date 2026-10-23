// Frontend_App/__tests__/test_evidence_thumbnail_rendering.js
// Regression test suite for Module 8 — Mobile Evidence Rendering & Image Provenance
const assert = require('assert');
const fs = require('fs');
const path = require('path');

console.log('=== Running Module 8 Evidence Thumbnail & Rendering Tests ===\n');

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
  const {
    buildImageUrl,
    getImageAuthHeaders,
    normalizeEvidenceList,
    createEvidenceKey,
    CANONICAL_PANEL_ORDER,
  } = await import('../config/evidenceRendering.js');

  const BASE_URL = 'https://niyamnetra-backend.onrender.com';

  // 1. Different image identifiers produce correct distinct evidence entries
  it('1. Different image identifiers produce correct distinct evidence entries', () => {
    const scan = {
      id: 50,
      server_id: 50,
      images: [
        { id: 101, panel: 'front', sha256: 'sha256_front_aaa', url: '/scans/50/images/101', thumbnail_url: '/scans/50/images/101/thumbnail' },
        { id: 102, panel: 'back', sha256: 'sha256_back_bbb', url: '/scans/50/images/102', thumbnail_url: '/scans/50/images/102/thumbnail' },
      ],
    };

    const list = normalizeEvidenceList(scan, scan.id, null, BASE_URL);
    assert.strictEqual(list.length, 2);
    assert.strictEqual(list[0].id, 101);
    assert.strictEqual(list[0].panel, 'front');
    assert.strictEqual(list[0].sha256, 'sha256_front_aaa');
    assert.strictEqual(list[0].thumbnail_url, `${BASE_URL}/scans/50/images/101/thumbnail`);

    assert.strictEqual(list[1].id, 102);
    assert.strictEqual(list[1].panel, 'back');
    assert.strictEqual(list[1].sha256, 'sha256_back_bbb');
    assert.strictEqual(list[1].thumbnail_url, `${BASE_URL}/scans/50/images/102/thumbnail`);

    // Unique keys
    const key0 = createEvidenceKey(scan.id, list[0].panel, list[0].id, list[0].sha256, 0);
    const key1 = createEvidenceKey(scan.id, list[1].panel, list[1].id, list[1].sha256, 1);
    assert.notStrictEqual(key0, key1);
    assert.ok(key0.includes('front'));
    assert.ok(key1.includes('back'));
  });

  // 2. Each panel displays its own persisted image without inventing absent panels
  it('2. Each panel displays its own persisted image according to existing panel model', () => {
    // Four panels in arbitrary returned order: mrp, front, batch, back
    const scan = {
      id: 60,
      images: [
        { id: 203, panel: 'mrp', sha256: 'hash_mrp' },
        { id: 201, panel: 'front', sha256: 'hash_front' },
        { id: 204, panel: 'batch', sha256: 'hash_batch' },
        { id: 202, panel: 'back', sha256: 'hash_back' },
      ],
    };

    const list = normalizeEvidenceList(scan, scan.id, null, BASE_URL);
    assert.strictEqual(list.length, 4);
    // Canonical order must be: front (1), back (2), mrp (3), batch (4)
    assert.strictEqual(list[0].panel, 'front');
    assert.strictEqual(list[0].id, 201);
    assert.strictEqual(list[1].panel, 'back');
    assert.strictEqual(list[1].id, 202);
    assert.strictEqual(list[2].panel, 'mrp');
    assert.strictEqual(list[2].id, 203);
    assert.strictEqual(list[3].panel, 'batch');
    assert.strictEqual(list[3].id, 204);

    // When only front and mrp are uploaded, absent panels are NOT invented
    const partialScan = {
      id: 61,
      images: [
        { id: 301, panel: 'front', sha256: 'hash_f' },
        { id: 303, panel: 'mrp', sha256: 'hash_m' },
      ],
    };
    const partialList = normalizeEvidenceList(partialScan, partialScan.id, null, BASE_URL);
    assert.strictEqual(partialList.length, 2);
    assert.strictEqual(partialList.some((e) => e.panel === 'back'), false);
    assert.strictEqual(partialList.some((e) => e.panel === 'batch'), false);
  });

  // 3. Navigating to another scan does not display images from previous scan
  it('3. Navigating to another scan does not display images from previous scan', () => {
    const scanA = {
      id: 101,
      server_id: 101,
      images: [
        { id: 1, panel: 'front', sha256: 'sha_scanA_front' },
      ],
    };
    const scanB = {
      id: 102,
      server_id: 102,
      images: [
        { id: 2, panel: 'front', sha256: 'sha_scanB_front' },
      ],
    };

    const listA = normalizeEvidenceList(scanA, scanA.id, null, BASE_URL);
    const listB = normalizeEvidenceList(scanB, scanB.id, null, BASE_URL);

    assert.strictEqual(listA[0].sha256, 'sha_scanA_front');
    assert.strictEqual(listB[0].sha256, 'sha_scanB_front');
    assert.notStrictEqual(listA[0].url, listB[0].url);

    // Verify key isolation
    const keyA = createEvidenceKey(scanA.id, listA[0].panel, listA[0].id, listA[0].sha256, 0);
    const keyB = createEvidenceKey(scanB.id, listB[0].panel, listB[0].id, listB[0].sha256, 0);
    assert.notStrictEqual(keyA, keyB);
    assert.ok(keyA.includes('scan-101'));
    assert.ok(keyB.includes('scan-102'));

    // Verify InspectorFlow supplies key prop isolating FindingsScreen per scan
    const flowPath = path.join(__dirname, '../screens/inspector/InspectorFlow.jsx');
    const flowCode = fs.readFileSync(flowPath, 'utf8');
    assert.ok(flowCode.includes('key={`findings-${selectedScan?.server_id || selectedScan?.id || \'scan\'}`}'));
  });

  // 4. Uploading or replacing evidence and refreshing displays correct current evidence
  it('4. Uploading or replacing evidence and refreshing displays the correct current evidence', () => {
    // Initial state: scan with initial blurry MRP
    const scanBefore = {
      id: 70,
      images: [
        { id: 401, panel: 'front', sha256: 'sha_front_orig' },
        { id: 402, panel: 'mrp', sha256: 'sha_mrp_blurry' },
      ],
    };
    const listBefore = normalizeEvidenceList(scanBefore, 70, null, BASE_URL);
    assert.strictEqual(listBefore.find((e) => e.panel === 'mrp').sha256, 'sha_mrp_blurry');

    // After recapture/reassessment: fresh clear MRP uploaded as image 405
    const scanAfter = {
      id: 70,
      images: [
        { id: 401, panel: 'front', sha256: 'sha_front_orig' },
        { id: 405, panel: 'mrp', sha256: 'sha_mrp_crisp_recaptured' },
      ],
    };
    const listAfter = normalizeEvidenceList(scanAfter, 70, null, BASE_URL);
    assert.strictEqual(listAfter.find((e) => e.panel === 'mrp').sha256, 'sha_mrp_crisp_recaptured');
    assert.strictEqual(listAfter.find((e) => e.panel === 'mrp').id, 405);

    // Old key vs new key must not collide
    const keyOld = createEvidenceKey(70, 'mrp', 402, 'sha_mrp_blurry', 1);
    const keyNew = createEvidenceKey(70, 'mrp', 405, 'sha_mrp_crisp_recaptured', 1);
    assert.notStrictEqual(keyOld, keyNew);
  });

  // 5. Image-load failures do not display stale or unrelated placeholders
  it('5. Image-load failures do not display stale or unrelated placeholders', () => {
    // Empty evidence test
    const emptyScan = { id: 80, images: [] };
    const emptyList = normalizeEvidenceList(emptyScan, 80, null, BASE_URL);
    assert.strictEqual(emptyList.length, 0);

    // Source code verification: EvidenceThumbnail has loadError state and retry button
    const thumbPath = path.join(__dirname, '../components/EvidenceThumbnail.jsx');
    const thumbCode = fs.readFileSync(thumbPath, 'utf8');
    assert.ok(thumbCode.includes('loadError'));
    assert.ok(thumbCode.includes('Load failed'));
    assert.ok(thumbCode.includes('handleRetry'));
    assert.ok(thumbCode.includes('ActivityIndicator'));

    // FindingsScreen has honest empty state and does not show fake placeholder images
    const findingsPath = path.join(__dirname, '../screens/inspector/FindingsScreen.jsx');
    const findingsCode = fs.readFileSync(findingsPath, 'utf8');
    assert.ok(findingsCode.includes('No evidence images attached to this package session.'));
    assert.strictEqual(findingsCode.includes('placeholder.png'), false);
    assert.strictEqual(findingsCode.includes('placeholder.jpg'), false);
  });

  // 6. Unauthorized image requests do not expose protected content
  it('6. Unauthorized image requests do not expose protected content and never leak tokens in URLs', () => {
    const remoteUrl = 'https://niyamnetra-backend.onrender.com/scans/42/images/10/thumbnail';
    const localUri = 'file:///var/mobile/Containers/Data/photo.jpg';

    // Authenticated request gets Authorization header
    const sampleToken = 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.dummy_token_bytes';
    const remoteHeaders = getImageAuthHeaders(remoteUrl, sampleToken);
    assert.deepStrictEqual(remoteHeaders, { Authorization: `Bearer ${sampleToken}` });

    // Unauthenticated request gets no auth headers
    const unauthHeaders = getImageAuthHeaders(remoteUrl, null);
    assert.strictEqual(unauthHeaders, undefined);

    // Local file URI never leaks auth headers
    const localHeaders = getImageAuthHeaders(localUri, sampleToken);
    assert.strictEqual(localHeaders, undefined);

    // buildImageUrl strictly preserves clean URL without appending ?token=
    const built = buildImageUrl('/scans/42/images/10/thumbnail', BASE_URL);
    assert.strictEqual(built, remoteUrl);
    assert.strictEqual(built.includes('token='), false);
    assert.strictEqual(built.includes('?token'), false);
    assert.strictEqual(built.includes('&token'), false);
    assert.strictEqual(built.includes('eyJ'), false);
  });

  // 7. Original evidence remains distinct from derived thumbnails and analysis artifacts
  it('7. Original evidence remains distinct from derived thumbnails and analysis artifacts', () => {
    const scan = {
      id: 90,
      images: [
        {
          id: 501,
          panel: 'front',
          sha256: 'original_raw_camera_sha256',
          url: '/scans/90/images/501',
          thumbnail_url: '/scans/90/images/501/thumbnail',
        },
      ],
    };

    const list = normalizeEvidenceList(scan, 90, null, BASE_URL);
    assert.strictEqual(list.length, 1);
    const item = list[0];

    // High-resolution original vs downscaled thumbnail
    assert.strictEqual(item.url, `${BASE_URL}/scans/90/images/501`);
    assert.strictEqual(item.thumbnail_url, `${BASE_URL}/scans/90/images/501/thumbnail`);
    assert.notStrictEqual(item.url, item.thumbnail_url);
    assert.ok(item.thumbnail_url.endsWith('/thumbnail'));
    assert.ok(!item.url.endsWith('/thumbnail'));

    // Modal viewer in FindingsScreen uses high-res url, gallery uses thumbnail_url
    const findingsPath = path.join(__dirname, '../screens/inspector/FindingsScreen.jsx');
    const findingsCode = fs.readFileSync(findingsPath, 'utf8');
    assert.ok(findingsCode.includes('const rawUrl = img.thumbnail_url || img.url;'));
    assert.ok(findingsCode.includes('const highResUrl = img.url || rawUrl;'));
  });

  // 8. Existing evidence provenance and assessment workflows continue to work
  it('8. Existing evidence provenance and assessment workflows continue to work', () => {
    // InspectionSessionScreen attaches assessedScan.images to scanItem
    const sessionPath = path.join(__dirname, '../screens/inspector/InspectionSessionScreen.jsx');
    const sessionCode = fs.readFileSync(sessionPath, 'utf8');
    assert.ok(sessionCode.includes('images: assessedScan.images || []'));

    // InspectorFlow preserves images in draft reconstruction
    const flowPath = path.join(__dirname, '../screens/inspector/InspectorFlow.jsx');
    const flowCode = fs.readFileSync(flowPath, 'utf8');
    assert.ok(flowCode.includes('images: s.images || []'));

    // PassScreen and ViolationsScreen use EvidenceThumbnail with stable keys
    const passPath = path.join(__dirname, '../screens/inspector/PassScreen.jsx');
    const passCode = fs.readFileSync(passPath, 'utf8');
    assert.ok(passCode.includes('<EvidenceThumbnail'));
    assert.ok(passCode.includes('pass-evidence-'));

    const violPath = path.join(__dirname, '../screens/inspector/ViolationsScreen.jsx');
    const violCode = fs.readFileSync(violPath, 'utf8');
    assert.ok(violCode.includes('<EvidenceThumbnail'));
    assert.ok(violCode.includes('violation-evidence-'));
  });

  console.log(`\nResults: ${passed} passed, ${failed} failed\n`);
  if (failed > 0) process.exit(1);
}

runTests().catch((err) => {
  console.error('Test runner uncaught error:', err);
  process.exit(1);
});
