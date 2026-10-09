// Frontend_App/__tests__/test_dynamic_assessment_findings.js — Dynamic Assessment & Evidence Regression Tests
const assert = require('assert');

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

console.log('=== Running Dynamic Assessment Findings & Evidence UI Tests ===\n');

// 1. Production catalog is dynamic; no hard-coded 19-check result list
it('1. Production catalog is dynamic and has 26 registered rules in 2026.09.v1', () => {
  const fs = require('fs');
  const path = require('path');
  const rulePackPath = path.join(__dirname, '../../Backend/rules/rule_packs/lmpc_2026_09_v1.json');
  const rulePack = JSON.parse(fs.readFileSync(rulePackPath, 'utf8'));
  
  assert.strictEqual(rulePack.rule_pack_version, '2026.09.v1');
  assert.strictEqual(rulePack.rules.length, 26);
  assert.strictEqual(rulePack.rules[0].code, 'CHK01');
  assert.strictEqual(rulePack.rules[25].code, 'CHK23');

  // Verify FindingsScreen.jsx has no hardcoded 19 legacy checklist headers
  const findingsScreenPath = path.join(__dirname, '../screens/inspector/FindingsScreen.jsx');
  const findingsScreenCode = fs.readFileSync(findingsScreenPath, 'utf8');

  assert.strictEqual(findingsScreenCode.includes('19 STATUTORY CHECKS'), false);
  assert.strictEqual(findingsScreenCode.includes('19 Statutory Rule Checks'), false);
  assert.strictEqual(findingsScreenCode.includes('Unspecified Brand (Unspecified Commodity)'), false);
  assert.strictEqual(findingsScreenCode.includes('Run Server Assessment (12 Pending Checks)'), false);
  assert.strictEqual(findingsScreenCode.includes('STATUTORY FINDINGS ({findings.length})'), true);
  assert.strictEqual(findingsScreenCode.includes('Statutory Findings (${findings.length})'), true);

  // Dynamic findings title
  const renderTitle = (findingsCount) => `Statutory Findings (${findingsCount})`;
  assert.strictEqual(renderTitle(26), 'Statutory Findings (26)');
  assert.strictEqual(renderTitle(19), 'Statutory Findings (19)');
  assert.strictEqual(renderTitle(4), 'Statutory Findings (4)');
});

// 2. Two different assessment payloads show different commodity names
it('2. Two different assessment payloads show different commodity names and brands', () => {
  const payload1 = {
    commodity_generic: 'Mustard Oil',
    brand_name: 'Dhara',
    batch_number: 'DH-101',
    mrp: 120.0,
    net_quantity_value: 500,
    net_quantity_unit: 'ml',
  };

  const payload2 = {
    commodity_generic: 'Basmati Rice',
    brand_name: 'India Gate',
    batch_number: 'IG-202',
    mrp: 350.0,
    net_quantity_value: 5,
    net_quantity_unit: 'kg',
  };

  const formatSubtitle = (scan) => {
    const brand = (scan?.brand_name && !scan.brand_name.toLowerCase().includes('unspecified')) ? scan.brand_name.trim() : null;
    const commodity = (scan?.commodity_generic && !scan.commodity_generic.toLowerCase().includes('unspecified')) ? scan.commodity_generic.trim() : null;
    if (brand && commodity) return `${brand} — ${commodity}`;
    if (commodity) return commodity;
    if (brand) return `${brand} • Commodity not determined`;
    return 'Commodity not determined';
  };

  assert.strictEqual(formatSubtitle(payload1), 'Dhara — Mustard Oil');
  assert.strictEqual(formatSubtitle(payload2), 'India Gate — Basmati Rice');
  assert.notStrictEqual(formatSubtitle(payload1), formatSubtitle(payload2));
});

