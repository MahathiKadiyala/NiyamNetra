// offline/reviewCacheKey.js — Canonical cache identity & query normalization for Officer Review Queue
// Pure utility module: no React Native / FileSystem dependencies so it can be evaluated anywhere.

export const REVIEW_CACHE_VERSION = 'v2';
export const REVIEW_CACHE_TTL_MS = 24 * 60 * 60 * 1000; // 24 hours bounded retention
export const REVIEW_CACHE_STALE_MS = 60 * 60 * 1000;    // 1 hour staleness marker
export const MAX_CACHED_PAGES = 30;                     // Bounded cache count

/**
 * Deterministic pure-JS 64-bit hash combining FNV-1a and DJB2 algorithms.
 * Produces a stable 16-character hexadecimal string without third-party dependencies.
 * Never stores raw sensitive query/search text in storage keys or filenames.
 */
export function safeHash(str) {
  const input = String(str || '');
  let h1 = 0x811c9dc5;
  let h2 = 5381;
  for (let i = 0; i < input.length; i++) {
    const c = input.charCodeAt(i);
    h1 = Math.imul(h1 ^ c, 16777619) >>> 0;
    h2 = (((h2 << 5) + h2) ^ c) >>> 0;
  }
  return h1.toString(16).padStart(8, '0') + h2.toString(16).padStart(8, '0');
}

/**
 * Canonicalize jurisdiction / district name into a safe, deterministic scope token.
 * e.g. "South Delhi" -> "south_delhi", "  HYDERABAD-NORTH " -> "hyderabad_north"
 * Returns null if empty, missing, or non-string.
 */
export function normalizeJurisdiction(jurisdiction) {
  if (!jurisdiction || typeof jurisdiction !== 'string') return null;
  const cleaned = jurisdiction
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '_')
    .replace(/^_+|_+$/g, '');
  return cleaned || null;
}

/**
 * Decode role and user identity (sub) from unverified JWT access token payload.
 * Hermes + standard base64 fallback.
 * Strictly ignores signature; used solely to extract caller's non-secret user_id & role.
 * Never exposes token secrets or keys.
 */
export function decodeTokenScope(token) {
  try {
    const part = String(token || '').split('.')[1];
    if (!part) return null;
    const b64 = part.replace(/-/g, '+').replace(/_/g, '/');
    const padded = b64 + '='.repeat((4 - (b64.length % 4)) % 4);
    let json;
    if (typeof atob === 'function') {
      json = atob(padded);
    } else {
      const chars = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/=';
      let out = '';
      let i = 0;
      const clean = padded.replace(/[^A-Za-z0-9+/=]/g, '');
      while (i < clean.length) {
        const e1 = chars.indexOf(clean.charAt(i++));
        const e2 = chars.indexOf(clean.charAt(i++));
        const e3 = chars.indexOf(clean.charAt(i++));
        const e4 = chars.indexOf(clean.charAt(i++));
        const n = (e1 << 18) | (e2 << 12) | ((e3 & 63) << 6) | (e4 & 63);
        out += String.fromCharCode((n >> 16) & 255, (n >> 8) & 255, n & 255);
      }
      json = out.replace(/\0+$/, '');
    }
    const payload = JSON.parse(json);
    if (payload?.exp && payload.exp * 1000 < Date.now()) return null;
    if (!payload?.sub) return null;
    return {
      userId: String(payload.sub),
      role: String(payload.role || 'inspector'),
      jurisdiction: payload.jurisdiction ? String(payload.jurisdiction) : null,
    };
  } catch {
    return null;
  }
}

let currentSessionGeneration = 1;

/**
 * Return current review cache session generation identifier.
 * Incremented on logout, session expiration, login, or explicit cache clearance.
 */
export function getReviewCacheSessionId() {
  return currentSessionGeneration;
}

/**
 * Bump review cache session generation identifier.
 * Immediately invalidates any in-flight cache writes from previous session state.
 */
export function bumpReviewCacheSessionId() {
  currentSessionGeneration++;
  return currentSessionGeneration;
}

/**
 * Reset session generation to initial state (for testing).
 */
export function resetReviewCacheSession() {
  currentSessionGeneration = 1;
  return currentSessionGeneration;
}

let currentQueueDataRevision = 1;

/**
 * Return current review cache data revision counter.
 * Monotonically incremented on inspection/adjudication mutations to discard in-flight queries
 * started prior to the mutation.
 */
export function getReviewCacheDataRevision() {
  return currentQueueDataRevision;
}

