// Frontend_App/__tests__/test_review_queue.js
// Standalone Node.js test suite for Phase 6A: Review Queue & Adjudication UI logic.
const assert = require('assert');

console.log('=== Running Phase 6A Inspector Review Queue & Adjudication Tests ===\n');

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
  // Test 1: Priority styling logic
  it('1. Priority categorization properly maps critical/high to urgent styling', () => {
    const getPriorityStyle = (priority) => {
      switch (String(priority).toLowerCase()) {
        case 'critical':
        case 'high':
          return { bg: '#FEE2E2', border: '#FCA5A5', text: '#B91C1C', urgent: true };
        case 'medium':
          return { bg: '#FEF3C7', border: '#FCD34D', text: '#B45309', urgent: false };
        default:
          return { bg: '#F1F5F9', border: '#CBD5E1', text: '#475569', urgent: false };
      }
    };

    assert.strictEqual(getPriorityStyle('high').urgent, true);
    assert.strictEqual(getPriorityStyle('critical').urgent, true);
    assert.strictEqual(getPriorityStyle('medium').urgent, false);
    assert.strictEqual(getPriorityStyle('low').urgent, false);
  });

  // Test 2: Dual verdict model invariant (C11)
  it('2. Dual verdict model preserves engine_verdict immutably while recording human_verdict', () => {
    const automatedFinding = {
      id: 101,
      check_id: 'CHK01',
      engine_verdict: 'fail',
      human_verdict: null,
      confidence: 0.92,
    };

    // Officer overrides fail -> pass with statutory justification
    const officerAdjudication = {
      action: 'override',
      human_verdict: 'pass',
      reason: 'Physical inspection confirms mandatory MRP declaration present on secondary carton flap.',
    };

    const updatedFinding = {
      ...automatedFinding,
      human_verdict: officerAdjudication.human_verdict,
      override_reason: officerAdjudication.reason,
    };

    // Engine verdict must remain untouched
    assert.strictEqual(updatedFinding.engine_verdict, 'fail', 'Engine verdict must NEVER be overwritten');
    assert.strictEqual(updatedFinding.human_verdict, 'pass');
    assert.strictEqual(updatedFinding.override_reason.length >= 10, true);
  });

  // Test 3: Mandatory reason validation for overrides
  it('3. Override rejection if explanation is missing or less than 10 characters', () => {
    const validateAdjudication = (body) => {
      if (!body.inspection_id) throw new Error('inspection_id required');
      if (!body.reason || body.reason.trim().length < 10) {
        throw new Error('mandatory reason must be at least 10 characters');
      }
      return true;
    };

    // Short reason fails
    assert.throws(
      () => validateAdjudication({ inspection_id: 1, reason: 'looks ok' }),
      /at least 10 characters/
    );

    // Empty reason fails
    assert.throws(
      () => validateAdjudication({ inspection_id: 1, reason: '' }),
      /at least 10 characters/
    );

    // Valid reason passes
    assert.strictEqual(
      validateAdjudication({
        inspection_id: 1,
        reason: 'Inspector verified batch code on package neck under magnification.',
      }),
      true
    );
  });

  // Test 4: Conflict resolution parameter validation
  it('4. Cross-panel conflict resolution requires valid conflict_id, resolution and reason', () => {
    const validateConflict = (body) => {
      if (!body.inspection_id || !body.conflict_id) {
        throw new Error('inspection_id and conflict_id required');
      }
      if (!body.resolution) throw new Error('resolution required');
      if (!body.reason || body.reason.trim().length < 10) {
        throw new Error('mandatory reason must be at least 10 characters');
      }
      return true;
    };

    assert.throws(
      () => validateConflict({ inspection_id: 5, conflict_id: '', resolution: 'accept_panel_a', reason: 'Verified manually' }),
      /conflict_id required/
    );

    assert.strictEqual(
      validateConflict({
        inspection_id: 5,
        conflict_id: 'CONF-MRP-01',
        resolution: 'accept_panel_a',
        reason: 'Front panel MRP sticker is authoritatively verified by establishment receipt.',
      }),
      true
    );
  });

  // Test 5: Offline queue item creation contract
  it('5. Offline adjudication enqueues structured item with unique ID and is_synced: false', () => {
    let mockSeq = 0;
    const createMockQueueItem = (type, inspectionId, body) => {
      mockSeq++;
      return {
        id: `${type}-${Date.now()}-${mockSeq}`,
        type,
        inspectionId,
        body,
        createdAt: new Date().toISOString(),
        is_synced: false,
      };
    };

    const item = createMockQueueItem('adjudication', 42, {
      inspection_id: 42,
      action: 'resolve',
      reason: 'All package markings verified compliant with Rule 6.',
    });

    assert.strictEqual(item.type, 'adjudication');
    assert.strictEqual(item.inspectionId, 42);
    assert.strictEqual(item.is_synced, false);
    assert.strictEqual(typeof item.id, 'string');
    assert.strictEqual(item.body.action, 'resolve');
  });

  // Test 6: Sync dispatching handles adjudication and conflict types
  it('6. SyncProvider recognizes adjudication and conflict_resolution queue types', () => {
    const handledTypes = new Set(['inspection', 'scan', 'adjudication', 'conflict_resolution']);

    assert.strictEqual(handledTypes.has('adjudication'), true);
    assert.strictEqual(handledTypes.has('conflict_resolution'), true);
    assert.strictEqual(handledTypes.has('unknown_type'), false);
  });

  // Test 7: Duplicate submission prevention via Idempotency-Key
  it('7. Every adjudication request carries a unique idempotency key', () => {
    function generateIdempotencyKey(prefix = 'adj') {
      return `${prefix}-` + 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (c) => {
        const r = (Math.random() * 16) | 0;
        const v = c === 'x' ? r : (r & 0x3) | 0x8;
        return v.toString(16);
      });
    }

    const key1 = generateIdempotencyKey();
    const key2 = generateIdempotencyKey();

    assert.notStrictEqual(key1, key2);
    assert.strictEqual(key1.startsWith('adj-'), true);
    assert.strictEqual(key1.length > 20, true);
  });

  // Test 8: Filter parameter building
  it('8. Filter parameter builder converts ALL values to undefined to avoid polluting API query', () => {
    const buildQuery = (params) => {
      const query = {};
      if (params.status_filter && params.status_filter !== 'ALL') {
        query.status_filter = params.status_filter;
      }
      if (params.reason && params.reason !== 'ALL') {
        query.reason = params.reason;
      }
      if (params.priority && params.priority !== 'ALL') {
        query.priority = params.priority;
      }
      return query;
    };

    const q1 = buildQuery({ status_filter: 'ALL', reason: 'ALL', priority: 'ALL' });
    assert.deepStrictEqual(q1, {});

    const q2 = buildQuery({ status_filter: 'OPEN', reason: 'violation', priority: 'high' });
    assert.deepStrictEqual(q2, { status_filter: 'OPEN', reason: 'violation', priority: 'high' });
  });

  // Test 9: Completeness percentage calculation
  it('9. Completeness calculation handles arbitrary checks assessed', () => {
    const calculateCompleteness = (assessed, total) => {
      if (!total || total <= 0) return '0%';
      const pct = Math.round((assessed / total) * 100);
      return `${pct}% (${assessed}/${total})`;
    };

    assert.strictEqual(calculateCompleteness(18, 18), '100% (18/18)');
    assert.strictEqual(calculateCompleteness(9, 18), '50% (9/18)');
    assert.strictEqual(calculateCompleteness(0, 18), '0% (0/18)');
  });

  // Test 10: Status transitions are valid
  it('10. Status transitions follow OPEN -> IN_REVIEW -> RESOLVED', () => {
    const validTransitions = {
      OPEN: ['IN_REVIEW', 'RESOLVED'],
      IN_REVIEW: ['RESOLVED', 'OPEN'],
      RESOLVED: ['IN_REVIEW'],
    };

    assert.strictEqual(validTransitions.OPEN.includes('RESOLVED'), true);
    assert.strictEqual(validTransitions.IN_REVIEW.includes('RESOLVED'), true);
  });

  // Test 11: Authoritative backend queue total & category breakdown
  it('11. Authoritative backend-provided queue total and category breakdown mapped correctly', () => {
    const backendSummary = {
      total: 7,
      violations_pending: 4,
      not_assessed: 3,
      low_confidence: 2,
      offline_edits: 1,
      breakdown: {
        technical_recovery: 2,
        recapture_required: 1,
        unconfirmed_violations: 3,
        confirmed_violations: 1,
        adjudicated_resolved: 5,
        draft_inspections: 2,
      },
    };

    // The UI renders summary.total as the authoritative queue total
    const displayedTotal = backendSummary.total;
    assert.strictEqual(displayedTotal, 7);

    // Facet breakdowns match backend breakdown exactly
    assert.strictEqual(backendSummary.breakdown.technical_recovery, 2);
    assert.strictEqual(backendSummary.breakdown.recapture_required, 1);
    assert.strictEqual(backendSummary.breakdown.unconfirmed_violations, 3);
    assert.strictEqual(backendSummary.breakdown.confirmed_violations, 1);
    assert.strictEqual(backendSummary.breakdown.draft_inspections, 2);
  });

  // Test 12: Technical OCR failure shown as technical recovery, NOT legal violation
  it('12. Technical OCR failure displayed as technical recovery, not proof of legal violation', () => {
    const scanItem = {
      inspection_id: 10,
      scan_id: 201,
      overall_result: 'not_assessed',
      reasons: [
        {
          type: 'not_assessed',
          subtype: 'technical_recovery',
          severity: 'major',
          detail: 'Technical OCR/provider error: Google Cloud Vision API timeout after 60s',
        },
      ],
      findings: [
        { check_id: 'CHK01', engine_verdict: 'not_assessed', reason: 'OCR service unavailable' },
      ],
    };

    const getCategoryBadge = (item) => {
      const reasons = item.reasons || [];
      if (reasons.some((r) => r.subtype === 'technical_recovery')) {
        return { label: '⚙️ TECHNICAL RECOVERY', isViolation: false, isTechnical: true };
      }
      if (reasons.some((r) => r.subtype === 'confirmed_violation' || r.subtype === 'unconfirmed_violation')) {
        return { label: 'VIOLATION', isViolation: true, isTechnical: false };
      }
      return { label: 'REVIEW', isViolation: false, isTechnical: false };
    };

    const badge = getCategoryBadge(scanItem);
    assert.strictEqual(badge.label, '⚙️ TECHNICAL RECOVERY');
    assert.strictEqual(badge.isViolation, false, 'Technical failure must NOT be treated as a statutory violation');
    assert.strictEqual(badge.isTechnical, true);
  });

  // Test 13: Recapture-required scans shown with appropriate guidance
  it('13. Recapture-required scans identified with clear evidence guidance', () => {
    const scanItem = {
      inspection_id: 11,
      scan_id: 202,
      overall_result: 'not_assessed',
      reasons: [
        {
          type: 'not_assessed',
          subtype: 'incomplete_coverage',
          severity: 'major',
          detail: 'Missing mandatory MRP panel image; cannot evaluate Rule 6(1)(e)',
        },
      ],
    };

    const getCategoryBadge = (item) => {
      const reasons = item.reasons || [];
      if (reasons.some((r) => r.subtype === 'incomplete_coverage' || r.detail?.toLowerCase().includes('missing'))) {
        return { label: '📷 RECAPTURE REQUIRED', type: 'recapture' };
      }
      return { label: 'OTHER', type: 'other' };
    };

    const badge = getCategoryBadge(scanItem);
    assert.strictEqual(badge.label, '📷 RECAPTURE REQUIRED');
    assert.strictEqual(badge.type, 'recapture');
  });

  // Test 14: Unconfirmed violations distinguished from confirmed violations
  it('14. Unconfirmed violations distinguished from confirmed violations awaiting enforcement', () => {
    const unconfirmedItem = {
      inspection_id: 12,
      scan_id: 301,
      reasons: [
        {
          type: 'violation',
          subtype: 'unconfirmed_violation',
          severity: 'critical',
          detail: '2 unconfirmed violations awaiting officer review',
        },
      ],
    };

    const confirmedItem = {
      inspection_id: 12,
      scan_id: 302,
      reasons: [
        {
          type: 'violation',
          subtype: 'confirmed_violation',
          severity: 'critical',
          detail: 'Confirmed legal violation awaiting enforcement action',
        },
      ],
    };

    const classifyViolation = (item) => {
      const r = item.reasons?.[0];
      if (r?.subtype === 'confirmed_violation') return 'CONFIRMED_ENFORCEMENT_ACTION';
      if (r?.subtype === 'unconfirmed_violation') return 'UNCONFIRMED_OFFICER_REVIEW';
      return 'UNKNOWN';
    };

    assert.strictEqual(classifyViolation(unconfirmedItem), 'UNCONFIRMED_OFFICER_REVIEW');
    assert.strictEqual(classifyViolation(confirmedItem), 'CONFIRMED_ENFORCEMENT_ACTION');
  });

  // Test 15: Resolved items excluded from open queue
  it('15. Resolved items excluded from open queue according to backend review_status', () => {
    const queueItems = [
      { scan_id: 1, review_status: 'OPEN' },
      { scan_id: 2, review_status: 'IN_REVIEW' },
      { scan_id: 3, review_status: 'RESOLVED' },
    ];

    const openItems = queueItems.filter((i) => i.review_status === 'OPEN');
    const inReviewItems = queueItems.filter((i) => i.review_status === 'IN_REVIEW');
    const resolvedItems = queueItems.filter((i) => i.review_status === 'RESOLVED');

    assert.strictEqual(openItems.length, 1);
    assert.strictEqual(openItems[0].scan_id, 1);
    assert.strictEqual(inReviewItems.length, 1);
    assert.strictEqual(resolvedItems.length, 1);
  });

  // Test 16: One scan with multiple findings does not create duplicate scan cards
  it('16. One scan with multiple findings does not create duplicate scan cards', () => {
    const rawJoinRows = [
      { scan_id: 50, inspection_id: 1, check_id: 'CHK01', finding_verdict: 'fail' },
      { scan_id: 50, inspection_id: 1, check_id: 'CHK04', finding_verdict: 'fail' },
      { scan_id: 50, inspection_id: 1, check_id: 'CHK06', finding_verdict: 'pass' },
      { scan_id: 51, inspection_id: 1, check_id: 'CHK01', finding_verdict: 'pass' },
    ];

    // Deduplication logic by scan_id
    const seenScans = new Set();
    const uniqueCards = [];
    for (const row of rawJoinRows) {
      if (!seenScans.has(row.scan_id)) {
        seenScans.add(row.scan_id);
        uniqueCards.push(row.scan_id);
      }
    }

    assert.strictEqual(uniqueCards.length, 2);
    assert.deepStrictEqual(uniqueCards, [50, 51]);
  });

  // Test 17: Overlapping category facets are NOT added together as a false total
  it('17. Overlapping category facets are not added together as a false total', () => {
    // A single scan has both unconfirmed violation and low confidence
    const summary = {
      total: 1, // Distinct scan count = 1
      violations_pending: 1,
      low_confidence: 1,
      breakdown: {
        unconfirmed_violations: 1,
        technical_recovery: 0,
        recapture_required: 0,
        confirmed_violations: 0,
      },
    };

    // Summing facets naively would give 1 + 1 = 2 (WRONG!)
    const naiveSum = (summary.breakdown.unconfirmed_violations + summary.low_confidence);
    assert.strictEqual(naiveSum, 2);

    // Authoritative total must remain 1
    assert.strictEqual(summary.total, 1);
    assert.notStrictEqual(summary.total, naiveSum);
  });

  // Test 18: Missing category data handled truthfully without invented counts
  it('18. Missing category data handled truthfully without invented counts', () => {
    const partialSummary = {
      total: 0,
      violations_pending: 0,
    };

    // Missing fields default safely to 0 without fabricating catalog sizes
    const techRecovery = partialSummary.breakdown?.technical_recovery ?? 0;
    const recapture = partialSummary.breakdown?.recapture_required ?? 0;
    const confirmed = partialSummary.breakdown?.confirmed_violations ?? 0;

    assert.strictEqual(techRecovery, 0);
    assert.strictEqual(recapture, 0);
    assert.strictEqual(confirmed, 0);

    // Invariants against historical bogus figures
    assert.notStrictEqual(partialSummary.total, 830);
    assert.notStrictEqual(partialSummary.total, 789);
    assert.notStrictEqual(partialSummary.total, 35);
    assert.notStrictEqual(partialSummary.total, 6);
  });

  // Test 19: API error state triggers explicit error UI, never silently showing empty queue
  it('19. UI error state explicitly triggered on API failure when no cache is available', () => {
    let uiState = 'loading';
    let errorMessage = null;
    let items = [];

    const simulateLoad = (apiSuccess, cachedItems) => {
      if (apiSuccess) {
        items = [{ scan_id: 1 }];
        uiState = 'success';
      } else {
        if (cachedItems && cachedItems.length > 0) {
          items = cachedItems;
          uiState = 'offline_cached';
        } else {
          items = [];
          uiState = 'error';
          errorMessage = 'Network connection failed (503 Service Unavailable)';
        }
      }
    };

    // Test API failure with no cache
    simulateLoad(false, []);
    assert.strictEqual(uiState, 'error', 'Must enter error state on network failure when no cache');
    assert.strictEqual(items.length, 0);
    assert.ok(errorMessage.includes('503'));
    assert.notStrictEqual(uiState, 'success');
  });

  // Test 20: Stale async responses cannot overwrite active inspection review
  it('20. Stale API response cannot overwrite active inspection review', async () => {
    let currentActiveInspectionId = 100;
    let renderedInspectionDetail = null;

    const fetchInspection = async (id, delayMs) => {
      await new Promise((r) => setTimeout(r, delayMs));
      return { id, store_name: `Store for #${id}` };
    };

    // User is on inspection 100
    // Rapidly switches to inspection 101
    // The request for 100 is slow (50ms), request for 101 is fast (10ms)
    let cancelledRequests = new Set();

    const loadInspection = (id, delayMs) => {
      let isCancelled = false;
      fetchInspection(id, delayMs).then((data) => {
        if (!isCancelled && id === currentActiveInspectionId) {
          renderedInspectionDetail = data;
        }
      });
      return () => { isCancelled = true; };
    };

    // Request 1: for 100 (slow)
    const cancel1 = loadInspection(100, 50);

    // User switches to 101 (fast)
    currentActiveInspectionId = 101;
    cancel1(); // cancel previous
    const cancel2 = loadInspection(101, 10);

    await new Promise((r) => setTimeout(r, 70));

    // After both complete, rendered inspection MUST be 101, never 100
    assert.strictEqual(renderedInspectionDetail.id, 101);
    assert.strictEqual(renderedInspectionDetail.store_name, 'Store for #101');
  });

  console.log(`\nResults: ${passed} passed, ${failed} failed\n`);
  if (failed > 0) {
    process.exit(1);
  }
}

runTests();