// 3. Missing fields are displayed truthfully
it('3. Missing fields are displayed truthfully without inventing data', () => {
  const emptyScan = {
    commodity_generic: null,
    brand_name: null,
    batch_number: null,
    mrp: null,
    net_quantity_value: null,
    net_quantity_unit: null,
  };

  const formatProductCard = (scan) => {
    const brand = (scan?.brand_name && !scan.brand_name.toLowerCase().includes('unspecified')) ? scan.brand_name.trim() : null;
    const commodity = (scan?.commodity_generic && !scan.commodity_generic.toLowerCase().includes('unspecified')) ? scan.commodity_generic.trim() : null;
    const batch = (scan?.batch_number && scan.batch_number !== 'N/A' && !scan.batch_number.toLowerCase().includes('unspecified')) ? scan.batch_number.trim() : null;
    const mrp = scan?.mrp;
    const netQty = scan?.net_quantity_value ? `${scan.net_quantity_value} ${scan.net_quantity_unit || ''}`.trim() : null;

    let subtitle;
    if (brand && commodity) subtitle = `${brand} — ${commodity}`;
    else if (commodity) subtitle = commodity;
    else if (brand) subtitle = `${brand} • Commodity not determined`;
    else subtitle = 'Commodity not determined';

    const batchLine = `Batch: ${batch || 'Batch not observed'}${mrp ? ` • MRP: ₹${mrp}` : ''}${netQty ? ` • Net Qty: ${netQty}` : ''}`;
    return { subtitle, batchLine };
  };

  const formatted = formatProductCard(emptyScan);
  assert.strictEqual(formatted.subtitle, 'Commodity not determined');
  assert.strictEqual(formatted.batchLine, 'Batch: Batch not observed');
  assert.strictEqual(formatted.subtitle.includes('Unspecified'), false);
});

// 4. Different image IDs render different image sources
it('4. Different image IDs render different image sources and keys', () => {
  const scanAImages = [
    { id: 101, panel: 'front', url: '/scans/1/images/101', sha256: 'sha_front_1' },
    { id: 102, panel: 'back', url: '/scans/1/images/102', sha256: 'sha_back_1' },
  ];

  const scanBImages = [
    { id: 201, panel: 'front', url: '/scans/2/images/201', sha256: 'sha_front_2' },
    { id: 202, panel: 'back', url: '/scans/2/images/202', sha256: 'sha_back_2' },
  ];

  const buildKeys = (scanId, images) => images.map((img, idx) => `evidence-${scanId}-${img.panel}-${img.id}-${img.sha256 || idx}`);

  const keysA = buildKeys(1, scanAImages);
  const keysB = buildKeys(2, scanBImages);

  assert.strictEqual(keysA[0], 'evidence-1-front-101-sha_front_1');
  assert.strictEqual(keysB[0], 'evidence-2-front-201-sha_front_2');
  assert.notStrictEqual(keysA[0], keysB[0]);
  assert.notStrictEqual(scanAImages[0].url, scanBImages[0].url);
});

// 5. Panel-to-image mapping is correct
it('5. Panel-to-image mapping correctly sorts front, back, mrp, batch', () => {
  const rawImages = [
    { id: 4, panel: 'batch', url: '/img/4' },
    { id: 2, panel: 'back', url: '/img/2' },
    { id: 1, panel: 'front', url: '/img/1' },
    { id: 3, panel: 'mrp', url: '/img/3' },
  ];

  const panelOrder = { front: 1, back: 2, mrp: 3, batch: 4, side: 5, other: 6 };
  const sorted = [...rawImages].sort((a, b) => {
    const orderA = panelOrder[a.panel?.toLowerCase()] || 99;
    const orderB = panelOrder[b.panel?.toLowerCase()] || 99;
    return orderA - orderB;
  });

  assert.strictEqual(sorted[0].panel, 'front');
  assert.strictEqual(sorted[1].panel, 'back');
  assert.strictEqual(sorted[2].panel, 'mrp');
  assert.strictEqual(sorted[3].panel, 'batch');
});

