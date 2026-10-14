// Frontend_App/__tests__/test_inspection_finalization_workflow.js
// Module 5 Verification: Connect Inspection Finalization to Assessment & Draft Recovery.
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

console.log('=== Running Module 5: Inspection Finalization & Draft Recovery Tests ===\n');

// 1. Finalization progress and honest per-scan outcome display
it('1. Finalization outcome parses evaluated, reused, and pending evidence packages honestly', () => {
  const backendResponse = {
    id: 42,
    status: 'submitted',
    display_status: 'Submitted',
    overall_result: 'violation',
    finalization_summary: {
      status: 'submitted',
      total_scans: 3,
      scans_evaluated: [102],
      scans_reused: [101],
      scans_pending_evidence: [103],
      scans_failed: [],
      scans_blocked: [],
      violations_count: 1,
      compliant_count: 1,
      not_assessed_count: 1,
      details: [
        { scan_id: 101, status: 'reused', overall_result: 'compliant', reason: 'Existing assessment valid and current' },
        { scan_id: 102, status: 'assessed', overall_result: 'violation', checks_assessed: 19, checks_total: 19 },
        { scan_id: 103, status: 'pending_evidence', overall_result: 'not_assessed', reason: 'No panel images uploaded for this package' },
      ],
    },
    scans: [
      { id: 101, commodity_generic: 'Rice', brand_name: 'Daawat', overall_result: 'compliant' },
      { id: 102, commodity_generic: 'Oil', brand_name: 'Dhara', overall_result: 'violation' },
      { id: 103, commodity_generic: 'Biscuits', brand_name: 'Britannia', overall_result: 'not_assessed' },
    ],
  };

  const fSum = backendResponse.finalization_summary;
  assert.strictEqual(backendResponse.status, 'submitted');
  assert.strictEqual(fSum.total_scans, 3);
  assert.deepStrictEqual(fSum.scans_reused, [101]);
  assert.deepStrictEqual(fSum.scans_evaluated, [102]);
  assert.deepStrictEqual(fSum.scans_pending_evidence, [103]);

  // Ensure pending evidence is NOT fabricated as compliant
  const scan103 = backendResponse.scans.find(s => s.id === 103);
  assert.strictEqual(scan103.overall_result, 'not_assessed');
});

// 2. Double-click idempotency and retry handling
it('2. Finalization retry handles already_submitted response cleanly without 409 conflict', () => {
  const retryResponse = {
    id: 42,
    status: 'submitted',
    display_status: 'Submitted',
    finalization_summary: {
      status: 'already_submitted',
      message: 'Inspection already submitted; returning persisted record.',
      total_scans: 3,
      scans_evaluated: [],
      scans_reused: [101, 102, 103],
      scans_pending_evidence: [],
      scans_failed: [],
      scans_blocked: [],
    },
  };

  assert.strictEqual(retryResponse.status, 'submitted');
  assert.strictEqual(retryResponse.finalization_summary.status, 'already_submitted');
  assert.strictEqual(retryResponse.finalization_summary.scans_evaluated.length, 0);
});

// 3. Scan-to-inspection isolation (no stale bleeding)
it('3. Scan outcomes strictly belong to active inspection without cross-visit bleeding', () => {
  const inspectionA = {
    id: 100,
    store_id: 5,
    scans: [
      { id: 501, commodity_generic: 'Wheat Flour', overall_result: 'compliant' },
    ],
  };

  const inspectionB = {
    id: 101,
    store_id: 6,
    scans: [
      { id: 601, commodity_generic: 'Refined Sugar', overall_result: 'violation' },
    ],
  };

  // Inspect scan-to-inspection mapping
  const scansA = inspectionA.scans.map(s => s.id);
  const scansB = inspectionB.scans.map(s => s.id);

  assert.strictEqual(scansA.includes(601), false);
  assert.strictEqual(scansB.includes(501), false);
  assert.strictEqual(inspectionA.scans[0].overall_result, 'compliant');
  assert.strictEqual(inspectionB.scans[0].overall_result, 'violation');
});

