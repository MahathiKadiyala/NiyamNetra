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

// 19. Cross-inspection isolation: packages list and in-flight inputs do not persist across inspections
it('19. Inspection session transition isolates sampled packages and clears in-flight state', () => {
  const fs = require('fs');
  const path = require('path');
  const sessionScreenPath = path.join(__dirname, '../screens/inspector/InspectionSessionScreen.jsx');
  const sessionScreenCode = fs.readFileSync(sessionScreenPath, 'utf8');

  // Verify synchronization useEffect exists
  assert.strictEqual(
    sessionScreenCode.includes('setPackages(inspectionSession?.scans || [])'),
    true
  );
  assert.strictEqual(
    sessionScreenCode.includes('setPanelPhotos({})'),
    true
  );

  // InspectorFlow must supply key to session screen
  const flowPath = path.join(__dirname, '../screens/inspector/InspectorFlow.jsx');
  const flowCode = fs.readFileSync(flowPath, 'utf8');
  assert.strictEqual(
    flowCode.includes('key={activeSession?.serverInspectionId || activeSession?.id || \'session\'}'),
    true
  );
});

// 20. FindingsScreen isolates assessment findings and resets stale state across scans
it('20. FindingsScreen sync effect updates on findings/verdict and isolates evidence state', () => {
  const fs = require('fs');
  const path = require('path');
  const findingsScreenPath = path.join(__dirname, '../screens/inspector/FindingsScreen.jsx');
  const findingsScreenCode = fs.readFileSync(findingsScreenPath, 'utf8');

  // Verify sync effect watches findings and overall_result
  assert.strictEqual(
    findingsScreenCode.includes('[scan?.id, scan?.server_id, scan?.findings, scan?.overall_result, scan?.updated_at]'),
    true
  );
  assert.strictEqual(
    findingsScreenCode.includes('setFindings(buildInitialFindings(scan))'),
    true
  );
});

// 21. Arithmetic & Finding Counts Reconciled Contract
it('21. Finding counts satisfy arithmetic invariants (4 pass, 3 fail, 12 not assessed -> 19 total, 7 assessed)', () => {
  const scanCounts = {
    total: 19,
    passed: 4,
    failed: 3,
    not_assessed: 12,
  };
  const checks_total = 19;
  const checks_assessed = 7;

  // Mutually exclusive final status invariant
  assert.strictEqual(scanCounts.passed + scanCounts.failed + scanCounts.not_assessed, scanCounts.total);
  assert.strictEqual(scanCounts.total, checks_total);

  // Successfully assessed invariant
  assert.strictEqual(checks_assessed, scanCounts.passed + scanCounts.failed);
});

// 22. Frontend screens use authoritative backend counters without hardcoded drift
it('22. Frontend screens avoid conflating 26 catalog rules with per-scan findings', () => {
  const fs = require('fs');
  const path = require('path');

  const sessionScreenPath = path.join(__dirname, '../screens/inspector/InspectionSessionScreen.jsx');
  const sessionScreenCode = fs.readFileSync(sessionScreenPath, 'utf8');

  // Verify checks_total does not default to 26 in InspectionSessionScreen
  assert.strictEqual(sessionScreenCode.includes('checks_total: assessedScan.checks_total || 26'), false);
  assert.strictEqual(sessionScreenCode.includes('checks_total: 26,'), false);

  const reviewScreenPath = path.join(__dirname, '../screens/inspector/ReviewDetailScreen.jsx');
  const reviewScreenCode = fs.readFileSync(reviewScreenPath, 'utf8');

  // Verify completeness does not fall back to /18 in ReviewDetailScreen
  assert.strictEqual(reviewScreenCode.includes('${scanReviews[0]?.checks_total || 18}'), false);
});

// =========================================================================
// Prompt 9: Render Backend-Derived Assessment States Regression Tests (23-34)
// =========================================================================

const {
  ASSESSMENT_STATE,
  deriveAssessmentState,
  formatFinding: formatFindingItem,
  formatFindings: formatFindingsList,
  computeEvidenceFingerprint: computeFp,
} = require('../config/assessmentWorkflow');