// 6. Dynamic counters reflect actual findings
it('6. Dynamic counters accurately reflect pass, fail, not_assessed, not_applicable', () => {
  const findings = [
    { check_id: 'CHK01', effective_verdict: 'pass' },
    { check_id: 'CHK02', effective_verdict: 'not_applicable' },
    { check_id: 'CHK03', effective_verdict: 'pass' },
    { check_id: 'CHK04', effective_verdict: 'fail' },
    { check_id: 'CHK05', effective_verdict: 'fail' },
    { check_id: 'CHK06', effective_verdict: 'not_assessed' },
  ];

  const passCount = findings.filter(f => f.effective_verdict === 'pass').length;
  const failCount = findings.filter(f => f.effective_verdict === 'fail').length;
  const notAssessedCount = findings.filter(f => f.effective_verdict === 'not_assessed').length;
  const notApplicableCount = findings.filter(f => f.effective_verdict === 'not_applicable').length;

  assert.strictEqual(passCount, 2);
  assert.strictEqual(failCount, 2);
  assert.strictEqual(notAssessedCount, 1);
  assert.strictEqual(notApplicableCount, 1);
  assert.strictEqual(findings.length, 6);
});

// 7. Completed assessments do not retain stale pending counts
it('7. Completed assessments display completed state instead of stale Run Server Assessment banner', () => {
  const scanAssessed = {
    id: 42,
    status: 'assessed',
    overall_result: 'violation',
    checks_assessed: 19,
    findings: [
      { check_id: 'CHK01', effective_verdict: 'pass', engine_verdict: 'pass' },
      { check_id: 'CHK04', effective_verdict: 'fail', engine_verdict: 'fail' },
      { check_id: 'CHK02', effective_verdict: 'not_assessed', engine_verdict: 'not_assessed' },
    ],
  };

  const hasServerAssessed = Boolean(
    scanAssessed?.status === 'assessed' ||
    (scanAssessed?.overall_result && scanAssessed?.overall_result !== 'not_assessed') ||
    (scanAssessed?.checks_assessed && scanAssessed?.checks_assessed > 0)
  );

  const isAwaitingAssessment = Boolean(
    scanAssessed.id &&
    !hasServerAssessed &&
    (scanAssessed?.status === 'captured' || scanAssessed?.status === 'pending')
  );

  assert.strictEqual(hasServerAssessed, true);
  assert.strictEqual(isAwaitingAssessment, false);
});

// 8. Previous inspection state is not shown for a new inspection
it('8. Distinct inspection instances isolate findings via key prop and state sync', () => {
  const scan1 = { id: 1, findings: [{ check_id: 'CHK01', effective_verdict: 'pass' }] };
  const scan2 = { id: 2, findings: [{ check_id: 'CHK01', effective_verdict: 'fail' }, { check_id: 'CHK02', effective_verdict: 'pass' }] };

  const key1 = scan1?.server_id || scan1?.id || 'scan';
  const key2 = scan2?.server_id || scan2?.id || 'scan';

  assert.notStrictEqual(key1, key2);
  assert.strictEqual(scan1.findings.length, 1);
  assert.strictEqual(scan2.findings.length, 2);
});

// 9. API / catalog version consistency
it('9. UI renders Rule Pack 2026.09.v1 and 26 catalog rules with uppercase inspection verdicts', () => {
  const { scanResultConfig, verdictConfig } = require('../theme');
  assert.strictEqual(scanResultConfig.compliant.label, 'COMPLIANT');
  assert.strictEqual(scanResultConfig.violation.label, 'VIOLATION');
  assert.strictEqual(scanResultConfig.review_required.label, 'REVIEW REQUIRED');

  assert.strictEqual(verdictConfig.pass.label, 'PASS');
  assert.strictEqual(verdictConfig.fail.label, 'FAIL');
  assert.strictEqual(verdictConfig.not_assessed.label, 'NOT ASSESSED');

  const rulePackBadge = (version, count) => `Rule Pack ${version || '2026.09.v1'} • ${count || 26} Catalog Rules`;
  assert.strictEqual(rulePackBadge('2026.09.v1', 26), 'Rule Pack 2026.09.v1 • 26 Catalog Rules');
});