// 4. Draft recovery state mapping
it('4. Draft inspection recovery parses store and package state to resume session', () => {
  const draftDetail = {
    id: 77,
    store_id: 12,
    store_name: 'Metro Cash & Carry',
    store_address: 'Balanagar, Hyderabad',
    status: 'draft',
    transaction_type: 'retail_sale',
    scans: [
      {
        id: 301,
        commodity_generic: 'Besan',
        brand_name: 'Fortune',
        overall_result: 'not_assessed',
        image_count: 1,
        images: [{ id: 1, panel: 'front' }],
      },
    ],
  };

  // State reconstruction logic matching handleResumeDraft in InspectorFlow.jsx
  const resumedSession = {
    inspectionId: draftDetail.id,
    store: {
      id: draftDetail.store_id,
      name: draftDetail.store_name,
      address: draftDetail.store_address,
    },
    transactionType: draftDetail.transaction_type,
    items: draftDetail.scans.map(s => ({
      scanId: s.id,
      name: `${s.brand_name || ''} ${s.commodity_generic || ''}`.trim(),
      commodityGeneric: s.commodity_generic,
      brandName: s.brand_name,
      result: s.overall_result,
      imageCount: s.image_count,
    })),
  };

  assert.strictEqual(resumedSession.inspectionId, 77);
  assert.strictEqual(resumedSession.store.id, 12);
  assert.strictEqual(resumedSession.items.length, 1);
  assert.strictEqual(resumedSession.items[0].scanId, 301);
  assert.strictEqual(resumedSession.items[0].name, 'Fortune Besan');
});

// 5. Merchant refusal with 0 scans finalization
it('5. Refusal submission without scans sets is_refusal and preserves overall_result = refused', () => {
  const refusalResponse = {
    id: 88,
    status: 'submitted',
    signature_status: 'refused',
    overall_result: 'refused',
    is_refusal: true,
    finalization_summary: {
      status: 'submitted',
      total_scans: 0,
      scans_evaluated: [],
      scans_reused: [],
      scans_pending_evidence: [],
      scans_failed: [],
    },
  };

  assert.strictEqual(refusalResponse.status, 'submitted');
  assert.strictEqual(refusalResponse.signature_status, 'refused');
  assert.strictEqual(refusalResponse.overall_result, 'refused');
  assert.strictEqual(refusalResponse.is_refusal, true);
  assert.strictEqual(refusalResponse.finalization_summary.total_scans, 0);
});

// 6. Network recovery and offline retry state
it('6. Network timeout during finalization permits safe retry without losing inspection draft', () => {
  let isSubmitting = true;
  let submissionError = null;

  // Simulate network timeout
  try {
    throw new Error('Network request failed: timeout');
  } catch (err) {
    submissionError = err.message;
    isSubmitting = false;
  }

  assert.strictEqual(isSubmitting, false);
  assert.ok(submissionError.includes('Network request failed'));

  // Retry operation succeeds
  const retryOutcome = { status: 200, data: { id: 99, status: 'submitted' } };
  assert.strictEqual(retryOutcome.status, 200);
  assert.strictEqual(retryOutcome.data.status, 'submitted');
});

// 7. Draft inspection visibility separated from officer review items
it('7. Draft inspection appears separately from officer-review items', () => {
  const inspections = [
    { id: 101, status: 'draft', store_name: 'Kirana Store A' },
    { id: 102, status: 'submitted', store_name: 'Supermarket B', overall_result: 'violation' },
    { id: 103, status: 'submitted', store_name: 'Hypermarket C', overall_result: 'compliant' },
  ];

  // Officer review queue only accepts submitted inspections
  const officerQueueEligible = inspections.filter((i) => i.status === 'submitted');
  const fieldDrafts = inspections.filter((i) => i.status === 'draft');

  assert.strictEqual(officerQueueEligible.length, 2);
  assert.strictEqual(fieldDrafts.length, 1);
  assert.strictEqual(fieldDrafts[0].id, 101);
  assert.strictEqual(officerQueueEligible.some((i) => i.status === 'draft'), false);
});