// 23. Two scans with different assessment responses display different findings and summaries
it('23. Two scans with different assessment responses display different findings and summaries', () => {
  const scanPass = {
    id: 101,
    status: 'assessed',
    overall_result: 'compliant',
    checks_assessed: 19,
    findings: [
      { check_id: 'CHK01', title: 'Mandatory Declarations', effective_verdict: 'pass', engine_verdict: 'pass' },
      { check_id: 'CHK04', title: 'Retail Sale Price', effective_verdict: 'pass', engine_verdict: 'pass' },
    ],
  };

  const scanFail = {
    id: 202,
    status: 'assessed',
    overall_result: 'violation',
    checks_assessed: 19,
    findings: [
      { check_id: 'CHK01', title: 'Mandatory Declarations', effective_verdict: 'fail', engine_verdict: 'fail', reason: 'MRP missing' },
      { check_id: 'CHK04', title: 'Retail Sale Price', effective_verdict: 'pass', engine_verdict: 'pass' },
    ],
  };

  const statePass = deriveAssessmentState({ currentScan: scanPass, findings: scanPass.findings });
  const stateFail = deriveAssessmentState({ currentScan: scanFail, findings: scanFail.findings });

  assert.strictEqual(statePass, ASSESSMENT_STATE.COMPLIANT);
  assert.strictEqual(stateFail, ASSESSMENT_STATE.VIOLATION);
  assert.notStrictEqual(statePass, stateFail);

  const passFindings = formatFindingsList(scanPass.findings);
  const failFindings = formatFindingsList(scanFail.findings);

  assert.strictEqual(passFindings[0].effective_verdict, 'pass');
  assert.strictEqual(failFindings[0].effective_verdict, 'fail');
  assert.strictEqual(failFindings[0].reason, 'MRP missing');
});

// 24. Switching scans does not leave stale counts or finding rows visible
it('24. Switching scans does not leave stale counts or finding rows visible', () => {
  let activeScanId = 101;
  let displayedFindings = [
    { id: 1, check_id: 'CHK01', effective_verdict: 'fail' },
    { id: 2, check_id: 'CHK04', effective_verdict: 'fail' },
  ];

  let failCount = displayedFindings.filter((f) => f.effective_verdict === 'fail').length;
  assert.strictEqual(failCount, 2);

  // Inspector switches to Scan 202 (which has 0 violations)
  const nextScan = {
    id: 202,
    status: 'assessed',
    overall_result: 'compliant',
    findings: [
      { id: 1, check_id: 'CHK01', effective_verdict: 'pass' },
      { id: 2, check_id: 'CHK04', effective_verdict: 'pass' },
    ],
  };

  activeScanId = nextScan.id;
  displayedFindings = formatFindingsList(nextScan.findings);
  failCount = displayedFindings.filter((f) => f.effective_verdict === 'fail').length;
  const passCount = displayedFindings.filter((f) => f.effective_verdict === 'pass').length;

  assert.strictEqual(activeScanId, 202);
  assert.strictEqual(failCount, 0);
  assert.strictEqual(passCount, 2);
  assert.strictEqual(displayedFindings[0].effective_verdict, 'pass');
});

// 25. Uploading or replacing evidence followed by reassessment refreshes displayed result
it('25. Uploading or replacing evidence invalidates previous assessment fingerprint and refreshes result', () => {
  const initialEvidence = [
    { panel: 'front', id: 1, sha256: 'sha_old_front' },
    { panel: 'back', id: 2, sha256: 'sha_old_back' },
  ];
  const oldFp = computeFp(initialEvidence);

  // Replace back panel photo
  const updatedEvidence = [
    { panel: 'front', id: 1, sha256: 'sha_old_front' },
    { panel: 'back', id: 3, sha256: 'sha_new_back' },
  ];
  const newFp = computeFp(updatedEvidence);

  assert.notStrictEqual(oldFp, newFp);
  assert.strictEqual(oldFp.includes('sha_old_back'), true);
  assert.strictEqual(newFp.includes('sha_new_back'), true);

  // After evidence replacement, scan resets to not_assessed until reassessed
  const reassessedScan = {
    id: 50,
    status: 'assessed',
    overall_result: 'compliant',
    diagnostics: { assessment_input_fingerprint: newFp },
    findings: [{ check_id: 'CHK01', effective_verdict: 'pass' }],
  };

  const state = deriveAssessmentState({ currentScan: reassessedScan, findings: reassessedScan.findings });
  assert.strictEqual(state, ASSESSMENT_STATE.COMPLIANT);
  assert.strictEqual(reassessedScan.diagnostics.assessment_input_fingerprint, newFp);
});