/**
 * Bump review cache data revision counter.
 * Invalidates in-flight queue queries initiated before this mutation without logging out or modifying auth session.
 */
export function bumpReviewCacheDataRevision() {
  currentQueueDataRevision++;
  return currentQueueDataRevision;
}

/**
 * Reset review cache data revision counter (for testing).
 */
export function resetReviewCacheDataRevision() {
  currentQueueDataRevision = 1;
  return currentQueueDataRevision;
}

const queueInvalidationListeners = new Set();

/**
 * Register a listener invoked whenever the review queue is invalidated due to a mutation.
 * Returns an unregister function.
 */
export function onReviewQueueInvalidated(listener) {
  if (typeof listener === 'function') {
    queueInvalidationListeners.add(listener);
    return () => queueInvalidationListeners.delete(listener);
  }
  return () => {};
}

/**
 * Emit review queue invalidation event to all active listeners.
 */
export function emitReviewQueueInvalidated(event) {
  for (const listener of queueInvalidationListeners) {
    try {
      listener(event);
    } catch (e) {
      console.warn('[reviewCacheKey] error in invalidation listener:', e?.message || e);
    }
  }
}

/**
 * Clear queue invalidation listeners (for testing).
 */
export function clearReviewQueueInvalidationListeners() {
  queueInvalidationListeners.clear();
}

/**
 * Core invalidation logic:
 * Resolves effective scope, bumps data revision, invokes onClearCache callback if provided,
 * and emits invalidation event to registered listeners.
 */
export async function invalidateReviewQueueCore(userScope = null, reason = 'mutation', onClearCache = null) {
  try {
    const currentScope = normalizeUserScope(userScope);
    const scopeStr = currentScope ? currentScope.scopeStr : null;

    const revision = bumpReviewCacheDataRevision();

    if (typeof onClearCache === 'function') {
      try {
        await onClearCache(scopeStr);
      } catch {}
    }

    const event = {
      scope: scopeStr,
      revision,
      reason,
      timestamp: new Date().toISOString(),
    };
    emitReviewQueueInvalidated(event);

    return {
      invalidated: true,
      scope: scopeStr,
      revision,
      reason,
    };
  } catch (err) {
    return {
      invalidated: false,
      error: err?.message || err,
    };
  }
}

/**
 * Standalone invalidation helper (evaluated in tests or pure-JS environments).
 */
export async function invalidateReviewQueue(userScope = null, reason = 'mutation', onClearCache = null) {
  return invalidateReviewQueueCore(userScope, reason, onClearCache);
}

let lastKnownEffectiveScopeStr = null;

/**
 * Return last known effective authorization scope string, or null.
 */
export function getEffectiveScopeStr() {
  return lastKnownEffectiveScopeStr;
}

/**
 * Update the last known effective authorization scope string.
 */
export function setEffectiveScopeStr(scope) {
  const norm = normalizeUserScope(scope);
  lastKnownEffectiveScopeStr = norm ? norm.scopeStr : null;
  return lastKnownEffectiveScopeStr;
}

/**
 * Reset tracked effective scope for testing.
 */
export function resetEffectiveScopeForTesting() {
  lastKnownEffectiveScopeStr = null;
}

/**
 * Pure evaluation of authorization scope transition.
 * Compares previous effective authorization scope with the newly established scope.
 *
 * Scenarios:
 * 1. Initial login / app startup: oldScope is null -> action 'initialized', changed: false.
 * 2. Unchanged scope (silent token refresh, re-auth with same role/jurisdiction):
 *    oldScope === newScope -> action 'preserved', changed: false.
 * 3. Changed scope (promotion, jurisdiction transfer, account switch):
 *    oldScope !== newScope -> action 'scope_changed', changed: true.
 * 4. Logout / session expiry:
 *    newScope is null -> action 'logged_out', changed: true.
 */
export function evaluateAuthorizationScopeChange(newUserOrScope, currentScopeStr = lastKnownEffectiveScopeStr) {
  const norm = normalizeUserScope(newUserOrScope);
  const newScopeStr = norm ? norm.scopeStr : null;
  const oldScopeStr = currentScopeStr;

  if (oldScopeStr === null) {
    return {
      changed: false,
      oldScope: null,
      newScope: newScopeStr,
      action: 'initialized',
    };
  }

  if (oldScopeStr === newScopeStr) {
    return {
      changed: false,
      oldScope: oldScopeStr,
      newScope: newScopeStr,
      action: 'preserved',
    };
  }

  return {
    changed: true,
    oldScope: oldScopeStr,
    newScope: newScopeStr,
    action: newScopeStr ? 'scope_changed' : 'logged_out',
  };
}