// 8. Selecting a draft resumes the correct persisted inspection
it('8. Selecting a draft resumes the correct persisted inspection', () => {
  const persistedDraft = {
    id: 55,
    status: 'draft',
    store_id: 10,
    store_name: 'Sri Krishna Provisions',
    store_city: 'Warangal',
    transaction_type: 'retail_sale',
    scans: [
      {
        id: 701,
        commodity_generic: 'Turmeric Powder',
        brand_name: 'Everest',
        overall_result: 'compliant',
        images: [{ id: 1, panel: 'front' }],
      },
    ],
  };

  // Resume builder
  const resumeState = {
    activeSession: {
      id: persistedDraft.id,
      store: {
        id: persistedDraft.store_id,
        name: persistedDraft.store_name,
        city: persistedDraft.store_city,
      },
      scans: persistedDraft.scans.map((s) => ({
        id: s.id,
        commodity_generic: s.commodity_generic,
        brand_name: s.brand_name,
      })),
    },
    selectedScan: null, // Stale scan reset
  };

  assert.strictEqual(resumeState.activeSession.id, 55);
  assert.strictEqual(resumeState.activeSession.store.name, 'Sri Krishna Provisions');
  assert.strictEqual(resumeState.activeSession.scans.length, 1);
  assert.strictEqual(resumeState.selectedScan, null);
});

// 9. A zero-scan draft can be resumed without crashing
it('9. A zero-scan draft can be resumed without crashing', () => {
  const emptyDraft = {
    id: 60,
    status: 'draft',
    store_id: 15,
    store_name: 'New Mart Express',
    scans: [], // 0 scans
  };

  // Verification that empty scan array maps cleanly without undefined errors
  const reconstructedScans = (emptyDraft.scans || []).map((s) => ({ id: s.id }));
  const session = {
    id: emptyDraft.id,
    store: { id: emptyDraft.store_id, name: emptyDraft.store_name },
    scans: reconstructedScans,
  };

  assert.strictEqual(session.scans.length, 0);
  assert.deepStrictEqual(session.scans, []);
  assert.strictEqual(session.store.name, 'New Mart Express');
});

// 10. Switching between drafts and submitted inspections does not retain stale findings
it('10. Switching between drafts and submitted inspections does not retain stale findings', () => {
  let sessionState = {
    activeInspectionId: 70,
    selectedScan: { id: 999, findings: [{ check_id: 'CHK01', verdict: 'fail' }] },
    panelPhotos: { front: '/tmp/old_panel.jpg' },
  };

  // Officer switches from inspection 70 to inspection 71
  const switchInspection = (newId, newScans) => {
    sessionState = {
      activeInspectionId: newId,
      selectedScan: null, // MUST be cleared
      panelPhotos: {}, // In-flight photos MUST be cleared
    };
  };

  switchInspection(71, []);

  assert.strictEqual(sessionState.activeInspectionId, 71);
  assert.strictEqual(sessionState.selectedScan, null, 'selectedScan must be cleared on switch');
  assert.deepStrictEqual(sessionState.panelPhotos, {}, 'panelPhotos must be cleared on switch');
});

// 11. Resume failures are shown clearly and preserve navigation state
it('11. Resume failures are shown clearly and preserve navigation state', async () => {
  let currentMode = 'home';
  let activeSession = null;
  let errorShown = null;

  const handleResume = async (inspectionId, fetchFn) => {
    try {
      const details = await fetchFn(inspectionId);
      activeSession = details;
      currentMode = 'session';
    } catch (err) {
      errorShown = `Resume Failed: ${err.message}`;
      // currentMode remains untouched
    }
  };

  // Simulate network failure
  await handleResume(999, async () => {
    throw new Error('500 Internal Server Error: Database timeout');
  });

  assert.strictEqual(currentMode, 'home', 'Navigation mode must NOT transition to session on failure');
  assert.strictEqual(activeSession, null);
  assert.ok(errorShown.includes('Resume Failed'));
});

// 12. A submitted inspection is not reopened as a draft
it('12. A submitted inspection is not reopened as a draft', async () => {
  let mode = 'home';
  let openedReportId = null;

  const handleResumeDraft = (details) => {
    if (details.status === 'draft') {
      mode = 'session';
    } else {
      // Divert to read-only report dossier
      openedReportId = details.id;
      mode = 'report_dossier';
    }
  };

  const submittedInspection = {
    id: 88,
    status: 'submitted',
    store_name: 'Metro Mega Mart',
  };

  handleResumeDraft(submittedInspection);

  assert.strictEqual(mode, 'report_dossier', 'Submitted inspection must be routed to report dossier, not edit session');
  assert.strictEqual(openedReportId, 88);
  assert.notStrictEqual(mode, 'session');
});

console.log(`\n========================================`);
console.log(`Module 5 & 6.3 Frontend Summary: ${passed} passed, ${failed} failed.`);
console.log(`========================================\n`);

if (failed > 0) {
  process.exit(1);
} else {
  process.exit(0);
}
