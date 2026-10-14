// api/review.js — Live endpoints for Officer Review Queue & Adjudication (Phase 5A / 6A)
import { api } from './client';

/**
 * Fetch review queue with deterministic backend filtering.
 * @param {Object} params
 * @param {string} [params.status_filter] - OPEN | IN_REVIEW | RESOLVED | ALL
 * @param {string} [params.reason] - not_assessed | low_confidence | offline_edit | conflict | violation | ALL
 * @param {string} [params.priority] - high | medium | low
 * @param {string} [params.area] - City or district filter
 * @param {string} [params.date_from] - YYYY-MM-DD
 * @param {string} [params.date_to] - YYYY-MM-DD
 * @param {number} [params.limit] - Page limit (default 100)
 * @param {number} [params.offset] - Offset (default 0)
 */
export async function fetchReviewQueue(params = {}) {
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
  if (params.area && params.area.trim()) {
    query.area = params.area.trim();
  }
  if (params.date_from) query.date_from = params.date_from;
  if (params.date_to) query.date_to = params.date_to;
  if (params.limit) query.limit = params.limit;
  if (params.offset) query.offset = params.offset;

  const { data } = await api.get('/review/queue', { params: query });
  return data;
}

/**
 * Fetch fast summary metrics for the review queue.
 * @param {Object} [params]
 * @param {string} [params.area]
 * @param {string} [params.status_filter]
 */
export async function fetchReviewSummary(params = {}) {
  const { data } = await api.get('/review/queue/summary', { params });
  return data;
}

/**
 * Fetch comprehensive review details for a specific inspection.
 * @param {number|string} inspectionId
 */
export async function fetchInspectionReviewDetail(inspectionId) {
  const id = Number(inspectionId);
  if (!Number.isInteger(id) || id <= 0) {
    throw new Error(`fetchInspectionReviewDetail: invalid inspection id ${inspectionId}`);
  }
  const { data } = await api.get(`/review/inspections/${id}`);
  return data;
}

/**
 * Adjudicate a review item or individual finding.
 * @param {Object} body
 * @param {number} body.inspection_id
 * @param {'accept'|'override'|'resolve'|'escalate'|'dismiss'|'reassign'} body.action
 * @param {string} body.reason - Mandatory officer explanation (>= 10 chars)
 * @param {string} [body.resolution_notes]
 * @param {number} [body.finding_id]
 * @param {'pass'|'fail'|'not_assessed'} [body.human_verdict]
 */
export async function adjudicateReview(body) {
  if (!body.inspection_id) {
    throw new Error('adjudicateReview: inspection_id is required');
  }
  if (!body.reason || body.reason.trim().length < 10) {
    throw new Error('adjudicateReview: mandatory reason must be at least 10 characters');
  }
  const { data } = await api.post(`/review/inspections/${body.inspection_id}/adjudicate`, body);
  return data;
}

/**
 * Resolve a cross-panel conflict (e.g. MRP front vs back).
 * @param {Object} body
 * @param {number} body.inspection_id
 * @param {string} body.conflict_id
 * @param {'accept_panel_a'|'accept_panel_b'|'manual_value'|'dismiss'} body.resolution
 * @param {string} [body.resolved_value]
 * @param {string} body.reason - Mandatory explanation (>= 10 chars)
 */
export async function resolveReviewConflict(body) {
  if (!body.inspection_id || !body.conflict_id) {
    throw new Error('resolveReviewConflict: inspection_id and conflict_id are required');
  }
  if (!body.reason || body.reason.trim().length < 10) {
    throw new Error('resolveReviewConflict: mandatory reason must be at least 10 characters');
  }
  const { data } = await api.post(
    `/review/inspections/${body.inspection_id}/conflicts/${body.conflict_id}/resolve`,
    body
  );
  return data;
}