/**
 * Execute authorization scope transition:
 * Compares old and new scopes. If changed, bumps session generation to kill in-flight requests
 * and invokes onInvalidateScope callback (if provided) to purge old cache entries.
 */
export async function handleAuthorizationScopeChange(newUserOrScope, onInvalidateScope = null) {
  const evalResult = evaluateAuthorizationScopeChange(newUserOrScope, lastKnownEffectiveScopeStr);

  if (!evalResult.changed) {
    lastKnownEffectiveScopeStr = evalResult.newScope;
    return evalResult;
  }

  // Permission scope changed or session terminated
  bumpReviewCacheSessionId();

  if (typeof onInvalidateScope === 'function') {
    try {
      await onInvalidateScope(evalResult.oldScope);
    } catch {}
  }

  lastKnownEffectiveScopeStr = evalResult.newScope;
  return evalResult;
}

/**
 * Canonicalize and normalize authenticated principal scope.
 * Accepts:
 *   - User profile object: { id/userId, role, jurisdiction, tenantId }
 *   - Session binding: { scope: ... }
 *   - Nested wrapper: { user: ... } or { currentUser: ... }
 *   - Canonical scope string: 'u1_inspector', 'u1_inspector_south_delhi', 'u1_admin'
 *   - Raw JWT string: decodes non-secret sub & role
 *
 * Enforces authoritative authorization dimensions (user identity, role, and authorized jurisdiction/district).
 * Fails safely (returns null) if identity cannot be established.
 * Never treats client query parameters (e.g. area search filter) as an authorization grant.
 */
export function normalizeUserScope(userScope) {
  if (!userScope) return null;

  // Unwrap session binding or wrapper object if passed
  if (typeof userScope === 'object') {
    if (userScope.scope) return normalizeUserScope(userScope.scope);
    if (userScope.user) return normalizeUserScope(userScope.user);
    if (userScope.currentUser) return normalizeUserScope(userScope.currentUser);
  }

  if (typeof userScope === 'string') {
    if (userScope.includes('.')) {
      // JWT token passed
      const decoded = decodeTokenScope(userScope);
      if (!decoded) return null;
      const normJuris = normalizeJurisdiction(decoded.jurisdiction);
      return {
        userId: decoded.userId,
        role: decoded.role,
        jurisdiction: normJuris,
        scopeStr: normJuris ? `u${decoded.userId}_${decoded.role}_${normJuris}` : `u${decoded.userId}_${decoded.role}`,
      };
    }
    const trimmed = userScope.trim();
    if (trimmed.startsWith('u') && trimmed.includes('_')) {
      const parts = trimmed.slice(1).split('_');
      const userId = parts[0];
      const role = parts[1] || 'inspector';
      const jurisdiction = parts.length > 2 ? parts.slice(2).join('_') : null;
      return {
        userId,
        role,
        jurisdiction,
        scopeStr: trimmed,
      };
    }
    const rawId = trimmed.startsWith('u') ? trimmed.slice(1) : trimmed;
    if (!rawId) return null;
    return {
      userId: rawId,
      role: 'inspector',
      jurisdiction: null,
      scopeStr: `u${rawId}_inspector`,
    };
  }

  const userId = String(userScope.userId || userScope.sub || userScope.id || userScope.user_id || '').trim();
  if (!userId) return null;
  const role = String(userScope.role || 'inspector').trim().toLowerCase();
  const rawJurisdiction = userScope.jurisdiction || userScope.authorizedArea || userScope.area_scope || userScope.authorized_area || null;
  const jurisdiction = normalizeJurisdiction(rawJurisdiction);

  const rawTenant = userScope.tenantId || userScope.tenant || userScope.tenant_id || userScope.orgId || userScope.organization || null;
  const tenant = normalizeJurisdiction(rawTenant);

  let scopeStr = `u${userId}_${role}`;
  if (jurisdiction) {
    scopeStr += `_${jurisdiction}`;
  }
  if (tenant) {
    scopeStr += `_t_${tenant}`;
  }

  return {
    userId,
    role,
    jurisdiction,
    tenant: tenant || null,
    scopeStr,
  };
}

/**
 * Canonicalize review queue query parameters according to the backend API contract.
 * Maps semantically equivalent query aliases to authoritative canonical representations.
 */