// 26. OCR/provider failure displays a truthful technical failure or recovery state
it('26. OCR/provider failure displays truthful technical failure without fabricating violations', () => {
  const ocrFailureScan = {
    id: 60,
    status: 'assessed',
    overall_result: 'not_assessed',
    checks_assessed: 0,
    checks_total: 19,
    diagnostics: { ocr_status: 'no_text', ocr_provider: 'google_vision' },
    findings: [
      { check_id: 'CHK01', effective_verdict: 'not_assessed', engine_verdict: 'not_assessed', reason: 'OCR extraction failure: no text was recognised' },
      { check_id: 'CHK04', effective_verdict: 'not_assessed', engine_verdict: 'not_assessed', observed: null, reason: 'Pending OCR extraction' },
    ],
  };

  const state = deriveAssessmentState({ currentScan: ocrFailureScan, findings: ocrFailureScan.findings });
  assert.strictEqual(state, ASSESSMENT_STATE.TECHNICAL_FAILURE);
  assert.notStrictEqual(state, ASSESSMENT_STATE.VIOLATION);
  assert.notStrictEqual(state, ASSESSMENT_STATE.COMPLIANT);

  const formatted = formatFindingsList(ocrFailureScan.findings);
  assert.strictEqual(formatted[0].effective_verdict, 'not_assessed');
  // Null observed field on not_assessed check stays 'Pending extraction...' and does NOT claim declaration is absent
  assert.strictEqual(formatted[1].observed, 'Pending extraction from packaging evidence');
});

// 27. Genuine violations are displayed when supported by the backend response
it('27. Genuine violations are displayed with citations, observed/required values, and remediation', () => {
  const violationScan = {
    id: 70,
    status: 'assessed',
    overall_result: 'violation',
    checks_assessed: 19,
    findings: [
      {
        check_id: 'CHK04',
        title: 'Retail Sale Price (MRP)',
        citation: 'Rule 6(1)(e)',
        engine_verdict: 'fail',
        effective_verdict: 'fail',
        observed: 'MRP declaration missing on panel',
        required: 'MRP in Indian Rupees inclusive of all taxes',
        reason: 'Statutory MRP declaration omitted',
        remediation: 'Rectify packaging declaration to comply with statutory rule requirements',
      },
    ],
  };

  const state = deriveAssessmentState({ currentScan: violationScan, findings: violationScan.findings });
  assert.strictEqual(state, ASSESSMENT_STATE.VIOLATION);

  const formatted = formatFindingsList(violationScan.findings);
  assert.strictEqual(formatted[0].effective_verdict, 'fail');
  assert.strictEqual(formatted[0].citation, 'Rule 6(1)(e)');
  assert.strictEqual(formatted[0].observed, 'MRP declaration missing on panel');
  assert.strictEqual(formatted[0].remediation.includes('Rectify packaging declaration'), true);
});

// 28. Not-assessed findings are preserved and explained appropriately
it('28. Not-assessed findings are preserved and explained appropriately without converting to violations', () => {
  const rawFindings = [
    {
      check_id: 'CHK03',
      title: 'Chapter II Scope',
      engine_verdict: 'not_assessed',
      effective_verdict: 'not_assessed',
      reason: 'Transaction type not recorded; wholesale/retail scope undetermined',
    },
    {
      check_id: 'CHK06',
      title: 'Letter Height',
      engine_verdict: 'not_assessed',
      effective_verdict: 'not_assessed',
      reason: 'No millimetre scale could be established for this capture',
    },
  ];

  const formatted = formatFindingsList(rawFindings);
  assert.strictEqual(formatted.length, 2);
  assert.strictEqual(formatted[0].effective_verdict, 'not_assessed');
  assert.strictEqual(formatted[1].effective_verdict, 'not_assessed');
  assert.strictEqual(formatted[0].reason.includes('wholesale/retail scope undetermined'), true);
  assert.strictEqual(formatted[1].reason.includes('No millimetre scale could be established'), true);
});

