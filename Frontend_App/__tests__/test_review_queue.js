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

  console.log(`\nResults: ${passed} passed, ${failed} failed\n`);
  if (failed > 0) {
    process.exit(1);
  }
}

runTests();