export function normalizeReviewQueryParams(params = {}) {
  // Support both object params and legacy filterKey string
  const p = typeof params === 'string' ? { subtype: params } : (params || {});

  // 1. Subtype normalization (including documented compatible aliases)
  const rawSubtype = p.subtype || p.subtype_filter;
  let normSubtype = 'ALL';
  if (rawSubtype && String(rawSubtype).trim().toLowerCase() !== 'all') {
    const st = String(rawSubtype).trim().toLowerCase();
    if (st === 'unconfirmed_violations' || st === 'unconfirmed_violation') {
      normSubtype = 'unconfirmed_violation';
    } else if (st === 'confirmed_violations' || st === 'confirmed_violation') {
      normSubtype = 'confirmed_violation';
    } else if (st === 'recapture_required' || st === 'incomplete_coverage') {
      normSubtype = 'recapture_required';
    } else if (st === 'offline_edits' || st === 'offline_edit') {
      normSubtype = 'offline_edit';
    } else {
      normSubtype = st;
    }
  }

  // 2. Status filter normalization
  let normStatus = 'ALL';
  if (p.status_filter && String(p.status_filter).trim().toUpperCase() !== 'ALL') {
    normStatus = String(p.status_filter).trim().toUpperCase();
  }

  // 3. Reason filter normalization
  let normReason = 'ALL';
  if (p.reason && String(p.reason).trim().toLowerCase() !== 'all') {
    normReason = String(p.reason).trim().toLowerCase();
  }

  // 4. Area / search text normalization
  const normArea = p.area && typeof p.area === 'string' ? p.area.trim().toLowerCase() : '';

  // 5. Pagination: limit & offset
  const normLimit = Number.isFinite(Number(p.limit)) && Number(p.limit) > 0 ? Number(p.limit) : 25;
  const normOffset = Number.isFinite(Number(p.offset)) && Number(p.offset) >= 0 ? Number(p.offset) : 0;

  // 6. Priority
  let normPriority = 'ALL';
  if (p.priority && String(p.priority).trim().toLowerCase() !== 'all') {
    normPriority = String(p.priority).trim().toLowerCase();
  }

  // 7. Date bounds
  const normDateFrom = p.date_from && typeof p.date_from === 'string' ? p.date_from.trim() : '';
  const normDateTo = p.date_to && typeof p.date_to === 'string' ? p.date_to.trim() : '';

  return {
    subtype: normSubtype,
    status_filter: normStatus,
    reason: normReason,
    area: normArea,
    limit: normLimit,
    offset: normOffset,
    priority: normPriority,
    date_from: normDateFrom,
    date_to: normDateTo,
  };
}

/**
 * Build canonical cache identity for a review queue query page.
 * Returns null if the authenticated scope cannot be established safely.
 */
export function buildReviewQueueCacheKey(params = {}, userScope = null) {
  const scope = normalizeUserScope(userScope);
  if (!scope) return null;

  const q = normalizeReviewQueryParams(params);

  // Canonical query string for deterministic hashing
  const canonicalQueryStr = [
    `area=${q.area}`,
    `date_from=${q.date_from}`,
    `date_to=${q.date_to}`,
    `limit=${q.limit}`,
    `offset=${q.offset}`,
    `priority=${q.priority}`,
    `reason=${q.reason}`,
    `status=${q.status_filter}`,
    `subtype=${q.subtype}`,
  ].join('&');

  const queryHash = safeHash(canonicalQueryStr);
  const descriptor = `${q.subtype}_off${q.offset}`;

  // Key format: nn_rq_v2_<scope>_<subtype_offset>_<hash>
  const storageKey = `nn_rq_${REVIEW_CACHE_VERSION}_${scope.scopeStr}_${descriptor}_${queryHash}`;
  const filename = `rq_${REVIEW_CACHE_VERSION}_${scope.scopeStr}_${descriptor}_${queryHash}.json`;

  return {
    version: REVIEW_CACHE_VERSION,
    scope: scope.scopeStr,
    descriptor,
    storageKey,
    filename,
    query: q,
    queryHash,
  };
}

/**
 * Build canonical cache identity for review queue summary metrics.
 */