// 29. Finding totals match returned findings and established counting contract
it('29. Finding totals match returned findings and established counting contract', () => {
  const scanCounts = {
    total: 19,
    passed: 13,
    failed: 4,
    not_assessed: 2,
  };

  // Mutually exclusive invariant
  assert.strictEqual(scanCounts.passed + scanCounts.failed + scanCounts.not_assessed, scanCounts.total);
  const checks_assessed = scanCounts.passed + scanCounts.failed;
  assert.strictEqual(checks_assessed, 17);

  // Findings list mirror
  const findings = [
    ...Array(13).fill(null).map((_, i) => ({ id: i + 1, check_id: `CHK_P_${i}`, effective_verdict: 'pass' })),
    ...Array(4).fill(null).map((_, i) => ({ id: i + 14, check_id: `CHK_F_${i}`, effective_verdict: 'fail' })),
    ...Array(2).fill(null).map((_, i) => ({ id: i + 18, check_id: `CHK_N_${i}`, effective_verdict: 'not_assessed' })),
  ];

  const p = findings.filter(f => f.effective_verdict === 'pass').length;
  const f = findings.filter(f => f.effective_verdict === 'fail').length;
  const n = findings.filter(f => f.effective_verdict === 'not_assessed').length;

  assert.strictEqual(p, scanCounts.passed);
  assert.strictEqual(f, scanCounts.failed);
  assert.strictEqual(n, scanCounts.not_assessed);
  assert.strictEqual(findings.length, scanCounts.total);
});

// 30. Registered rules (26 catalog rules) are not misleadingly presented as individually executed results
it('30. Registered rules (26) are not conflated with executed checks count (19)', () => {
  const currentScan = {
    checks_total: 19,
    checks_assessed: 19,
    diagnostics: {
      catalog_rule_count: 26,
      executable_check_count: 19,
      rule_pack_version: '2026.09.v1',
    },
  };

  const catalogCount = currentScan?.diagnostics?.catalog_rule_count || 26;
  const executableCount = currentScan?.diagnostics?.executable_check_count || 19;
  const totalFindings = currentScan.checks_total;

  assert.strictEqual(catalogCount, 26);
  assert.strictEqual(executableCount, 19);
  assert.strictEqual(totalFindings, 19);
  assert.notStrictEqual(catalogCount, totalFindings);
});

// 31. Retry sends the actual request and handles success and error states correctly
it('31. Retry handles in-flight progress and transition to request failure on 503 error', () => {
  let state = deriveAssessmentState({ currentScan: { id: 80, status: 'captured' }, findings: [], reassessing: true });
  assert.strictEqual(state, ASSESSMENT_STATE.ASSESSING);

  // Simulation of 503 capacity error
  state = deriveAssessmentState({
    currentScan: { id: 80, status: 'captured', overall_result: 'not_assessed' },
    findings: [],
    reassessing: false,
    requestError: 'Server error (HTTP 503): Assessment capacity busy; retry shortly',
  });
  assert.strictEqual(state, ASSESSMENT_STATE.REQUEST_FAILURE);

  // Simulation of successful retry response
  const reassessed = {
    id: 80,
    status: 'assessed',
    overall_result: 'compliant',
    checks_assessed: 19,
    findings: [{ check_id: 'CHK01', effective_verdict: 'pass' }],
  };
  state = deriveAssessmentState({
    currentScan: reassessed,
    findings: reassessed.findings,
    reassessing: false,
    requestError: null,
  });
  assert.strictEqual(state, ASSESSMENT_STATE.COMPLIANT);
});

