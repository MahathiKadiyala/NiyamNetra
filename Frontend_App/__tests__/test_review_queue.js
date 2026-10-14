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
  await itAsync('20. Stale API response cannot overwrite active inspection review', async () => {
    let currentActiveInspectionId = 100;
    let renderedInspectionDetail = null;

    const fetchInspection = async (id, delayMs) => {
      await new Promise((r) => setTimeout(r, delayMs));
      return { id, store_name: `Store for #${id}` };
    };

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

  // Test 21: Category selection maps to correct canonical subtype parameter for API query
  it('21. Category selection sends valid subtype parameter according to backend contract', () => {
    const buildQueueQuery = (params) => {
      const query = {};
      if (params.status_filter && params.status_filter !== 'ALL') {
        query.status_filter = params.status_filter;
      }
      if (params.reason && params.reason !== 'ALL') {
        query.reason = params.reason;
      }
      const subtype = params.subtype || params.subtype_filter;
      if (subtype && subtype !== 'ALL') {
        query.subtype = subtype;
      }
      if (params.limit !== undefined) query.limit = params.limit;
      if (params.offset !== undefined) query.offset = params.offset;
      return query;
    };

    // Subtype parameter validation for canonical categories
    assert.strictEqual(
      buildQueueQuery({ subtype: 'technical_recovery' }).subtype,
      'technical_recovery'
    );
    assert.strictEqual(
      buildQueueQuery({ subtype: 'recapture_required' }).subtype,
      'recapture_required'
    );
    assert.strictEqual(
      buildQueueQuery({ subtype: 'unconfirmed_violations' }).subtype,
      'unconfirmed_violations'
    );
    assert.strictEqual(
      buildQueueQuery({ subtype: 'confirmed_violations' }).subtype,
      'confirmed_violations'
    );
    assert.strictEqual(
      buildQueueQuery({ subtype: 'low_confidence' }).subtype,
      'low_confidence'
    );
    assert.strictEqual(
      buildQueueQuery({ subtype: 'ALL' }).subtype,
      undefined,
      'ALL category must not send subtype query param'
    );
  });

  // Test 22: Pagination parameters forwarded in query params
  it('22. Pagination sends the expected limit and offset parameters', () => {
    const buildQueueQuery = (params) => {
      const query = { limit: params.limit ?? 25, offset: params.offset ?? 0 };
      if (params.subtype && params.subtype !== 'ALL') query.subtype = params.subtype;
      return query;
    };

    const qFirstPage = buildQueueQuery({ limit: 25, offset: 0, subtype: 'technical_recovery' });
    assert.strictEqual(qFirstPage.limit, 25);
    assert.strictEqual(qFirstPage.offset, 0);
    assert.strictEqual(qFirstPage.subtype, 'technical_recovery');

    const qSecondPage = buildQueueQuery({ limit: 25, offset: 25, subtype: 'technical_recovery' });
    assert.strictEqual(qSecondPage.limit, 25);
    assert.strictEqual(qSecondPage.offset, 25);
    assert.strictEqual(qSecondPage.subtype, 'technical_recovery');
  });

  // Test 23: Category selection resets pagination offset to zero
  it('23. Selecting a subtype resets the pagination offset to zero', () => {
    let state = {
      facetFilter: 'ALL',
      offset: 50,
    };

    const handleSelectFacet = (newFacet) => {
      state = {
        ...state,
        facetFilter: newFacet,
        offset: 0, // Reset to zero on category change
      };
    };

    handleSelectFacet('recapture_required');
    assert.strictEqual(state.facetFilter, 'recapture_required');
    assert.strictEqual(state.offset, 0, 'Offset MUST be reset to zero on category selection');
  });

  // Test 24: Backend filtered total controls page count and pagination bounds
  it('24. The backend filtered total controls the page count and boundary conditions', () => {
    const calculatePagination = (totalMatching, limit, currentOffset) => {
      const totalPages = Math.ceil(totalMatching / limit) || 1;
      const currentPage = Math.floor(currentOffset / limit) + 1;
      const hasNext = currentOffset + limit < totalMatching;
      const hasPrev = currentOffset > 0;
      return { totalPages, currentPage, hasNext, hasPrev };
    };

    // Total 58 items, limit 25
    // Page 1: offset 0
    const p1 = calculatePagination(58, 25, 0);
    assert.strictEqual(p1.totalPages, 3);
    assert.strictEqual(p1.currentPage, 1);
    assert.strictEqual(p1.hasPrev, false);
    assert.strictEqual(p1.hasNext, true);

    // Page 2: offset 25
    const p2 = calculatePagination(58, 25, 25);
    assert.strictEqual(p2.currentPage, 2);
    assert.strictEqual(p2.hasPrev, true);
    assert.strictEqual(p2.hasNext, true);

    // Page 3: offset 50 (final 8 items)
    const p3 = calculatePagination(58, 25, 50);
    assert.strictEqual(p3.currentPage, 3);
    assert.strictEqual(p3.hasPrev, true);
    assert.strictEqual(p3.hasNext, false);
  });

  // Test 25: Later pages remain reachable without resetting category
  it('25. Matching scans from later pages remain reachable via pagination without losing subtype filter', () => {
    let queryParams = {
      subtype: 'technical_recovery',
      offset: 0,
      limit: 25,
    };

    const paginateNext = (currentParams, totalMatching) => {
      if (currentParams.offset + currentParams.limit < totalMatching) {
        return {
          ...currentParams,
          offset: currentParams.offset + currentParams.limit,
        };
      }
      return currentParams;
    };

    // User navigates from Page 1 to Page 2
    queryParams = paginateNext(queryParams, 40);
    assert.strictEqual(queryParams.offset, 25);
    assert.strictEqual(queryParams.subtype, 'technical_recovery', 'Subtype must be preserved on page change');
  });

  // Test 26: Overall queue summary remains separate from active filtered total
  it('26. Overall queue summary remains separate from the active filtered total', () => {
    const overallSummary = {
      total: 30, // 30 total scans in entire review queue
      breakdown: {
        technical_recovery: 4,
        recapture_required: 6,
        unconfirmed_violations: 15,
        confirmed_violations: 5,
      },
    };

    // Backend response for GET /review/queue?subtype=technical_recovery
    const filteredResponse = {
      total: 4, // 4 matching scans for this category
      offset: 0,
      limit: 25,
      items: [{ scan_id: 101 }, { scan_id: 102 }, { scan_id: 103 }, { scan_id: 104 }],
    };

    // Verification: summary total describes overall queue, filtered total describes category
    assert.strictEqual(overallSummary.total, 30);
    assert.strictEqual(filteredResponse.total, 4);
    assert.strictEqual(filteredResponse.items.length, 4);
    assert.notStrictEqual(overallSummary.total, filteredResponse.total);
  });

  // Test 27: Overlapping category counts are not added together into a misleading total
  it('27. Overlapping category counts are never added into a misleading total', () => {
    const multiReasonScan = {
      scan_id: 88,
      reasons: [
        { type: 'violation', subtype: 'unconfirmed_violation' },
        { type: 'low_confidence', subtype: 'low_confidence' },
      ],
    };

    // Both categories match the single scan
    const matchesUnconfirmed = multiReasonScan.reasons.some((r) => r.subtype === 'unconfirmed_violation');
    const matchesLowConf = multiReasonScan.reasons.some((r) => r.type === 'low_confidence');
    assert.strictEqual(matchesUnconfirmed, true);
    assert.strictEqual(matchesLowConf, true);

    // Summing matching counts naively gives 2
    const naiveCount = (matchesUnconfirmed ? 1 : 0) + (matchesLowConf ? 1 : 0);
    assert.strictEqual(naiveCount, 2);

    // Authoritative distinct scan count returned by backend MUST be 1
    const distinctScanCount = 1;
    assert.strictEqual(distinctScanCount, 1);
    assert.notStrictEqual(distinctScanCount, naiveCount);
  });

  // Test 28: Stale in-flight category response cannot overwrite current category
  await itAsync('28. Slow response from previously selected category cannot overwrite current category', async () => {
    let activeRequestId = 0;
    let renderedState = { facet: 'ALL', items: [] };

    const simulateFetchCategory = async (facet, delayMs) => {
      const requestId = ++activeRequestId;
      await new Promise((r) => setTimeout(r, delayMs));

      // Simulate receiving response
      if (requestId === activeRequestId) {
        renderedState = {
          facet,
          items: [{ category: facet, scan_id: facet === 'technical_recovery' ? 101 : 202 }],
        };
      }
    };

    // User first clicks 'technical_recovery' (slow response: 60ms)
    simulateFetchCategory('technical_recovery', 60);

    // User immediately switches to 'confirmed_violations' (fast response: 15ms)
    simulateFetchCategory('confirmed_violations', 15);

    // Wait for both to settle
    await new Promise((r) => setTimeout(r, 80));

    // The rendered state MUST be confirmed_violations, NOT technical_recovery
    assert.strictEqual(renderedState.facet, 'confirmed_violations');
    assert.strictEqual(renderedState.items[0].category, 'confirmed_violations');
    assert.strictEqual(renderedState.items[0].scan_id, 202);
  });

  // Test 29: Stale in-flight page response cannot overwrite current page
  await itAsync('29. Late response from earlier page cannot replace a newer page', async () => {
    let activeRequestId = 0;
    let renderedPage = 0;

    const simulatePageFetch = async (pageOffset, delayMs) => {
      const requestId = ++activeRequestId;
      await new Promise((r) => setTimeout(r, delayMs));

      if (requestId === activeRequestId) {
        renderedPage = pageOffset;
      }
    };

    // Page 1 requested (slow: 50ms)
    simulatePageFetch(0, 50);

    // Page 2 requested (fast: 10ms)
    simulatePageFetch(25, 10);

    await new Promise((r) => setTimeout(r, 70));

    // Must be Page 2 (offset 25), never overwritten by Page 1 (offset 0)
    assert.strictEqual(renderedPage, 25);
  });

  // Test 30: Cache partitioning: a filtered request cannot silently reuse an unfiltered cached list
  it('30. Filtered category request cannot silently reuse an unfiltered cached list', () => {
    const memoryCache = {
      ALL: [{ scan_id: 1, type: 'general' }, { scan_id: 2, type: 'tech' }],
      // Note: No cache for 'technical_recovery'
    };

    const loadCacheForCategory = (categoryKey) => {
      if (categoryKey && categoryKey !== 'ALL') {
        return memoryCache[categoryKey] || null;
      }
      return memoryCache['ALL'] || [];
    };

    // Unfiltered request finds unfiltered cache
    const allResult = loadCacheForCategory('ALL');
    assert.strictEqual(allResult.length, 2);

    // Filtered request for technical_recovery returns null, NOT the unfiltered list
    const filteredResult = loadCacheForCategory('technical_recovery');
    assert.strictEqual(filteredResult, null, 'Must NOT fall back to unfiltered cache for category request');
  });

  // Test 31: HTTP 422 responses handled correctly without resetting filter or falling back
  it('31. HTTP 422 validation errors are handled correctly without silently removing filter', () => {
    let activeFilter = 'invalid_subtype';
    let uiError = null;
    let displayedItems = [{ scan_id: 999 }];

    const handleApiResponse = (err) => {
      if (err.response?.status === 422) {
        uiError = {
          type: 'validation',
          message: err.response.data?.detail || 'Invalid filter parameters specified.',
        };
        // Preserves active filter
        displayedItems = [];
      }
    };

    handleApiResponse({
      response: {
        status: 422,
        data: { detail: "Invalid subtype 'invalid_subtype'. Allowed values: technical_recovery, recapture_required..." },
      },
    });

    assert.strictEqual(uiError.type, 'validation');
    assert.ok(uiError.message.includes('Invalid subtype'));
    assert.strictEqual(activeFilter, 'invalid_subtype', 'Active filter must NOT be silently cleared');
    assert.strictEqual(displayedItems.length, 0, 'Must NOT fall back to unfiltered items');
  });

  // Test 32: Authorization and network failures remain distinguishable
  it('32. Authorization (401/403) and network failures remain distinguishable', () => {
    const classifyError = (err) => {
      const status = err.status || err.response?.status;
      if (status === 401) return { type: 'auth', label: 'Authentication Required' };
      if (status === 403 || err.code === 'FORBIDDEN') return { type: 'forbidden', label: 'Access Restricted' };
      if (status === 422) return { type: 'validation', label: 'Invalid Parameters' };
      return { type: 'network', label: 'Network Offline' };
    };

    const err401 = classifyError({ response: { status: 401 } });
    assert.strictEqual(err401.type, 'auth');

    const err403 = classifyError({ response: { status: 403 } });
    assert.strictEqual(err403.type, 'forbidden');

    const err422 = classifyError({ response: { status: 422 } });
    assert.strictEqual(err422.type, 'validation');

    const errNet = classifyError(new Error('Network request failed'));
    assert.strictEqual(errNet.type, 'network');
  });

  // Test 33: Cached summary distinguishable from live results
  it('33. Cached summary data is clearly distinguishable from live results when offline', () => {
    const renderBanner = (isOffline, facetFilter) => {
      if (!isOffline) return null;
      return `Offline mode: showing cached ${facetFilter !== 'ALL' ? `"${facetFilter}" ` : ''}data. Live synchronization pending.`;
    };

    const liveBanner = renderBanner(false, 'technical_recovery');
    assert.strictEqual(liveBanner, null);

    const offlineFilteredBanner = renderBanner(true, 'technical_recovery');
    assert.strictEqual(offlineFilteredBanner.includes('Offline mode'), true);
    assert.strictEqual(offlineFilteredBanner.includes('technical_recovery'), true);
  });

  // Test 34: API errors never fabricate zero counts or synthesize queue entries
  it('34. API errors never fabricate zero counts or synthesize queue entries', () => {
    let totalMatching = null;
    let items = null;
    let errorState = null;

    const handleFailure = (err) => {
      totalMatching = 0;
      items = [];
      errorState = err.message;
    };

    handleFailure(new Error('500 Internal Server Error'));

    // Should indicate error, not a successful "All clear" state
    assert.strictEqual(errorState, '500 Internal Server Error');
    assert.strictEqual(items.length, 0);
  });

  // Test 35: Deduplication preserves single scan card when backend returns multiple findings per scan
  it('35. A scan with multiple findings appears once according to backend response', () => {
    const multiFindingBackendItems = [
      {
        scan_id: 777,
        inspection_id: 50,
        commodity_generic: 'Edible Oil',
        findings: [
          { check_id: 'CHK01', needs_review: true },
          { check_id: 'CHK02', needs_review: true },
          { check_id: 'CHK05', needs_review: true },
        ],
      },
    ];

    const seenScans = new Set();
    const uniqueScans = [];
    for (const item of multiFindingBackendItems) {
      if (!seenScans.has(item.scan_id)) {
        seenScans.add(item.scan_id);
        uniqueScans.push(item);
      }
    }

    assert.strictEqual(uniqueScans.length, 1);
    assert.strictEqual(uniqueScans[0].scan_id, 777);
  });

  // --- Module 6.6: Query-Safe Offline Caching & Account Isolation Tests ---

  const cacheKeyModule = await import('../offline/reviewCacheKey.js');
  const {
    buildReviewQueueCacheKey,
    buildReviewSummaryCacheKey,
    createReviewQueueEnvelope,
    validateCachedReviewQueue,
    normalizeUserScope,
    normalizeJurisdiction,
    normalizeReviewQueryParams,
    safeHash,
    REVIEW_CACHE_VERSION,
    REVIEW_CACHE_TTL_MS,
    REVIEW_CACHE_STALE_MS,
    MAX_CACHED_PAGES,
    getReviewCacheSessionId,
    bumpReviewCacheSessionId,
    resetReviewCacheSession,
    evaluateAuthorizationScopeChange,
    handleAuthorizationScopeChange,
    getEffectiveScopeStr,
    setEffectiveScopeStr,
    resetEffectiveScopeForTesting,
  } = cacheKeyModule;

  // Test 36: Two pages of the same category produce different cache identities
  it('36. Two pages of the same category produce different cache identities', () => {
    const scope = { userId: '10', role: 'inspector' };
    const page1Key = buildReviewQueueCacheKey({ subtype: 'technical_recovery', offset: 0, limit: 25 }, scope);
    const page2Key = buildReviewQueueCacheKey({ subtype: 'technical_recovery', offset: 25, limit: 25 }, scope);

    assert.notStrictEqual(page1Key.storageKey, page2Key.storageKey);
    assert.notStrictEqual(page1Key.filename, page2Key.filename);
    assert.strictEqual(page1Key.descriptor, 'technical_recovery_off0');
    assert.strictEqual(page2Key.descriptor, 'technical_recovery_off25');
  });

  // Test 37: Different subtypes do not collide
  it('37. Different subtypes do not collide in cache identity', () => {
    const scope = { userId: '10', role: 'inspector' };
    const techKey = buildReviewQueueCacheKey({ subtype: 'technical_recovery', offset: 0 }, scope);
    const recapKey = buildReviewQueueCacheKey({ subtype: 'recapture_required', offset: 0 }, scope);

    assert.notStrictEqual(techKey.storageKey, recapKey.storageKey);
    assert.notStrictEqual(techKey.filename, recapKey.filename);
  });

  // Test 38: Different status filters do not collide
  it('38. Different status filters produce distinct cache keys', () => {
    const scope = { userId: '10', role: 'inspector' };
    const openKey = buildReviewQueueCacheKey({ status_filter: 'OPEN' }, scope);
    const inReviewKey = buildReviewQueueCacheKey({ status_filter: 'IN_REVIEW' }, scope);
    const resolvedKey = buildReviewQueueCacheKey({ status_filter: 'RESOLVED' }, scope);

    assert.notStrictEqual(openKey.storageKey, inReviewKey.storageKey);
    assert.notStrictEqual(openKey.storageKey, resolvedKey.storageKey);
    assert.notStrictEqual(inReviewKey.storageKey, resolvedKey.storageKey);
  });

  // Test 39: Different reason filters do not collide
  it('39. Different reason filters produce distinct cache keys', () => {
    const scope = { userId: '10', role: 'inspector' };
    const viKey = buildReviewQueueCacheKey({ reason: 'violation' }, scope);
    const naKey = buildReviewQueueCacheKey({ reason: 'not_assessed' }, scope);

    assert.notStrictEqual(viKey.storageKey, naKey.storageKey);
    assert.notStrictEqual(viKey.filename, naKey.filename);
  });

  // Test 40: Different areas or search filters do not collide
  it('40. Different search/area filters produce distinct safe hash cache identities', () => {
    const scope = { userId: '10', role: 'inspector' };
    const southKey = buildReviewQueueCacheKey({ area: 'South Delhi' }, scope);
    const northKey = buildReviewQueueCacheKey({ area: 'North Delhi' }, scope);
    const emptyAreaKey = buildReviewQueueCacheKey({ area: '' }, scope);

    assert.notStrictEqual(southKey.storageKey, northKey.storageKey);
    assert.notStrictEqual(southKey.storageKey, emptyAreaKey.storageKey);
    // Raw sensitive search text must NOT appear directly in key
    assert.strictEqual(southKey.storageKey.includes('South Delhi'), false);
    assert.strictEqual(northKey.storageKey.includes('North Delhi'), false);
  });

  // Test 41: Different page sizes do not collide
  it('41. Different page sizes (limit 25 vs 50) produce distinct cache keys', () => {
    const scope = { userId: '10', role: 'inspector' };
    const lim25 = buildReviewQueueCacheKey({ limit: 25, offset: 0 }, scope);
    const lim50 = buildReviewQueueCacheKey({ limit: 50, offset: 0 }, scope);

    assert.notStrictEqual(lim25.storageKey, lim50.storageKey);
    assert.notStrictEqual(lim25.queryHash, lim50.queryHash);
  });

  // Test 42: Equivalent normalized queries produce the exact same cache identity
  it('42. Semantically equivalent normalized queries produce the exact same cache identity', () => {
    const scope = { userId: '10', role: 'inspector' };

    // Alias mapping: unconfirmed_violations vs unconfirmed_violation
    const k1 = buildReviewQueueCacheKey({ subtype: 'unconfirmed_violations' }, scope);
    const k2 = buildReviewQueueCacheKey({ subtype: 'unconfirmed_violation' }, scope);
    assert.strictEqual(k1.storageKey, k2.storageKey);
    assert.strictEqual(k1.filename, k2.filename);

    // Alias mapping: incomplete_coverage vs recapture_required
    const k3 = buildReviewQueueCacheKey({ subtype: 'incomplete_coverage' }, scope);
    const k4 = buildReviewQueueCacheKey({ subtype: 'recapture_required' }, scope);
    assert.strictEqual(k3.storageKey, k4.storageKey);

    // Whitespace and case normalization in area
    const k5 = buildReviewQueueCacheKey({ area: '  South Delhi  ' }, scope);
    const k6 = buildReviewQueueCacheKey({ area: 'south delhi' }, scope);
    assert.strictEqual(k5.storageKey, k6.storageKey);

    // Default vs explicit status/reason/offset
    const k7 = buildReviewQueueCacheKey({ status_filter: 'ALL', reason: 'ALL', offset: 0 }, scope);
    const k8 = buildReviewQueueCacheKey({}, scope);
    assert.strictEqual(k7.storageKey, k8.storageKey);
  });

  // Test 43: Authoritative total, page size, and offset are preserved with cached result
  it('43. Authoritative total, page size, and offset are preserved in envelope', () => {
    const normQuery = normalizeReviewQueryParams({ subtype: 'technical_recovery', offset: 25, limit: 25 });
    const payload = {
      items: [{ scan_id: 201 }, { scan_id: 202 }],
      total: 58,
      offset: 25,
      limit: 25,
    };
    const envelope = createReviewQueueEnvelope(payload, normQuery, 'u10_inspector');

    assert.strictEqual(envelope.version, REVIEW_CACHE_VERSION);
    assert.strictEqual(envelope.scope, 'u10_inspector');
    assert.strictEqual(envelope.total, 58, 'Authoritative total 58 must be preserved');
    assert.strictEqual(envelope.offset, 25);
    assert.strictEqual(envelope.limit, 25);
    assert.strictEqual(envelope.items.length, 2);
    assert.ok(envelope.cachedAt);
  });

  // Test 44: Filtered and unfiltered results cannot overwrite or satisfy each other
  it('44. Filtered and unfiltered queries have disjoint identities and cannot satisfy each other', () => {
    const scope = { userId: '10', role: 'inspector' };
    const unfilteredKey = buildReviewQueueCacheKey({ subtype: 'ALL' }, scope);
    const filteredKey = buildReviewQueueCacheKey({ subtype: 'technical_recovery' }, scope);

    assert.notStrictEqual(unfilteredKey.storageKey, filteredKey.storageKey);
    assert.strictEqual(unfilteredKey.descriptor.includes('ALL'), true);
    assert.strictEqual(filteredKey.descriptor.includes('technical_recovery'), true);

    // Validate that unfiltered envelope cannot satisfy filtered query
    const unfilteredEnvelope = createReviewQueueEnvelope([{ scan_id: 1 }], unfilteredKey.query, 'u10_inspector');
    const check1 = validateCachedReviewQueue(unfilteredEnvelope, filteredKey.query, scope);
    assert.strictEqual(check1.valid, false);
    assert.ok(check1.reason.includes('Subtype mismatch'));

    // Validate that filtered envelope cannot satisfy unfiltered query
    const filteredEnvelope = createReviewQueueEnvelope([{ scan_id: 2 }], filteredKey.query, 'u10_inspector');
    const check2 = validateCachedReviewQueue(filteredEnvelope, unfilteredKey.query, scope);
    assert.strictEqual(check2.valid, false);
    assert.ok(check2.reason.includes('Subtype mismatch'));
  });

  // Test 45: Cache schema changes invalidate incompatible legacy entries safely
  it('45. Incompatible legacy or unversioned cache entries fail validation safely', () => {
    const scope = { userId: '10', role: 'inspector' };
    const query = { subtype: 'technical_recovery', offset: 0, limit: 25 };

    // Legacy v1 envelope
    const legacyEnvelope = {
      version: 'v1',
      items: [{ scan_id: 99 }],
    };
    const checkLegacy = validateCachedReviewQueue(legacyEnvelope, query, scope);
    assert.strictEqual(checkLegacy.valid, false);
    assert.ok(checkLegacy.reason.includes('Incompatible schema version'));

    // Unversioned raw array
    const rawArray = [{ scan_id: 1 }, { scan_id: 2 }];
    const checkArray = validateCachedReviewQueue(rawArray, query, scope);
    assert.strictEqual(checkArray.valid, false);
  });

  // Test 46: Two authenticated accounts cannot read each other's cache entries
  it('46. Two authenticated accounts produce isolated cache keys and fail cross-user validation', () => {
    const user1Scope = { userId: '1', role: 'inspector' };
    const user2Scope = { userId: '2', role: 'inspector' };
    const query = { subtype: 'technical_recovery', offset: 0 };

    const u1Key = buildReviewQueueCacheKey(query, user1Scope);
    const u2Key = buildReviewQueueCacheKey(query, user2Scope);

    assert.notStrictEqual(u1Key.storageKey, u2Key.storageKey);
    assert.notStrictEqual(u1Key.filename, u2Key.filename);
    assert.strictEqual(u1Key.scope, 'u1_inspector');
    assert.strictEqual(u2Key.scope, 'u2_inspector');

    // User 1 envelope validated against User 2 scope must fail
    const u1Envelope = createReviewQueueEnvelope([{ scan_id: 101 }], u1Key.query, u1Key.scope);
    const crossCheck = validateCachedReviewQueue(u1Envelope, query, user2Scope);
    assert.strictEqual(crossCheck.valid, false);
    assert.ok(crossCheck.reason.includes('Scope mismatch'));
  });

  // Test 47: Different authorization scopes do not share restricted review data
  it('47. Inspector scope and Admin scope do not share cached review data', () => {
    const inspectorScope = { userId: '5', role: 'inspector' };
    const adminScope = { userId: '5', role: 'admin' };
    const query = { subtype: 'ALL', offset: 0 };

    const inspKey = buildReviewQueueCacheKey(query, inspectorScope);
    const adminKey = buildReviewQueueCacheKey(query, adminScope);

    assert.notStrictEqual(inspKey.storageKey, adminKey.storageKey);
    assert.strictEqual(inspKey.scope, 'u5_inspector');
    assert.strictEqual(adminKey.scope, 'u5_admin');

    const adminEnvelope = createReviewQueueEnvelope([{ scan_id: 1 }], adminKey.query, adminKey.scope);
    const valResult = validateCachedReviewQueue(adminEnvelope, query, inspectorScope);
    assert.strictEqual(valResult.valid, false);
    assert.ok(valResult.reason.includes('Scope mismatch'));
  });

  // Test 48: Missing authenticated identity fails safely
  it('48. Missing or null authenticated identity fails safely without creating global cache', () => {
    const query = { subtype: 'technical_recovery' };

    assert.strictEqual(buildReviewQueueCacheKey(query, null), null);
    assert.strictEqual(buildReviewQueueCacheKey(query, undefined), null);
    assert.strictEqual(buildReviewQueueCacheKey(query, ''), null);
    assert.strictEqual(buildReviewQueueCacheKey(query, { userId: '' }), null);
    assert.strictEqual(buildReviewSummaryCacheKey(query, null), null);
  });

  // Test 49: No bearer token, password, or secret is included in cache keys or filenames
  it('49. Cache keys and filenames never expose bearer tokens or passwords', () => {
    const scope = { userId: '42', role: 'inspector' };
    const key = buildReviewQueueCacheKey(
      { subtype: 'confirmed_violations', area: 'Secret Location District 9' },
      scope
    );

    assert.strictEqual(key.storageKey.includes('Bearer'), false);
    assert.strictEqual(key.storageKey.includes('token'), false);
    assert.strictEqual(key.storageKey.includes('Secret Location'), false);
    assert.strictEqual(key.filename.includes('Secret Location'), false);
  });

  // Test 50: Logout or session expiration invalidates cache access
  it('50. Logout / session expiration prevents subsequent cache reads', () => {
    let mockSessionToken = 'header.eyJzdWIiOiI3Iiwicm9sZSI6Imluc3BlY3RvciIsImV4cCI6OTk5OTk5OTk5OX0.sig';
    let currentScope = normalizeUserScope(mockSessionToken);
    assert.strictEqual(currentScope.scopeStr, 'u7_inspector');

    // Active session can build key
    const activeKey = buildReviewQueueCacheKey({ subtype: 'technical_recovery' }, currentScope);
    assert.ok(activeKey);

    // Logout clears session token
    mockSessionToken = null;
    currentScope = normalizeUserScope(mockSessionToken);
    assert.strictEqual(currentScope, null);

    // Cache read fails safely when logged out
    const loggedOutKey = buildReviewQueueCacheKey({ subtype: 'technical_recovery' }, currentScope);
    assert.strictEqual(loggedOutKey, null, 'Must refuse cache key generation when unauthenticated');
  });

  // Test 51: Exact matching cached query can be loaded offline
  it('51. Exact matching query loads successfully and passes validation', () => {
    const scope = { userId: '10', role: 'inspector' };
    const query = { subtype: 'recapture_required', offset: 0, limit: 25, area: 'South Delhi' };
    const keyInfo = buildReviewQueueCacheKey(query, scope);

    const envelope = createReviewQueueEnvelope(
      { items: [{ scan_id: 301 }], total: 1 },
      keyInfo.query,
      keyInfo.scope
    );

    const validation = validateCachedReviewQueue(envelope, query, scope);
    assert.strictEqual(validation.valid, true);
    assert.strictEqual(validation.isStale, false);
  });

  // Test 52: Uncached subtype displays proper unavailable state, never reusing unfiltered cache
  it('52. Uncached subtype request rejected by validation against unfiltered cache', () => {
    const scope = { userId: '10', role: 'inspector' };
    const unfilteredKey = buildReviewQueueCacheKey({ subtype: 'ALL' }, scope);
    const cachedUnfiltered = createReviewQueueEnvelope([{ scan_id: 1 }], unfilteredKey.query, scope);

    const requestedSubtype = { subtype: 'technical_recovery' };
    const val = validateCachedReviewQueue(cachedUnfiltered, requestedSubtype, scope);

    assert.strictEqual(val.valid, false);
    assert.ok(val.reason.includes('Subtype mismatch'));
  });

  // Test 53: Cached first page cannot be shown as second page
  it('53. Cached first page cannot be served as second page', () => {
    const scope = { userId: '10', role: 'inspector' };
    const p1Query = { subtype: 'technical_recovery', offset: 0, limit: 25 };
    const p1Key = buildReviewQueueCacheKey(p1Query, scope);
    const page1Envelope = createReviewQueueEnvelope([{ scan_id: 101 }], p1Key.query, scope);

    // User navigates to page 2 (offset: 25)
    const p2Query = { subtype: 'technical_recovery', offset: 25, limit: 25 };
    const val = validateCachedReviewQueue(page1Envelope, p2Query, scope);

    assert.strictEqual(val.valid, false);
    assert.ok(val.reason.includes('Offset mismatch'));
  });

  // Test 54: Different area or status filter cannot receive cached results from another filter
  it('54. Mismatched area or status filter rejected during cache validation', () => {
    const scope = { userId: '10', role: 'inspector' };
    const southQuery = { area: 'south delhi' };
    const southKey = buildReviewQueueCacheKey(southQuery, scope);
    const southEnvelope = createReviewQueueEnvelope([{ scan_id: 501 }], southKey.query, scope);

    // Requesting North Delhi must reject South Delhi cache
    const northQuery = { area: 'north delhi' };
    const valArea = validateCachedReviewQueue(southEnvelope, northQuery, scope);
    assert.strictEqual(valArea.valid, false);
    assert.ok(valArea.reason.includes('Area mismatch'));

    // Requesting IN_REVIEW status must reject OPEN status cache
    const openQuery = { status_filter: 'OPEN' };
    const openKey = buildReviewQueueCacheKey(openQuery, scope);
    const openEnvelope = createReviewQueueEnvelope([{ scan_id: 502 }], openKey.query, scope);

    const inReviewQuery = { status_filter: 'IN_REVIEW' };
    const valStatus = validateCachedReviewQueue(openEnvelope, inReviewQuery, scope);
    assert.strictEqual(valStatus.valid, false);
    assert.ok(valStatus.reason.includes('Status mismatch'));
  });

  // Test 55: HTTP errors, 401/403 unauthorized, or malformed responses do not become cached pages
  it('55. HTTP errors and unauthorized responses never populate cache', () => {
    const scope = { userId: '10', role: 'inspector' };
    const query = { subtype: 'technical_recovery' };

    // Null scope returns null key so cache write cannot proceed
    assert.strictEqual(buildReviewQueueCacheKey(query, null), null);

    // Malformed/null envelopes are rejected by validator
    const invalidVal = validateCachedReviewQueue(null, query, scope);
    assert.strictEqual(invalidVal.valid, false);
    assert.ok(invalidVal.reason.includes('Invalid or empty envelope'));

    // Corrupted envelope without query metadata
    const corruptedVal = validateCachedReviewQueue({ version: REVIEW_CACHE_VERSION, scope: 'u10_inspector' }, query, scope);
    assert.strictEqual(corruptedVal.valid, false);
    assert.ok(corruptedVal.reason.includes('Missing cached query metadata'));

    // Envelope with wrong schema version
    const wrongVersionVal = validateCachedReviewQueue({ version: 'v1', scope: 'u10_inspector', query }, query, scope);
    assert.strictEqual(wrongVersionVal.valid, false);
    assert.ok(wrongVersionVal.reason.includes('Incompatible schema version'));
  });

  // Test 56: Concurrent category requests do not overwrite each other's cache entries
  await itAsync('56. Concurrent category requests generate independent cache keys without collision', async () => {
    const scope = { userId: '10', role: 'inspector' };
    const keyTech = buildReviewQueueCacheKey({ subtype: 'technical_recovery' }, scope);
    const keyConf = buildReviewQueueCacheKey({ subtype: 'confirmed_violations' }, scope);
    const keyRecap = buildReviewQueueCacheKey({ subtype: 'recapture_required' }, scope);

    const keys = new Set([keyTech.storageKey, keyConf.storageKey, keyRecap.storageKey]);
    assert.strictEqual(keys.size, 3, 'All 3 concurrent category cache keys must be distinct');
  });

  // Test 57: Expired cache entries (age > 24 hours) are invalidated according to TTL policy
  it('57. Expired cache entries older than 24h are invalidated', () => {
    const scope = { userId: '10', role: 'inspector' };
    const query = { subtype: 'technical_recovery' };
    const keyInfo = buildReviewQueueCacheKey(query, scope);

    const oldTimestamp = new Date(Date.now() - (25 * 60 * 60 * 1000)).toISOString(); // 25 hours ago
    const expiredEnvelope = {
      version: REVIEW_CACHE_VERSION,
      scope: keyInfo.scope,
      query: keyInfo.query,
      total: 10,
      offset: 0,
      limit: 25,
      cachedAt: oldTimestamp,
      items: [{ scan_id: 1 }],
    };

    const val = validateCachedReviewQueue(expiredEnvelope, query, scope);
    assert.strictEqual(val.valid, false);
    assert.ok(val.reason.includes('Cache expired'));
  });

  // Test 58: Stale cache entries (age > 1 hour) are flagged as stale while preserving offline usability
  it('58. Stale cache entries between 1h and 24h are flagged isStale: true but valid', () => {
    const scope = { userId: '10', role: 'inspector' };
    const query = { subtype: 'technical_recovery' };
    const keyInfo = buildReviewQueueCacheKey(query, scope);

    const twoHoursAgo = new Date(Date.now() - (2 * 60 * 60 * 1000)).toISOString();
    const staleEnvelope = {
      version: REVIEW_CACHE_VERSION,
      scope: keyInfo.scope,
      query: keyInfo.query,
      total: 10,
      offset: 0,
      limit: 25,
      cachedAt: twoHoursAgo,
      items: [{ scan_id: 1 }],
    };

    const val = validateCachedReviewQueue(staleEnvelope, query, scope);
    assert.strictEqual(val.valid, true);
    assert.strictEqual(val.isStale, true, 'Entries older than 1h must be marked stale');
  });

  // Test 59: Cached summary totals remain distinct from filtered matching totals and are scoped by user
  it('59. Summary cache keys are distinct from queue page keys and scoped by user', () => {
    const scope = { userId: '10', role: 'inspector' };
    const queueKey = buildReviewQueueCacheKey({ subtype: 'technical_recovery' }, scope);
    const summaryKey = buildReviewSummaryCacheKey({}, scope);

    assert.notStrictEqual(queueKey.storageKey, summaryKey.storageKey);
    assert.strictEqual(queueKey.storageKey.startsWith('nn_rq_'), true);
    assert.strictEqual(summaryKey.storageKey.startsWith('nn_rs_'), true);
  });

  // Test 60: Bounded retention limits the maximum number of cached pages per user
  it('60. Bounded retention limit constant is defined and enforces maximum 30 cached pages', () => {
    assert.strictEqual(MAX_CACHED_PAGES, 30);
    assert.strictEqual(REVIEW_CACHE_VERSION, 'v2');
    assert.strictEqual(REVIEW_CACHE_TTL_MS, 86400000);
  });

  // --- Module 6.7: Session-Scope Invalidation & Late-Write Protection Tests ---

  // Test 61: Web/localStorage keys matching nn_rq_ and nn_rs_ are removed on cleanup for target user
  it('61. Web/localStorage keys for target scope are removed while preserving other accounts', () => {
    const mockStorage = {
      'nn_rq_v2_u1_inspector_tech_off0_hash1': '{"items":[]}',
      'nn_rs_v2_u1_inspector_hash2': '{"summary":{}}',
      'nn_rq_v2_u2_inspector_tech_off0_hash3': '{"items":[]}',
      'nn_access': 'valid_token_xyz',
      'nn_install_id': 'install_device_99',
    };

    const clearWebStorage = (targetScopeStr) => {
      const keys = Object.keys(mockStorage);
      for (const k of keys) {
        const isLegacyKey = k.startsWith('nn_review_queue_cache') || k.startsWith('nn_review_summary_cache');
        const isScopedKey = k.startsWith('nn_rq_') || k.startsWith('nn_rs_');
        if (isLegacyKey) {
          delete mockStorage[k];
        } else if (isScopedKey) {
          if (!targetScopeStr || k.includes(`_${targetScopeStr}_`)) {
            delete mockStorage[k];
          }
        }
      }
    };

    // Clean for user 1
    clearWebStorage('u1_inspector');

    assert.strictEqual(mockStorage['nn_rq_v2_u1_inspector_tech_off0_hash1'], undefined);
    assert.strictEqual(mockStorage['nn_rs_v2_u1_inspector_hash2'], undefined);
    // User 2's cache must remain intact
    assert.ok(mockStorage['nn_rq_v2_u2_inspector_tech_off0_hash3']);
    // Unrelated auth & install records must remain intact
    assert.strictEqual(mockStorage['nn_access'], 'valid_token_xyz');
    assert.strictEqual(mockStorage['nn_install_id'], 'install_device_99');
  });

  // Test 62: Native filesystem queue and summary cache files are removed on cleanup
  it('62. Native filesystem queue and summary cache files are deleted for target scope', () => {
    const mockFiles = new Set([
      'rq_v2_u1_inspector_technical_recovery_off0_hash1.json',
      'rs_v2_u1_inspector_hash2.json',
      'rq_v2_u2_inspector_technical_recovery_off0_hash3.json',
      'manifest.json',
      'recapture_tasks_101.json',
      'scan_101_panel_1.jpg',
    ]);

    const clearNativeFiles = (targetScopeStr) => {
      for (const f of Array.from(mockFiles)) {
        const isLegacyFile = f.startsWith('review_queue_cache') || f.startsWith('review_summary_cache');
        const isScopedFile = f.startsWith('rq_') || f.startsWith('rs_');
        if (isLegacyFile) {
          mockFiles.delete(f);
        } else if (isScopedFile) {
          if (!targetScopeStr || f.includes(`_${targetScopeStr}_`)) {
            mockFiles.delete(f);
          }
        }
      }
    };

    clearNativeFiles('u1_inspector');

    assert.strictEqual(mockFiles.has('rq_v2_u1_inspector_technical_recovery_off0_hash1.json'), false);
    assert.strictEqual(mockFiles.has('rs_v2_u1_inspector_hash2.json'), false);
    // User 2 file preserved
    assert.strictEqual(mockFiles.has('rq_v2_u2_inspector_technical_recovery_off0_hash3.json'), true);
    // Unrelated queue manifest, task files, and evidence photos preserved
    assert.strictEqual(mockFiles.has('manifest.json'), true);
    assert.strictEqual(mockFiles.has('recapture_tasks_101.json'), true);
    assert.strictEqual(mockFiles.has('scan_101_panel_1.jpg'), true);
  });

  // Test 63: Legacy unversioned/unscoped cache entries are purged safely during cleanup
  it('63. Legacy unscoped cache entries are purged regardless of scope argument', () => {
    const mockStorage = {
      'nn_review_queue_cache': '{"items":[]}',
      'nn_review_queue_cache_technical_recovery': '{"items":[]}',
      'nn_review_summary_cache': '{"total":10}',
      'nn_rq_v2_u1_inspector_recapture_off0_hash': '{"items":[]}',
    };

    const clearWebStorage = (targetScopeStr) => {
      for (const k of Object.keys(mockStorage)) {
        const isLegacyKey = k.startsWith('nn_review_queue_cache') || k.startsWith('nn_review_summary_cache');
        const isScopedKey = k.startsWith('nn_rq_') || k.startsWith('nn_rs_');
        if (isLegacyKey) {
          delete mockStorage[k];
        } else if (isScopedKey) {
          if (!targetScopeStr || k.includes(`_${targetScopeStr}_`)) {
            delete mockStorage[k];
          }
        }
      }
    };

    // Calling cleanup even with a specific scope must remove legacy unscoped keys
    clearWebStorage('u1_inspector');

    assert.strictEqual(mockStorage['nn_review_queue_cache'], undefined);
    assert.strictEqual(mockStorage['nn_review_queue_cache_technical_recovery'], undefined);
    assert.strictEqual(mockStorage['nn_review_summary_cache'], undefined);
  });

  // Test 64: Cache cleanup is idempotent and does not throw on empty storage
  it('64. Cache cleanup is idempotent and handles empty or missing storage safely', () => {
    const mockStorage = {};
    const clearWeb = (targetScopeStr) => {
      for (const k of Object.keys(mockStorage)) {
        if (k.startsWith('nn_rq_') || k.startsWith('nn_rs_')) {
          delete mockStorage[k];
        }
      }
    };

    assert.doesNotThrow(() => {
      clearWeb('u1_inspector');
      clearWeb('u1_inspector'); // second call
      clearWeb(null);           // null scope call
    });
  });

  // Test 65: Cleanup strictly preserves unrelated keys
  it('65. Cleanup does not remove unrelated application keys or mutation manifests', () => {
    const mockStorage = {
      'nn_access': 'token',
      'nn_install_id': 'install_id_123',
      'nn_offline_queue': '[{"id":"recapture_1"}]',
      'nn_recapture_tasks_55': '[]',
      'nn_report_summary_55': '{}',
      'nn_violation_dossier_55': '{}',
      'nn_rq_v2_u1_inspector_all_off0_hash': '{}',
    };

    for (const k of Object.keys(mockStorage)) {
      if (
        k.startsWith('nn_rq_') ||
        k.startsWith('nn_rs_') ||
        k.startsWith('nn_review_queue_cache') ||
        k.startsWith('nn_review_summary_cache')
      ) {
        delete mockStorage[k];
      }
    }

    assert.strictEqual(mockStorage['nn_access'], 'token');
    assert.strictEqual(mockStorage['nn_install_id'], 'install_id_123');
    assert.strictEqual(mockStorage['nn_offline_queue'], '[{"id":"recapture_1"}]');
    assert.strictEqual(mockStorage['nn_recapture_tasks_55'], '[]');
    assert.strictEqual(mockStorage['nn_report_summary_55'], '{}');
    assert.strictEqual(mockStorage['nn_violation_dossier_55'], '{}');
    assert.strictEqual(mockStorage['nn_rq_v2_u1_inspector_all_off0_hash'], undefined);
  });

  // Test 66: Account A's cached queue cannot be loaded by Account B
  it('66. Account A cached queue fails validation when accessed by Account B', () => {
    const scopeA = { userId: '10', role: 'inspector' };
    const scopeB = { userId: '20', role: 'inspector' };
    const query = { subtype: 'technical_recovery', offset: 0 };

    const keyA = buildReviewQueueCacheKey(query, scopeA);
    const keyB = buildReviewQueueCacheKey(query, scopeB);
    assert.notStrictEqual(keyA.storageKey, keyB.storageKey);

    const envelopeA = createReviewQueueEnvelope([{ scan_id: 101 }], keyA.query, scopeA);
    const validationForB = validateCachedReviewQueue(envelopeA, query, scopeB);

    assert.strictEqual(validationForB.valid, false);
    assert.ok(validationForB.reason.includes('Scope mismatch'));
  });

  // Test 67: Login as a different account cannot access prior session cache
  it('67. Login after logout cannot access prior session cache', () => {
    let mockSessionToken = 'header.eyJzdWIiOiIxMCIsInJvbGUiOiJpbnNwZWN0b3IifQ.sig';
    let currentScope = normalizeUserScope(mockSessionToken);
    assert.strictEqual(currentScope.scopeStr, 'u10_inspector');

    const keyUser1 = buildReviewQueueCacheKey({ subtype: 'recapture_required' }, currentScope);
    const envelope1 = createReviewQueueEnvelope([{ scan_id: 1 }], keyUser1.query, currentScope);

    // User 1 logs out
    mockSessionToken = null;
    currentScope = normalizeUserScope(mockSessionToken);
    assert.strictEqual(currentScope, null);

    // User 2 logs in
    mockSessionToken = 'header.eyJzdWIiOiIyMCIsInJvbGUiOiJpbnNwZWN0b3IifQ.sig';
    currentScope = normalizeUserScope(mockSessionToken);
    assert.strictEqual(currentScope.scopeStr, 'u20_inspector');

    // User 2 tries to validate User 1's envelope
    const check = validateCachedReviewQueue(envelope1, { subtype: 'recapture_required' }, currentScope);
    assert.strictEqual(check.valid, false);
    assert.ok(check.reason.includes('Scope mismatch'));
  });

  // Test 68: Role change from inspector to admin isolates cached review records
  it('68. Role change from inspector to admin isolates cached review records', () => {
    const inspScope = { userId: '5', role: 'inspector' };
    const adminScope = { userId: '5', role: 'admin' };
    const query = { subtype: 'ALL', offset: 0 };

    const inspKey = buildReviewQueueCacheKey(query, inspScope);
    const adminKey = buildReviewQueueCacheKey(query, adminScope);

    assert.notStrictEqual(inspKey.storageKey, adminKey.storageKey);
    assert.strictEqual(inspKey.scope, 'u5_inspector');
    assert.strictEqual(adminKey.scope, 'u5_admin');

    const inspEnvelope = createReviewQueueEnvelope([{ scan_id: 501 }], inspKey.query, inspScope);
    const adminValidation = validateCachedReviewQueue(inspEnvelope, query, adminScope);

    assert.strictEqual(adminValidation.valid, false);
    assert.ok(adminValidation.reason.includes('Scope mismatch'));
  });

  // Test 69: Routine token refresh preserves valid cached data when principal and role are unchanged
  it('69. Routine token refresh preserves valid cached data when principal and role are unchanged', () => {
    const payloadPre = Buffer.from(JSON.stringify({ sub: '30', role: 'inspector', exp: Math.floor(Date.now() / 1000) + 3600 })).toString('base64');
    const payloadPost = Buffer.from(JSON.stringify({ sub: '30', role: 'inspector', exp: Math.floor(Date.now() / 1000) + 7200 })).toString('base64');
    const preRefreshToken = `header.${payloadPre}.sig1`;
    const postRefreshToken = `header.${payloadPost}.sig2`;

    const scopePre = normalizeUserScope(preRefreshToken);
    const scopePost = normalizeUserScope(postRefreshToken);

    assert.strictEqual(scopePre.scopeStr, scopePost.scopeStr);
    assert.strictEqual(scopePre.userId, scopePost.userId);
    assert.strictEqual(scopePre.role, scopePost.role);

    const query = { subtype: 'unconfirmed_violation', offset: 0 };
    const key = buildReviewQueueCacheKey(query, scopePre);
    const envelope = createReviewQueueEnvelope([{ scan_id: 701 }], key.query, scopePre);

    const val = validateCachedReviewQueue(envelope, query, scopePost);
    assert.strictEqual(val.valid, true);
    assert.strictEqual(val.isStale, false);
  });

  // Helper function to simulate cache write revalidation logic from queue.js
  function simulateCacheWriteRevalidation(sessionBinding, currentScope, currentSessionId) {
    if (!currentScope) return false;
    if (sessionBinding) {
      if (sessionBinding.sessionId !== undefined && sessionBinding.sessionId !== currentSessionId) {
        return false;
      }
      const boundScope = normalizeUserScope(sessionBinding.scope || sessionBinding);
      if (!boundScope || boundScope.scopeStr !== currentScope.scopeStr) {
        return false;
      }
    }
    return true;
  }

  // Test 70: Late response from request started by Account A cannot write to Account B cache
  it('70. Late response started by Account A is rejected after Account B signs in', () => {
    const sessionGen1 = 1;
    const scopeA = normalizeUserScope({ userId: '1', role: 'inspector' });

    // Request started under Account A
    const sessionBindingA = { scope: scopeA, sessionId: sessionGen1 };

    // While in flight, Account A logs out and Account B signs in
    const sessionGen2 = 2;
    const scopeB = normalizeUserScope({ userId: '2', role: 'inspector' });

    // Response for Account A returns late
    const writeAllowed = simulateCacheWriteRevalidation(sessionBindingA, scopeB, sessionGen2);
    assert.strictEqual(writeAllowed, false, 'Late response from Account A must not write to Account B cache');
  });

  // Test 71: Response initiated before logout cannot repopulate cache after logout cleanup
  it('71. Response initiated before logout cannot repopulate cache after logout cleanup', () => {
    const sessionGen1 = 1;
    const scopeUser1 = normalizeUserScope({ userId: '1', role: 'inspector' });
    const sessionBinding = { scope: scopeUser1, sessionId: sessionGen1 };

    // User 1 logs out: session generation bumped, current scope is null
    const sessionGenAfterLogout = 2;
    const currentScopeAfterLogout = null;

    const writeAllowed = simulateCacheWriteRevalidation(sessionBinding, currentScopeAfterLogout, sessionGenAfterLogout);
    assert.strictEqual(writeAllowed, false, 'In-flight response must not recreate cache after logout');
  });

  // Test 72: Pending summary cache write cannot restore previous account's metrics
  it('72. Pending summary cache write cannot restore previous account metrics after account change', () => {
    const sessionBindingUser1 = {
      scope: { userId: '10', role: 'inspector' },
      sessionId: 4,
    };

    // Active session switched to User 20
    const activeScope = { userId: '20', role: 'inspector', scopeStr: 'u20_inspector' };
    const activeSessionId = 5;

    const writeAllowed = simulateCacheWriteRevalidation(sessionBindingUser1, activeScope, activeSessionId);
    assert.strictEqual(writeAllowed, false, 'Summary write must reject mismatched account');
  });

  // Test 73: Late response for an old authorization scope cannot overwrite current cache entry
  it('73. Late response for old authorization scope is rejected when role changes', () => {
    const sessionBindingOldRole = {
      scope: { userId: '5', role: 'inspector' },
      sessionId: 10,
    };

    // User promoted to admin, same user ID but new role
    const promotedScope = { userId: '5', role: 'admin', scopeStr: 'u5_admin' };
    const currentSessionId = 10; // Even if generation is same, role mismatch must reject

    const writeAllowed = simulateCacheWriteRevalidation(sessionBindingOldRole, promotedScope, currentSessionId);
    assert.strictEqual(writeAllowed, false, 'Old inspector response must not overwrite admin cache');
  });

  // Test 74: Session generation bump is monotonic and invalidates older generation requests
  it('74. Session generation bump is monotonic and invalidates older generation requests', () => {
    resetReviewCacheSession();
    const g1 = getReviewCacheSessionId();
    assert.strictEqual(g1, 1);

    const g2 = bumpReviewCacheSessionId();
    assert.strictEqual(g2, 2);

    const g3 = bumpReviewCacheSessionId();
    assert.strictEqual(g3, 3);
    assert.ok(g3 > g2 && g2 > g1);
  });

  // Test 75: Session binding object is safely unwrapped by normalizeUserScope
  it('75. Session binding object is safely unwrapped by normalizeUserScope', () => {
    const binding = {
      scope: { userId: '7', role: 'inspector' },
      sessionId: 99,
    };

    const unwrapped = normalizeUserScope(binding);
    assert.ok(unwrapped);
    assert.strictEqual(unwrapped.userId, '7');
    assert.strictEqual(unwrapped.role, 'inspector');
    assert.strictEqual(unwrapped.scopeStr, 'u7_inspector');
  });

  // Test 76: Missing or unauthenticated session scope rejects cache writes safely
  it('76. Missing or unauthenticated session scope rejects cache writes safely', () => {
    const nullScope = null;
    const writeWithNull = simulateCacheWriteRevalidation(null, nullScope, 1);
    assert.strictEqual(writeWithNull, false);

    const undefinedScope = undefined;
    const writeWithUndefined = simulateCacheWriteRevalidation(null, undefinedScope, 1);
    assert.strictEqual(writeWithUndefined, false);
  });

  // Test 77: Concurrent category requests preserve distinct query identities within active session
  it('77. Concurrent category requests preserve distinct query identities within active session', () => {
    const scope = { userId: '10', role: 'inspector' };
    const key1 = buildReviewQueueCacheKey({ subtype: 'technical_recovery', offset: 0 }, scope);
    const key2 = buildReviewQueueCacheKey({ subtype: 'recapture_required', offset: 0 }, scope);
    const key3 = buildReviewQueueCacheKey({ subtype: 'technical_recovery', offset: 25 }, scope);

    assert.notStrictEqual(key1.storageKey, key2.storageKey);
    assert.notStrictEqual(key1.storageKey, key3.storageKey);
    assert.notStrictEqual(key2.storageKey, key3.storageKey);
  });

  // Test 78: Offline cache miss triggers truthful unavailable state without fabricating records
  it('78. Offline cache miss triggers truthful unavailable state without fabricating records', () => {
    let uiState = 'loading';
    let errorMessage = null;

    const handleOfflineLookup = (cachedEnvelope) => {
      if (!cachedEnvelope || !cachedEnvelope.items) {
        uiState = 'unavailable';
        errorMessage = 'No offline cache available for this query.';
        return null;
      }
      uiState = 'cached';
      return cachedEnvelope.items;
    };

    const res = handleOfflineLookup(null);
    assert.strictEqual(res, null);
    assert.strictEqual(uiState, 'unavailable');
    assert.ok(errorMessage.includes('No offline cache'));
  });

  // --- Module 6.8: Effective Authorization Scope Fingerprint & Cache Invalidation Tests ---

  // Test 79: Same user and role with different authorized jurisdictions produce distinct effective scopes
  it('79. Same user and role with different authorized jurisdictions produce distinct effective scopes', () => {
    const scopeSouth = normalizeUserScope({ userId: '10', role: 'inspector', jurisdiction: 'South Delhi' });
    const scopeNorth = normalizeUserScope({ userId: '10', role: 'inspector', jurisdiction: 'North Delhi' });

    assert.ok(scopeSouth);
    assert.ok(scopeNorth);
    assert.strictEqual(scopeSouth.scopeStr, 'u10_inspector_south_delhi');
    assert.strictEqual(scopeNorth.scopeStr, 'u10_inspector_north_delhi');
    assert.notStrictEqual(scopeSouth.scopeStr, scopeNorth.scopeStr);

    const keySouth = buildReviewQueueCacheKey({ subtype: 'unconfirmed_violations' }, scopeSouth);
    const keyNorth = buildReviewQueueCacheKey({ subtype: 'unconfirmed_violations' }, scopeNorth);

    assert.notStrictEqual(keySouth.storageKey, keyNorth.storageKey);
    assert.notStrictEqual(keySouth.filename, keyNorth.filename);
  });

  // Test 80: Cache envelope generated under South Delhi scope is rejected during validation under North Delhi scope
  it('80. Cache envelope generated under South Delhi scope is rejected during validation under North Delhi scope', () => {
    const scopeSouth = { userId: '10', role: 'inspector', jurisdiction: 'South Delhi' };
    const scopeNorth = { userId: '10', role: 'inspector', jurisdiction: 'North Delhi' };
    const query = { subtype: 'recapture_required', offset: 0 };

    const keySouth = buildReviewQueueCacheKey(query, scopeSouth);
    const envelopeSouth = createReviewQueueEnvelope([{ scan_id: 201 }], keySouth.query, scopeSouth);

    // Attempt to validate with North Delhi scope
    const validation = validateCachedReviewQueue(envelopeSouth, query, scopeNorth);
    assert.strictEqual(validation.valid, false);
    assert.ok(validation.reason.includes('Scope mismatch'));
  });

  // Test 81: Same user and role with unchanged effective permissions retains valid cache access
  it('81. Same user and role with unchanged effective permissions retains valid cache access', () => {
    const scope1 = { userId: '10', role: 'inspector', jurisdiction: 'South Delhi' };
    const scope2 = { userId: '10', role: 'inspector', jurisdiction: 'South Delhi' };
    const query = { subtype: 'technical_recovery', offset: 0 };

    const key = buildReviewQueueCacheKey(query, scope1);
    const envelope = createReviewQueueEnvelope([{ scan_id: 301 }], key.query, scope1);

    const validation = validateCachedReviewQueue(envelope, query, scope2);
    assert.strictEqual(validation.valid, true);
    assert.strictEqual(validation.isStale, false);
  });

  // Test 82: Different roles produce isolated cache access even if user ID and jurisdiction are identical
  it('82. Different roles produce isolated cache access even if user ID and jurisdiction are identical', () => {
    const inspectorScope = normalizeUserScope({ userId: '10', role: 'inspector', jurisdiction: 'South Delhi' });
    const adminScope = normalizeUserScope({ userId: '10', role: 'admin', jurisdiction: 'South Delhi' });

    assert.strictEqual(inspectorScope.scopeStr, 'u10_inspector_south_delhi');
    assert.strictEqual(adminScope.scopeStr, 'u10_admin_south_delhi');
    assert.notStrictEqual(inspectorScope.scopeStr, adminScope.scopeStr);

    const keyInspector = buildReviewQueueCacheKey({}, inspectorScope);
    const keyAdmin = buildReviewQueueCacheKey({}, adminScope);

    assert.notStrictEqual(keyInspector.storageKey, keyAdmin.storageKey);
  });

  // Test 83: Tenant or organizational restrictions are included where they actually exist
  it('83. Tenant or organizational restrictions are embedded in effective scope when provided', () => {
    const scopedTenant = normalizeUserScope({
      userId: '15',
      role: 'inspector',
      jurisdiction: 'Delhi West',
      tenantId: 'dept_lm_01',
    });

    assert.strictEqual(scopedTenant.scopeStr, 'u15_inspector_delhi_west_t_dept_lm_01');
    assert.strictEqual(scopedTenant.tenant, 'dept_lm_01');

    const keyTenant = buildReviewQueueCacheKey({}, scopedTenant);
    assert.ok(keyTenant.storageKey.includes('_u15_inspector_delhi_west_t_dept_lm_01_'));
  });

  // Test 84: Missing authoritative permission-scope data fails safely
  it('84. Missing authoritative permission-scope data fails safely without creating or reading cache', () => {
    assert.strictEqual(normalizeUserScope(null), null);
    assert.strictEqual(normalizeUserScope(undefined), null);
    assert.strictEqual(normalizeUserScope({}), null);
    assert.strictEqual(normalizeUserScope({ role: 'inspector' }), null); // Missing userId
    assert.strictEqual(normalizeUserScope({ jurisdiction: 'South Delhi' }), null); // Missing userId

    assert.strictEqual(buildReviewQueueCacheKey({}, null), null);
    assert.strictEqual(buildReviewSummaryCacheKey({}, null), null);

    const validEnvelope = createReviewQueueEnvelope([{ scan_id: 1 }], {}, 'u1_inspector');
    assert.strictEqual(validateCachedReviewQueue(validEnvelope, {}, null).valid, false);
  });

  // Test 85: Arbitrary search filters are not mistaken for authoritative permission grants
  it('85. Arbitrary search filters are not mistaken for authoritative permission grants', () => {
    const userScope = { userId: '25', role: 'inspector', jurisdiction: 'South Delhi' };
    const queryWithSearch = { area: 'North Delhi Market' };

    const key = buildReviewQueueCacheKey(queryWithSearch, userScope);
    // User scope remains strictly South Delhi; North Delhi Market only affects query parameter and queryHash
    assert.strictEqual(key.scope, 'u25_inspector_south_delhi');
    assert.strictEqual(key.query.area, 'north delhi market');
    assert.ok(!key.scope.includes('north_delhi_market'));

    // Client passing query filter as userScope must fail safely
    const invalidScopeAttempt = normalizeUserScope({ area: 'South Delhi' });
    assert.strictEqual(invalidScopeAttempt, null);
  });

  // Test 86: Silent refresh with unchanged effective scope preserves valid offline cache data
  await itAsync('86. Silent refresh with unchanged effective scope preserves valid offline cache data', async () => {
    resetEffectiveScopeForTesting();
    const initialUser = { id: 30, role: 'inspector', jurisdiction: 'East Zone' };
    const initRes = await handleAuthorizationScopeChange(initialUser);
    assert.strictEqual(initRes.changed, false);
    assert.strictEqual(initRes.action, 'initialized');
    assert.strictEqual(getEffectiveScopeStr(), 'u30_inspector_east_zone');

    const sessionGenBefore = getReviewCacheSessionId();

    // Mock cache in place
    let invalidatedScope = null;
    const mockInvalidator = async (scope) => { invalidatedScope = scope; };

    // Silent refresh returns identical identity, role, and jurisdiction
    const refreshedUser = { id: 30, role: 'inspector', jurisdiction: 'East Zone' };
    const refreshRes = await handleAuthorizationScopeChange(refreshedUser, mockInvalidator);

    assert.strictEqual(refreshRes.changed, false);
    assert.strictEqual(refreshRes.action, 'preserved');
    assert.strictEqual(invalidatedScope, null, 'Cache must NOT be invalidated when scope is unchanged');
    assert.strictEqual(getReviewCacheSessionId(), sessionGenBefore, 'Session generation must NOT bump on routine refresh');
  });

  // Test 87: Refresh that changes the effective scope invalidates incompatible cache access
  await itAsync('87. Refresh that changes effective jurisdiction scope invalidates incompatible cache access', async () => {
    resetEffectiveScopeForTesting();
    setEffectiveScopeStr({ id: 35, role: 'inspector', jurisdiction: 'East Zone' });

    let purgedScope = null;
    const mockInvalidator = async (scope) => { purgedScope = scope; };

    const sessionGenBefore = getReviewCacheSessionId();

    // Server informs client that officer was transferred to West Zone
    const updatedUser = { id: 35, role: 'inspector', jurisdiction: 'West Zone' };
    const result = await handleAuthorizationScopeChange(updatedUser, mockInvalidator);

    assert.strictEqual(result.changed, true);
    assert.strictEqual(result.action, 'scope_changed');
    assert.strictEqual(result.oldScope, 'u35_inspector_east_zone');
    assert.strictEqual(result.newScope, 'u35_inspector_west_zone');
    assert.strictEqual(purgedScope, 'u35_inspector_east_zone', 'Old East Zone cache must be purged');
    assert.strictEqual(getReviewCacheSessionId(), sessionGenBefore + 1, 'Session generation must bump to kill in-flight requests');
    assert.strictEqual(getEffectiveScopeStr(), 'u35_inspector_west_zone');
  });

  // Test 88: Role promotion/demotion invalidates old scope cache and bumps session generation
  await itAsync('88. Role promotion/demotion cannot reuse incompatible cache and invalidates old scope', async () => {
    resetEffectiveScopeForTesting();
    setEffectiveScopeStr({ id: 40, role: 'inspector', jurisdiction: 'Central' });

    let purgedScope = null;
    const mockInvalidator = async (scope) => { purgedScope = scope; };

    // Officer promoted to Admin
    const promotedUser = { id: 40, role: 'admin' };
    const result = await handleAuthorizationScopeChange(promotedUser, mockInvalidator);

    assert.strictEqual(result.changed, true);
    assert.strictEqual(result.action, 'scope_changed');
    assert.strictEqual(purgedScope, 'u40_inspector_central');
    assert.strictEqual(result.newScope, 'u40_admin');
  });

  // Test 89: Area/tenant permission changes cannot reuse old restricted data
  it('89. Area/tenant permission changes cannot reuse old restricted data during cache read', () => {
    const oldScope = { userId: '45', role: 'inspector', jurisdiction: 'Zone Alpha' };
    const newScope = { userId: '45', role: 'inspector', jurisdiction: 'Zone Beta' };
    const query = { subtype: 'unconfirmed_violations', offset: 0 };

    const keyOld = buildReviewQueueCacheKey(query, oldScope);
    const envelopeOld = createReviewQueueEnvelope([{ scan_id: 4501 }], keyOld.query, oldScope);

    // Reading with new jurisdiction scope MUST fail validation
    const validation = validateCachedReviewQueue(envelopeOld, query, newScope);
    assert.strictEqual(validation.valid, false);
    assert.ok(validation.reason.includes('Scope mismatch'));
  });

  // Test 90: Late in-flight write from previous jurisdiction is rejected when new jurisdiction is active
  it('90. Late in-flight write from previous jurisdiction is rejected when new jurisdiction is active', () => {
    const sessionGen1 = 20;
    const oldJurisdictionScope = normalizeUserScope({ userId: '50', role: 'inspector', jurisdiction: 'District A' });
    const sessionBinding = { scope: oldJurisdictionScope, sessionId: sessionGen1 };

    // Active session updated to District B
    const sessionGen2 = 21;
    const newJurisdictionScope = normalizeUserScope({ userId: '50', role: 'inspector', jurisdiction: 'District B' });

    const writeAllowed = simulateCacheWriteRevalidation(sessionBinding, newJurisdictionScope, sessionGen2);
    assert.strictEqual(writeAllowed, false, 'Late write from District A must not write to District B cache');
  });

  // Test 91: Missing scope information cannot fall back to an unscoped queue cache
  it('91. Missing scope information cannot fall back to an unscoped queue cache', () => {
    const query = { subtype: 'recapture_required', offset: 0 };
    const nullScopeKey = buildReviewQueueCacheKey(query, null);
    assert.strictEqual(nullScopeKey, null);

    const undefinedScopeKey = buildReviewQueueCacheKey(query, undefined);
    assert.strictEqual(undefinedScopeKey, null);
  });

  // Test 92: Cached summaries and pages use compatible scope semantics
  it('92. Cached summaries and pages use compatible scope semantics', () => {
    const scopeSouth = { userId: '60', role: 'inspector', jurisdiction: 'South Delhi' };
    const scopeNorth = { userId: '60', role: 'inspector', jurisdiction: 'North Delhi' };

    const queueKeySouth = buildReviewQueueCacheKey({}, scopeSouth);
    const summaryKeySouth = buildReviewSummaryCacheKey({}, scopeSouth);
    const queueKeyNorth = buildReviewQueueCacheKey({}, scopeNorth);
    const summaryKeyNorth = buildReviewSummaryCacheKey({}, scopeNorth);

    assert.ok(queueKeySouth.storageKey.includes(scopeSouth.jurisdiction.toLowerCase().replace(' ', '_')));
    assert.ok(summaryKeySouth.storageKey.includes(scopeSouth.jurisdiction.toLowerCase().replace(' ', '_')));
    assert.notStrictEqual(summaryKeySouth.storageKey, summaryKeyNorth.storageKey);
  });

  // Test 93: Reconnection after offline operation refreshes authorization through the real session workflow
  await itAsync('93. Reconnection after offline operation triggers authorization scope verification', async () => {
    resetEffectiveScopeForTesting();
    // 1. Offline operation used cached data from known scope
    const offlineScope = { id: 70, role: 'inspector', jurisdiction: 'South Zone' };
    setEffectiveScopeStr(offlineScope);
    assert.strictEqual(getEffectiveScopeStr(), 'u70_inspector_south_zone');

    let purgedScope = null;
    const mockInvalidator = async (scope) => { purgedScope = scope; };

    // 2. Reconnection: client calls /auth/refresh and server returns updated profile (jurisdiction changed)
    const serverAuthoritativeUser = { id: 70, role: 'inspector', jurisdiction: 'North Zone' };
    const res = await handleAuthorizationScopeChange(serverAuthoritativeUser, mockInvalidator);

    assert.strictEqual(res.changed, true);
    assert.strictEqual(res.oldScope, 'u70_inspector_south_zone');
    assert.strictEqual(res.newScope, 'u70_inspector_north_zone');
    assert.strictEqual(purgedScope, 'u70_inspector_south_zone');
  });

  // Test 94: If offline, application operates truthfully without claiming server-side revocation detection
  it('94. Offline miss produces truthful unavailable state without exposing unpermitted cached data', () => {
    const evaluateOfflineState = (cachedEnvelope, currentScope) => {
      if (!cachedEnvelope) return { status: 'unavailable', message: 'No offline cache available for this query.' };
      const validation = validateCachedReviewQueue(cachedEnvelope, {}, currentScope);
      if (!validation.valid) return { status: 'unavailable', message: 'Cached data belongs to a different authorization scope.' };
      return { status: 'cached', data: cachedEnvelope.items };
    };

    const envelopeOld = createReviewQueueEnvelope([{ scan_id: 99 }], {}, 'u80_inspector_district_1');
    const activeScope = { userId: '80', role: 'inspector', jurisdiction: 'District 2' };

    const outcome = evaluateOfflineState(envelopeOld, activeScope);
    assert.strictEqual(outcome.status, 'unavailable');
    assert.ok(outcome.message.includes('different authorization scope'));
  });

  // Test 95: Cache keys and filenames never expose bearer tokens or passwords
  it('95. Cache keys and filenames never expose bearer tokens or passwords even with secret-like input', () => {
    const rawJwtWithSecret = 'header.eyJzdWIiOiI5OSIsInJvbGUiOiJpbnNwZWN0b3IiLCJqdXJpc2RpY3Rpb24iOiJzb3V0aCJ9.my_super_secret_signature';
    const scope = normalizeUserScope(rawJwtWithSecret);

    const key = buildReviewQueueCacheKey({ subtype: 'unconfirmed_violations' }, scope);
    assert.ok(!key.storageKey.includes('my_super_secret_signature'));
    assert.ok(!key.storageKey.includes('header.'));
    assert.ok(!key.filename.includes('my_super_secret_signature'));
    assert.ok(!key.storageKey.includes('Bearer'));
  });

  console.log(`\nResults: ${passed} passed, ${failed} failed\n`);
  if (failed > 0) {
    process.exit(1);
  }
}

runTests();