export function buildReviewSummaryCacheKey(params = {}, userScope = null) {
  const scope = normalizeUserScope(userScope);
  if (!scope) return null;

  const normArea = params.area && typeof params.area === 'string' ? params.area.trim().toLowerCase() : '';
  const normStatus = params.status_filter && String(params.status_filter).trim().toUpperCase() !== 'ALL'
    ? String(params.status_filter).trim().toUpperCase()
    : 'ALL';

  const canonical = `area=${normArea}&status=${normStatus}`;
  const hash = safeHash(canonical);

  const storageKey = `nn_rs_${REVIEW_CACHE_VERSION}_${scope.scopeStr}_${hash}`;
  const filename = `rs_${REVIEW_CACHE_VERSION}_${scope.scopeStr}_${hash}.json`;

  return {
    version: REVIEW_CACHE_VERSION,
    scope: scope.scopeStr,
    storageKey,
    filename,
    params: { area: normArea, status_filter: normStatus },
    hash,
  };
}

/**
 * Create a structured cache envelope containing metadata, authoritative counts, and scans.
 */
export function createReviewQueueEnvelope(payload, normalizedQuery, userScope) {
  const rawItems = Array.isArray(payload) ? payload : payload?.items || [];
  const total = typeof payload?.total === 'number' ? payload.total : rawItems.length;
  const offset = typeof payload?.offset === 'number' ? payload.offset : normalizedQuery?.offset ?? 0;
  const limit = typeof payload?.limit === 'number' ? payload.limit : normalizedQuery?.limit ?? 25;
  const scopeObj = normalizeUserScope(userScope);
  const scopeStr = scopeObj ? scopeObj.scopeStr : String(userScope || '');

  return {
    version: REVIEW_CACHE_VERSION,
    scope: scopeStr,
    query: normalizedQuery,
    total,
    offset,
    limit,
    cachedAt: new Date().toISOString(),
    items: rawItems,
  };
}

/**
 * Validate that a cached envelope satisfies the requested query and user scope.
 */
export function validateCachedReviewQueue(envelope, expectedParams = {}, expectedScope = null) {
  if (!envelope || typeof envelope !== 'object') {
    return { valid: false, isStale: false, reason: 'Invalid or empty envelope' };
  }

  // Schema version check
  if (envelope.version !== REVIEW_CACHE_VERSION) {
    return { valid: false, isStale: false, reason: `Incompatible schema version: ${envelope.version}` };
  }

  // Scope check
  const scope = normalizeUserScope(expectedScope);
  if (!scope || envelope.scope !== scope.scopeStr) {
    return { valid: false, isStale: false, reason: `Scope mismatch: expected ${scope?.scopeStr}, found ${envelope.scope}` };
  }

  // Query parameter check
  const expQuery = normalizeReviewQueryParams(expectedParams);
  const curQuery = envelope.query;

  if (!curQuery) {
    return { valid: false, isStale: false, reason: 'Missing cached query metadata' };
  }

  if (curQuery.subtype !== expQuery.subtype) {
    return { valid: false, isStale: false, reason: `Subtype mismatch: expected ${expQuery.subtype}, cached ${curQuery.subtype}` };
  }
  if (curQuery.offset !== expQuery.offset) {
    return { valid: false, isStale: false, reason: `Offset mismatch: expected ${expQuery.offset}, cached ${curQuery.offset}` };
  }
  if (curQuery.limit !== expQuery.limit) {
    return { valid: false, isStale: false, reason: `Limit mismatch: expected ${expQuery.limit}, cached ${curQuery.limit}` };
  }
  if (curQuery.area !== expQuery.area) {
    return { valid: false, isStale: false, reason: `Area mismatch: expected ${expQuery.area}, cached ${curQuery.area}` };
  }
  if (curQuery.status_filter !== expQuery.status_filter) {
    return { valid: false, isStale: false, reason: `Status mismatch: expected ${expQuery.status_filter}, cached ${curQuery.status_filter}` };
  }
  if (curQuery.reason !== expQuery.reason) {
    return { valid: false, isStale: false, reason: `Reason mismatch: expected ${expQuery.reason}, cached ${curQuery.reason}` };
  }

  // TTL expiration check
  const cachedTime = Date.parse(envelope.cachedAt);
  if (isNaN(cachedTime)) {
    return { valid: false, isStale: false, reason: 'Invalid cached timestamp' };
  }

  const ageMs = Date.now() - cachedTime;
  if (ageMs > REVIEW_CACHE_TTL_MS) {
    return { valid: false, isStale: true, reason: `Cache expired (age ${Math.round(ageMs / 1000)}s > TTL ${Math.round(REVIEW_CACHE_TTL_MS / 1000)}s)` };
  }

  const isStale = ageMs > REVIEW_CACHE_STALE_MS;

  return {
    valid: true,
    isStale,
    ageMs,
  };
}