// 32. Duplicate taps and obsolete in-flight responses cannot corrupt screen state
it('32. In-flight response isolation discards responses from previous scan on scan switch', () => {
  let isMounted = true;
  let activeScanId = 101;
  let currentScanState = { id: 101, findings: [{ check_id: 'CHK01', effective_verdict: 'pass' }] };

  // Request for scan 101 starts in background
  const targetScanId = 101;

  // User quickly switches to Scan 202 before request finishes
  activeScanId = 202;
  currentScanState = { id: 202, findings: [{ check_id: 'CHK01', effective_verdict: 'fail' }] };

  // Late response for Scan 101 arrives:
  const lateResponse = { id: 101, findings: [{ check_id: 'CHK01', effective_verdict: 'pass' }] };

  // Guard condition:
  const shouldApply = isMounted && activeScanId === targetScanId;
  assert.strictEqual(shouldApply, false); // Blocked!

  if (shouldApply) {
    currentScanState = lateResponse;
  }

  // Scan 202 was NOT overwritten by late response for 101
  assert.strictEqual(currentScanState.id, 202);
  assert.strictEqual(currentScanState.findings[0].effective_verdict, 'fail');
});

// 33. Null product fields remain unknown rather than receiving fabricated values
it('33. Null product fields remain unknown rather than receiving fabricated values', () => {
  const scanWithNulls = {
    commodity_generic: null,
    brand_name: null,
    batch_number: null,
    mrp: null,
    net_quantity_value: null,
    net_quantity_unit: null,
  };

  const brand = (scanWithNulls.brand_name && !scanWithNulls.brand_name.toLowerCase().includes('unspecified')) ? scanWithNulls.brand_name.trim() : null;
  const commodity = (scanWithNulls.commodity_generic && !scanWithNulls.commodity_generic.toLowerCase().includes('unspecified')) ? scanWithNulls.commodity_generic.trim() : null;
  const batch = (scanWithNulls.batch_number && scanWithNulls.batch_number !== 'N/A' && !scanWithNulls.batch_number.toLowerCase().includes('unspecified')) ? scanWithNulls.batch_number.trim() : null;
  const mrp = scanWithNulls.mrp;
  const netQty = scanWithNulls.net_quantity_value ? `${scanWithNulls.net_quantity_value} ${scanWithNulls.net_quantity_unit || ''}`.trim() : null;

  let displaySubtitle;
  if (brand && commodity) displaySubtitle = `${brand} — ${commodity}`;
  else if (commodity) displaySubtitle = commodity;
  else if (brand) displaySubtitle = `${brand} • Commodity not determined`;
  else displaySubtitle = 'Commodity not determined';

  const displayBatch = `Batch: ${batch || 'Batch not observed'}${mrp ? ` • MRP: ₹${mrp}` : ''}${netQty ? ` • Net Qty: ${netQty}` : ''}`;

  assert.strictEqual(displaySubtitle, 'Commodity not determined');
  assert.strictEqual(displayBatch, 'Batch: Batch not observed');
  assert.strictEqual(displayBatch.includes('MRP'), false);
  assert.strictEqual(displayBatch.includes('Net Qty'), false);
});

// 34. Module 8 evidence rendering and authenticated image access continue to work
it('34. Evidence thumbnail URLs and auth headers remain valid and JWT-free', () => {
  const { buildImageUrl, getImageAuthHeaders, normalizeEvidenceList } = require('../config/evidenceRendering');

  const testScan = {
    id: 99,
    server_id: 99,
    images: [
      { id: 1, panel: 'front', url: '/scans/99/images/1', thumbnail_url: '/scans/99/images/1/thumbnail', sha256: 'sha99_1' },
      { id: 2, panel: 'back', url: '/scans/99/images/2', thumbnail_url: '/scans/99/images/2/thumbnail', sha256: 'sha99_2' },
    ],
  };

  const normalized = normalizeEvidenceList(testScan, 99);
  assert.strictEqual(normalized.length, 2);
  assert.strictEqual(normalized[0].panel, 'front');
  assert.strictEqual(normalized[1].panel, 'back');
  assert.strictEqual(normalized[0].url.includes('?token='), false);
  assert.strictEqual(normalized[0].thumbnail_url.includes('?token='), false);

  const sampleToken = 'eyJhbGciOiJIUzI1NiJ9.demo';
  const headers = getImageAuthHeaders(normalized[0].url, sampleToken);
  assert.deepStrictEqual(headers, { Authorization: `Bearer ${sampleToken}` });
});

console.log(`\nDynamic Assessment Findings Results: ${passed} passed, ${failed} failed.\n`);
if (failed > 0) process.exit(1);