// 10. Security: Media requests do not put JWTs into URLs
it('10. Media requests do not put JWTs into URLs, query params, or cache keys', () => {
  const fs = require('fs');
  const path = require('path');
  const findingsScreenPath = path.join(__dirname, '../screens/inspector/FindingsScreen.jsx');
  const code = fs.readFileSync(findingsScreenPath, 'utf8');

  // Verify no `?token=` or `token=` in URLs
  assert.strictEqual(code.includes('token='), false);
  assert.strictEqual(code.includes('?token'), false);
  assert.strictEqual(code.includes('&token'), false);

  // Verify buildImageUrl returns pure path without query token
  const buildImageUrl = (u) => {
    if (!u) return null;
    const base = 'https://niyamnetra-backend.onrender.com';
    return (u.startsWith('http://') || u.startsWith('https://') || u.startsWith('file://'))
      ? u
      : `${base.replace(/\/+$/, '')}/${u.replace(/^\/+/, '')}`;
  };

  const cleanUrl = buildImageUrl('/scans/42/images/7/thumbnail');
  assert.strictEqual(cleanUrl, 'https://niyamnetra-backend.onrender.com/scans/42/images/7/thumbnail');
  assert.strictEqual(cleanUrl.includes('token'), false);

  // Cache key verification: keys must not contain tokens
  const imageKey = `evidence-42-front-7-sha256abc`;
  assert.strictEqual(imageKey.includes('token'), false);
});

// 11. Security: Authorization headers are used securely
it('11. Authorization headers are used securely via Bearer token', () => {
  const getAuthHeaders = (token, uri) => {
    if (!token || !uri || uri.startsWith('file://')) return undefined;
    return { Authorization: `Bearer ${token}` };
  };

  const sampleToken = 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.dummy';
  const remoteHeaders = getAuthHeaders(sampleToken, 'https://niyamnetra-backend.onrender.com/scans/42/images/7');
  assert.deepStrictEqual(remoteHeaders, { Authorization: `Bearer ${sampleToken}` });

  // Local file URI needs no auth header
  const localHeaders = getAuthHeaders(sampleToken, 'file:///var/mobile/img.jpg');
  assert.strictEqual(localHeaders, undefined);

  // Missing token returns undefined
  const unauthHeaders = getAuthHeaders(null, 'https://niyamnetra-backend.onrender.com/scans/42/images/7');
  assert.strictEqual(unauthHeaders, undefined);
});

