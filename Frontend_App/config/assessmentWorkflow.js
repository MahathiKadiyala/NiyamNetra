// Frontend_App/config/assessmentWorkflow.js
// Authoritative state derivation, finding formatting, and evidence fingerprinting for mobile assessment.

export const ASSESSMENT_STATE = {
  INITIAL_LOADING: 'initial_loading',
  ASSESSING: 'assessing',
  COMPLIANT: 'compliant',
  VIOLATION: 'violation',
  TECHNICAL_FAILURE: 'technical_failure',
  RECAPTURE_REQUIRED: 'recapture_required',
  OFFICER_REVIEW: 'officer_review',
  AWAITING_ASSESSMENT: 'awaiting_assessment',
  REQUEST_FAILURE: 'request_failure',
  UNAVAILABLE: 'unavailable',
};

/**
 * Computes a deterministic evidence fingerprint from panel evidence or images.
 * Changes to evidence invalidate previous assessments and permit fresh reassessment.
 */
export function computeEvidenceFingerprint(evidence) {
  if (!evidence) return '';
  if (Array.isArray(evidence)) {
    return evidence
      .map((im) => `${im.panel || 'panel'}:${im.sha256 || im.id || im.url || ''}`)
      .sort()
      .join(';');
  }
  if (typeof evidence === 'object') {
    return Object.entries(evidence)
      .filter(([_, v]) => Boolean(v))
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([k, v]) => `${k}:${typeof v === 'object' ? (v.sha256 || v.uri || JSON.stringify(v)) : String(v)}`)
      .join(';');
  }
  return String(evidence);
}

/**
 * Derives the honest runtime assessment state from backend responses and client status.
 */
export function deriveAssessmentState({
  currentScan,
  findings,
  reassessing = false,
  fetchLoading = false,
  requestError = null,
}) {
  if (reassessing) {
    return ASSESSMENT_STATE.ASSESSING;
  }
  if (requestError) {
    return ASSESSMENT_STATE.REQUEST_FAILURE;
  }
  if (fetchLoading && (!findings || findings.length === 0)) {
    return ASSESSMENT_STATE.INITIAL_LOADING;
  }
  if (!currentScan && (!findings || findings.length === 0)) {
    return ASSESSMENT_STATE.UNAVAILABLE;
  }

  const rawOverall = currentScan?.overall_result;
  const ocrStatus = currentScan?.diagnostics?.ocr_status;

  // Check if findings or diagnostics indicate OCR or provider technical failure
  const hasOcrOrProviderError = (Array.isArray(findings) && findings.some((f) => {
    if (f.effective_verdict !== 'not_assessed' && f.engine_verdict !== 'not_assessed') return false;
    const r = (f.reason || '').toLowerCase();
    return r.includes('ocr') || r.includes('provider') || r.includes('timeout') || r.includes('technical') || r.includes('failed to initialize') || r.includes('no text was recognised');
  })) || ocrStatus === 'error' || (ocrStatus === 'no_text' && rawOverall === 'not_assessed');

  // Check if unreadable / incomplete evidence / recapture required
  const hasRecaptureRequired = (Array.isArray(findings) && findings.some((f) => {
    if (f.effective_verdict !== 'not_assessed' && f.engine_verdict !== 'not_assessed') return false;
    const r = (f.reason || '').toLowerCase();
    return r.includes('recapture') || r.includes('unreadable') || r.includes('blur') || r.includes('glare') || r.includes('cannot be computed');
  })) || (rawOverall === 'not_assessed' && !hasOcrOrProviderError && (currentScan?.checks_assessed === 0 && currentScan?.status === 'assessed'));

  const hasServerAssessed = Boolean(
    currentScan?.status === 'assessed' ||
    (rawOverall && rawOverall !== 'not_assessed') ||
    (currentScan?.checks_assessed && currentScan?.checks_assessed > 0) ||
    (Array.isArray(findings) && findings.some((f) => f.engine_verdict && f.engine_verdict !== 'not_assessed'))
  );

  if (!hasServerAssessed) {
    return ASSESSMENT_STATE.AWAITING_ASSESSMENT;
  }

  // 1. Technical OCR / provider failure
  if (rawOverall === 'not_assessed' && hasOcrOrProviderError) {
    return ASSESSMENT_STATE.TECHNICAL_FAILURE;
  }

  // 2. Recapture required
  if (rawOverall === 'not_assessed' && hasRecaptureRequired) {
    return ASSESSMENT_STATE.RECAPTURE_REQUIRED;
  }

  // 3. Officer review required
  if (
    rawOverall === 'review_required' ||
    (Array.isArray(findings) && findings.some((f) => f.effective_verdict === 'review_required' || f.human_verdict === 'review_required'))
  ) {
    return ASSESSMENT_STATE.OFFICER_REVIEW;
  }

  // 4. Completed violation
  if (rawOverall === 'violation' || (Array.isArray(findings) && findings.some((f) => f.effective_verdict === 'fail'))) {
    return ASSESSMENT_STATE.VIOLATION;
  }

  // 5. Completed compliant
  if (rawOverall === 'compliant' || (rawOverall !== 'violation' && Array.isArray(findings) && findings.length > 0 && findings.every((f) => f.effective_verdict === 'pass' || f.effective_verdict === 'not_applicable'))) {
    return ASSESSMENT_STATE.COMPLIANT;
  }

  if (rawOverall === 'not_assessed') {
    return ASSESSMENT_STATE.RECAPTURE_REQUIRED;
  }

  return ASSESSMENT_STATE.COMPLIANT;
}

/**
 * Normalizes an individual statutory finding.
 * Preserves null/unavailable fields without fabricating false positive declarations.
 */
export function formatFinding(f, idx = 0) {
  if (!f) return null;
  const srvId = typeof f.id === 'number' && f.id > 0 ? f.id : (typeof f.server_id === 'number' && f.server_id > 0 ? f.server_id : null);
  const checkId = f.check_id || f.code || f.rule_id || `CHK${String(idx + 1).padStart(2, '0')}`;
  const effective = f.effective_verdict || f.human_verdict || f.engine_verdict || f.verdict || f.finding_status || 'not_assessed';
  const engine = f.engine_verdict || f.verdict || 'not_assessed';
  const isFail = effective === 'fail' || effective === 'violation';

  let defaultObserved = 'No declaration observed on packaging';
  if (effective === 'not_assessed') {
    defaultObserved = 'Pending extraction from packaging evidence';
  }

  return {
    id: f.id || idx + 1,
    server_id: srvId,
    check_id: checkId,
    rule_id: f.rule_id || checkId,
    title: f.title || f.name || f.rule_title || checkId,
    citation: f.citation || f.statutory_reference || f.source_rule || 'Legal Metrology (Packaged Commodities) Rules, 2011',
    engine_verdict: engine,
    effective_verdict: effective,
    human_verdict: f.human_verdict || null,
    override_reason: f.override_reason || null,
    observed: (f.observed !== undefined && f.observed !== null) ? f.observed : defaultObserved,
    required: f.required || 'Statutory declaration required under Legal Metrology Rules',
    severity: f.severity || 'critical',
    reason: f.reason || f.explanation || null,
    remediation: f.remediation || (isFail ? 'Rectify packaging declaration to comply with statutory rule requirements' : null),
    confidence: f.confidence,
    evidence_references: f.evidence_references || (f.check_id ? [f.check_id] : []),
  };
}

export function formatFindings(rawFindings) {
  if (!Array.isArray(rawFindings) || rawFindings.length === 0) {
    return [];
  }
  return rawFindings.map((f, idx) => formatFinding(f, idx)).filter(Boolean);
}