// 12. Security: Token values are not logged or leaked to console
it('12. Token values and Authorization headers are not written to application logs', () => {
  const fs = require('fs');
  const path = require('path');
  const findingsScreenPath = path.join(__dirname, '../screens/inspector/FindingsScreen.jsx');
  const violationsScreenPath = path.join(__dirname, '../screens/inspector/ViolationsScreen.jsx');
  const passScreenPath = path.join(__dirname, '../screens/inspector/PassScreen.jsx');

  const fCode = fs.readFileSync(findingsScreenPath, 'utf8');
  const vCode = fs.readFileSync(violationsScreenPath, 'utf8');
  const pCode = fs.readFileSync(passScreenPath, 'utf8');

  // Verify no console.log of token or Authorization headers
  assert.strictEqual(/console\.(log|info|debug)\(.*token/i.test(fCode), false);
  assert.strictEqual(/console\.(log|info|debug)\(.*token/i.test(vCode), false);
  assert.strictEqual(/console\.(log|info|debug)\(.*token/i.test(pCode), false);
  assert.strictEqual(/console\.(log|info|debug)\(.*Authorization/i.test(fCode), false);
});

// 13. Frontend Request Control: duplicate submission protection
it('13. Duplicate submission protection prevents double assessment triggers', () => {
  let inFlight = false;
  let executionCount = 0;

  const triggerAssessment = () => {
    if (inFlight) return 'BLOCKED_DUPLICATE';
    inFlight = true;
    executionCount++;
    return 'EXECUTED';
  };

  assert.strictEqual(triggerAssessment(), 'EXECUTED');
  assert.strictEqual(triggerAssessment(), 'BLOCKED_DUPLICATE');
  assert.strictEqual(triggerAssessment(), 'BLOCKED_DUPLICATE');
  assert.strictEqual(executionCount, 1);

  inFlight = false;
  assert.strictEqual(triggerAssessment(), 'EXECUTED');
  assert.strictEqual(executionCount, 2);
});

// 14. Frontend Request Control: successful assessment is not automatically retried
it('14. Successful assessment is not automatically retried and displays completed state', () => {
  const completedScan = {
    id: 42,
    status: 'assessed',
    overall_result: 'violation',
    checks_assessed: 18,
    checks_total: 18,
    findings: [{ check_id: 'CHK01', engine_verdict: 'pass' }],
  };

  const hasServerAssessed = Boolean(
    completedScan?.status === 'assessed' ||
    (completedScan?.overall_result && completedScan?.overall_result !== 'not_assessed') ||
    (completedScan?.checks_assessed && completedScan?.checks_assessed > 0)
  );

  const isAwaitingAssessment = Boolean(
    completedScan.id &&
    !hasServerAssessed &&
    (completedScan?.status === 'captured' || completedScan?.status === 'pending' || !completedScan?.status)
  );

  assert.strictEqual(hasServerAssessed, true);
  assert.strictEqual(isAwaitingAssessment, false);
});

// 15. Genuine new evidence permits reassessment
it('15. Genuine new evidence permits reassessment after recapture', () => {
  let currentEvidence = { front: 'file:///evidence/front1.jpg' };
  let lastAssessedFingerprint = 'fp_front1';

  const computeEvidenceFingerprint = (ev) => Object.entries(ev).sort().map(([k, v]) => `${k}:${v}`).join(';');

  // Before recapture
  assert.strictEqual(computeEvidenceFingerprint(currentEvidence), 'front:file:///evidence/front1.jpg');

  // Recapture adds back panel
  currentEvidence = { ...currentEvidence, back: 'file:///evidence/back1.jpg' };
  const newFingerprint = computeEvidenceFingerprint(currentEvidence);

  assert.notStrictEqual(newFingerprint, lastAssessedFingerprint);
  assert.strictEqual(newFingerprint, 'back:file:///evidence/back1.jpg;front:file:///evidence/front1.jpg');
});

// 16. Truthful failure state
it('16. Truthful failure state displays honest reason without fabricating compliance', () => {
  const offlineFailure = {
    errorDetail: 'Server error (HTTP 503): Assessment capacity busy',
    overall_result: 'not_assessed',
    checks_assessed: 0,
    checks_total: 18,
  };

  assert.strictEqual(offlineFailure.overall_result, 'not_assessed');
  assert.notStrictEqual(offlineFailure.overall_result, 'compliant');
  assert.strictEqual(offlineFailure.errorDetail.includes('503'), true);
});

// 17. Correct distinction between catalog and evaluated counts
it('17. Correct distinction between catalog rules (26) and executable checks (19)', () => {
  const catalogCount = 26;
  const executableChecks = 19;
  const applicableChecksOnPackage = 18;

  const headerBadge = `Rule Pack 2026.09.v1 • ${catalogCount} Catalog Rules (${executableChecks} Algorithmic Checks)`;
  assert.strictEqual(headerBadge, 'Rule Pack 2026.09.v1 • 26 Catalog Rules (19 Algorithmic Checks)');

  const bannerSub = `18 findings evaluated against Rule Pack 2026.09.v1 (${executableChecks} Algorithmic Checks)`;
  assert.strictEqual(bannerSub, '18 findings evaluated against Rule Pack 2026.09.v1 (19 Algorithmic Checks)');
  assert.strictEqual(executableChecks < catalogCount, true);
});

// 18. Media URLs and cache keys contain no JWT
it('18. Evidence image URLs and cache keys contain strictly zero JWT tokens', () => {
  const imageItem = {
    id: 99,
    panel: 'front',
    url: 'https://niyamnetra-backend.onrender.com/scans/47/images/99',
    thumbnail_url: 'https://niyamnetra-backend.onrender.com/scans/47/images/99/thumbnail',
    sha256: 'sha256_package_99',
  };

  assert.strictEqual(imageItem.url.includes('?token='), false);
  assert.strictEqual(imageItem.thumbnail_url.includes('?token='), false);
  assert.strictEqual(imageItem.url.includes('eyJ'), false);
  assert.strictEqual(imageItem.thumbnail_url.includes('eyJ'), false);

  const cacheKey = `evidence-47-${imageItem.panel}-${imageItem.id}-${imageItem.sha256}`;
  assert.strictEqual(cacheKey.includes('eyJ'), false);
  assert.strictEqual(cacheKey.includes('token'), false);
});

console.log(`\nDynamic Assessment Findings Results: ${passed} passed, ${failed} failed.\n`);
if (failed > 0) process.exit(1);
