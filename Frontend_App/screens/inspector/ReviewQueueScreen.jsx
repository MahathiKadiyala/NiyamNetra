// screens/inspector/ReviewQueueScreen.jsx — Phase 6A / 6.3: Authoritative Inspector Review Queue
import React, { useCallback, useEffect, useState } from 'react';
import {
  View,
  Text,
  ScrollView,
  Pressable,
  RefreshControl,
  ActivityIndicator,
  TextInput,
  StyleSheet,
} from 'react-native';
import { colors, spacing, typography, radius, shadows } from '../../theme';
import Header from '../../components/Header';
import Card from '../../components/Card';
import EmptyState from '../../components/EmptyState';
import PrimaryButton from '../../components/PrimaryButton';
import { fetchReviewQueue, fetchReviewSummary } from '../../api/review';
import { getAccessToken } from '../../api/client';
import {
  cacheReviewQueue,
  loadCachedReviewQueue,
  cacheReviewSummary,
  loadCachedReviewSummary,
  getReviewCacheSessionId,
  normalizeUserScope,
} from '../../offline/queue';

const STATUS_FILTERS = [
  { id: 'ALL', label: 'All Status' },
  { id: 'OPEN', label: 'Open' },
  { id: 'IN_REVIEW', label: 'In Review' },
  { id: 'RESOLVED', label: 'Resolved' },
];

const REASON_FILTERS = [
  { id: 'ALL', label: 'All Items' },
  { id: 'violation', label: 'Violations' },
  { id: 'not_assessed', label: 'Incomplete / Tech' },
  { id: 'low_confidence', label: 'Low Conf' },
  { id: 'offline_edit', label: 'Offline Edits' },
];

const PAGE_SIZE = 25;

export default function ReviewQueueScreen({ onSelectInspection, onBack }) {
  const [items, setItems] = useState([]);
  const [summary, setSummary] = useState(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [statusFilter, setStatusFilter] = useState('ALL');
  const [reasonFilter, setReasonFilter] = useState('ALL');
  const [facetFilter, setFacetFilter] = useState('ALL'); // 'ALL' | 'unconfirmed_violations' | 'confirmed_violations' | 'technical_recovery' | 'recapture_required' | 'low_confidence'
  const [offset, setOffset] = useState(0);
  const [totalMatching, setTotalMatching] = useState(0);
  const [areaSearch, setAreaSearch] = useState('');
  const [isOffline, setIsOffline] = useState(false);
  const [loadError, setLoadError] = useState(null);

  const activeRequestIdRef = React.useRef(0);

  const loadData = useCallback(async (isRefresh = false) => {
    const requestId = ++activeRequestIdRef.current;
    if (isRefresh) setRefreshing(true);
    else setLoading(true);
    setLoadError(null);

    const filterKey = facetFilter !== 'ALL' ? facetFilter : 'ALL';

    // Capture active authenticated scope and session generation at request start
    const requestScope = normalizeUserScope(getAccessToken());
    const requestSessionId = getReviewCacheSessionId();

    try {
      const queueParams = {
        limit: PAGE_SIZE,
        offset,
      };
      if (statusFilter !== 'ALL') {
        queueParams.status_filter = statusFilter;
      }
      if (reasonFilter !== 'ALL') {
        queueParams.reason = reasonFilter;
      }
      if (facetFilter !== 'ALL') {
        queueParams.subtype = facetFilter;
      }
      if (areaSearch && areaSearch.trim()) {
        queueParams.area = areaSearch.trim();
      }

      const [queueData, summaryData] = await Promise.all([
        fetchReviewQueue(queueParams),
        fetchReviewSummary(),
      ]);

      // Late response from an earlier category or offset request must not overwrite active state
      if (requestId !== activeRequestIdRef.current) {
        return;
      }

      const rawItems = Array.isArray(queueData) ? queueData : queueData?.items || [];
      const matchingCount = typeof queueData?.total === 'number' ? queueData.total : rawItems.length;

      // Deduplicate scans by scan_id to ensure stable scan identity
      const seenScans = new Set();
      const uniqueScans = [];
      for (const item of rawItems) {
        const key = item.scan_id || `${item.inspection_id}-${item.id}`;
        if (!seenScans.has(key)) {
          seenScans.add(key);
          uniqueScans.push(item);
        }
      }

      setItems(uniqueScans);
      setTotalMatching(matchingCount);
      setSummary(summaryData);
      setIsOffline(false);
      setLoadError(null);

      // Persist to cache, binding write to the session scope and generation of the initiating request
      const sessionBinding = {
        scope: requestScope,
        sessionId: requestSessionId,
      };

      await Promise.all([
        cacheReviewQueue({
          items: uniqueScans,
          total: matchingCount,
          offset,
          limit: PAGE_SIZE,
        }, queueParams, sessionBinding),
        cacheReviewSummary(summaryData, queueParams, sessionBinding),
      ]);
    } catch (err) {
      if (requestId !== activeRequestIdRef.current) {
        return;
      }

      const status = err?.status || err?.response?.status;
      let errorType = 'network';
      let message = err?.message || 'Unable to retrieve review queue from server.';

      if (status === 401) {
        errorType = 'auth';
        message = 'Authentication required. Session expired or missing.';
      } else if (status === 403 || err?.code === 'FORBIDDEN') {
        errorType = 'forbidden';
        message = 'Access restricted: officer review queue requires inspector privileges.';
      } else if (status === 422) {
        errorType = 'validation';
        const detail = err?.response?.data?.detail;
        message = typeof detail === 'string' ? detail : 'Invalid filter parameters specified.';
      }

      // Offline fallback: load cached review items for this EXACT query and cached summary
      // NEVER fall back to cache on validation (422) or auth (401/403) errors.
      if (errorType === 'network') {
        const [cachedData, cachedSummary] = await Promise.all([
          loadCachedReviewQueue(queueParams, requestScope),
          loadCachedReviewSummary(queueParams, requestScope),
        ]);

        const cachedItems = Array.isArray(cachedData) ? cachedData : cachedData?.items;
        if (cachedItems && cachedItems.length > 0) {
          const cachedTotal = typeof cachedData?.total === 'number' ? cachedData.total : cachedItems.length;
          setItems(cachedItems);
          setTotalMatching(cachedTotal);
          setSummary(cachedSummary?.summary || cachedSummary);
          setIsOffline(true);
          setLoadError(null);
          return;
        }
      }

      // Explicit error state: do NOT show false empty queue or unfiltered cache
      setItems([]);
      setTotalMatching(0);
      setIsOffline(false);
      setLoadError({
        type: errorType,
        status,
        message,
      });
    } finally {
      if (requestId === activeRequestIdRef.current) {
        setLoading(false);
        setRefreshing(false);
      }
    }
  }, [statusFilter, reasonFilter, facetFilter, areaSearch, offset]);

  useEffect(() => {
    loadData();
  }, [loadData]);

  const handleSelectFacet = (targetFacet) => {
    const nextFacet = facetFilter === targetFacet ? 'ALL' : targetFacet;
    setFacetFilter(nextFacet);
    setOffset(0);
  };

  const handleSelectStatus = (newStatus) => {
    setStatusFilter(newStatus);
    setOffset(0);
  };

  const handleSelectReason = (newReason) => {
    setReasonFilter(newReason);
    setOffset(0);
  };

  const getPriorityStyle = (priority) => {
    switch (String(priority).toLowerCase()) {
      case 'critical':
      case 'high':
        return { bg: '#FEE2E2', border: '#FCA5A5', text: '#B91C1C' };
      case 'medium':
        return { bg: '#FEF3C7', border: '#FCD34D', text: '#B45309' };
      default:
        return { bg: '#F1F5F9', border: '#CBD5E1', text: '#475569' };
    }
  };

  const getStatusBadgeStyle = (status) => {
    switch (String(status).toUpperCase()) {
      case 'RESOLVED':
        return { bg: '#ECFDF5', border: '#A7F3D0', text: '#047857' };
      case 'IN_REVIEW':
        return { bg: '#EFF6FF', border: '#BFDBFE', text: '#1D4ED8' };
      default:
        return { bg: '#FFFBEB', border: '#FDE68A', text: '#B45309' };
    }
  };

  const getCategoryBadge = (item) => {
    const reasons = item.reasons || item.review_reasons || [];
    if (reasons.some((r) => r.subtype === 'technical_recovery')) {
      return {
        label: '⚙️ TECHNICAL RECOVERY',
        bg: '#EDE9FE',
        border: '#DDD6FE',
        text: '#6D28D9',
        type: 'technical_recovery',
      };
    }
    if (
      reasons.some(
        (r) =>
          r.subtype === 'incomplete_coverage' ||
          r.detail?.toLowerCase().includes('recapture') ||
          r.detail?.toLowerCase().includes('missing') ||
          r.detail?.toLowerCase().includes('blur')
      )
    ) {
      return {
        label: '📷 RECAPTURE REQUIRED',
        bg: '#FEF3C7',
        border: '#FDE68A',
        text: '#B45309',
        type: 'recapture_required',
      };
    }
    if (reasons.some((r) => r.subtype === 'confirmed_violation')) {
      return {
        label: '🚨 CONFIRMED VIOLATION',
        bg: '#FEE2E2',
        border: '#FCA5A5',
        text: '#B91C1C',
        type: 'confirmed_violations',
      };
    }
    if (reasons.some((r) => r.subtype === 'unconfirmed_violation')) {
      return {
        label: '⚠️ PENDING ADJUDICATION',
        bg: '#FFFBEB',
        border: '#FCD34D',
        text: '#D97706',
        type: 'unconfirmed_violations',
      };
    }
    if (reasons.some((r) => r.type === 'low_confidence')) {
      return {
        label: '🔍 LOW CONFIDENCE',
        bg: '#F1F5F9',
        border: '#CBD5E1',
        text: '#475569',
        type: 'low_confidence',
      };
    }
    if (reasons.some((r) => r.type === 'offline_edit')) {
      return {
        label: '⚡ OFFLINE EDIT',
        bg: '#FFF7ED',
        border: '#FED7AA',
        text: '#C2410C',
        type: 'offline_edit',
      };
    }
    return {
      label: '📋 REVIEW REQUIRED',
      bg: '#EFF6FF',
      border: '#BFDBFE',
      text: '#1D4ED8',
      type: 'general',
    };
  };

  // Authoritative server-filtered items directly displayed
  const displayedItems = items;

  return (
    <View style={styles.container}>
      <Header
        title="Officer Review Queue"
        subtitle="Adjudication of flagged packages & incomplete evidence"
        leftAction={onBack ? { icon: '←', onPress: onBack, label: 'Back' } : null}
      />

      {isOffline && (
        <View style={styles.offlineBanner}>
          <Text style={styles.offlineText}>
            ⚡ Offline mode: showing cached {facetFilter !== 'ALL' ? `"${facetFilter.replace(/_/g, ' ')}" ` : ''}data. Live synchronization pending.
          </Text>
        </View>
      )}

      {/* Drafts Separation Banner */}
      {summary?.breakdown?.draft_inspections > 0 && (
        <View style={styles.draftNoticeBanner}>
          <Text style={styles.draftNoticeText}>
            📝 {summary.breakdown.draft_inspections} active draft visit(s) in progress • Drafts remain in inspector field flow and are excluded from the officer review queue.
          </Text>
        </View>
      )}

      {/* Authoritative Category Summary Cards */}
      {summary && (
        <ScrollView
          horizontal
          showsHorizontalScrollIndicator={false}
          contentContainerStyle={styles.summaryScroll}
        >
          {/* 1. Authoritative Backend Total */}
          <Pressable onPress={() => handleSelectFacet('ALL')}>
            <Card
              padding="sm"
              style={[
                styles.summaryCard,
                facetFilter === 'ALL' && styles.summaryCardSelected,
              ]}
            >
              <Text style={styles.summaryLabel}>Total Actionable</Text>
              <Text style={[styles.summaryValue, { color: colors.niyamBlue }]}>
                {summary.total ?? 0}
              </Text>
              <Text style={styles.summaryFacetDesc}>Scans in queue</Text>
            </Card>
          </Pressable>

          {/* 2. Unconfirmed Violations (Pending Officer Review) */}
          <Pressable onPress={() => handleSelectFacet('unconfirmed_violations')}>
            <Card
              padding="sm"
              style={[
                styles.summaryCard,
                facetFilter === 'unconfirmed_violations' && styles.summaryCardSelected,
              ]}
            >
              <Text style={styles.summaryLabel}>Pending Review</Text>
              <Text style={[styles.summaryValue, { color: '#D97706' }]}>
                {summary.breakdown?.unconfirmed_violations ?? summary.violations_pending ?? 0}
              </Text>
              <Text style={styles.summaryFacetDesc}>Unconfirmed</Text>
            </Card>
          </Pressable>

          {/* 3. Confirmed Violations (Awaiting Enforcement Action) */}
          <Pressable onPress={() => handleSelectFacet('confirmed_violations')}>
            <Card
              padding="sm"
              style={[
                styles.summaryCard,
                facetFilter === 'confirmed_violations' && styles.summaryCardSelected,
              ]}
            >
              <Text style={styles.summaryLabel}>Confirmed Action</Text>
              <Text style={[styles.summaryValue, { color: colors.violation.text }]}>
                {summary.breakdown?.confirmed_violations ?? 0}
              </Text>
              <Text style={styles.summaryFacetDesc}>Section 36</Text>
            </Card>
          </Pressable>

          {/* 4. Technical Recovery (OCR / Service Failure - Not a Legal Violation) */}
          <Pressable onPress={() => handleSelectFacet('technical_recovery')}>
            <Card
              padding="sm"
              style={[
                styles.summaryCard,
                facetFilter === 'technical_recovery' && styles.summaryCardSelected,
              ]}
            >
              <Text style={styles.summaryLabel}>Tech Recovery</Text>
              <Text style={[styles.summaryValue, { color: '#6D28D9' }]}>
                {summary.breakdown?.technical_recovery ?? 0}
              </Text>
              <Text style={styles.summaryFacetDesc}>OCR retry task</Text>
            </Card>
          </Pressable>

          {/* 5. Recapture Requirements (Missing or Unreadable Photos) */}
          <Pressable onPress={() => handleSelectFacet('recapture_required')}>
            <Card
              padding="sm"
              style={[
                styles.summaryCard,
                facetFilter === 'recapture_required' && styles.summaryCardSelected,
              ]}
            >
              <Text style={styles.summaryLabel}>Recapture</Text>
              <Text style={[styles.summaryValue, { color: colors.warning }]}>
                {summary.breakdown?.recapture_required ?? 0}
              </Text>
              <Text style={styles.summaryFacetDesc}>Missing panels</Text>
            </Card>
          </Pressable>

          {/* 6. Low Confidence Assessments (< 60%) */}
          <Pressable onPress={() => handleSelectFacet('low_confidence')}>
            <Card
              padding="sm"
              style={[
                styles.summaryCard,
                facetFilter === 'low_confidence' && styles.summaryCardSelected,
              ]}
            >
              <Text style={styles.summaryLabel}>Low Conf</Text>
              <Text style={[styles.summaryValue, { color: colors.textSecondary }]}>
                {summary.low_confidence ?? 0}
              </Text>
              <Text style={styles.summaryFacetDesc}>&lt; 60% conf</Text>
            </Card>
          </Pressable>
        </ScrollView>
      )}

      {/* Filter Tabs */}
      <View style={styles.filterSection}>
        {/* Status Filters */}
        <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.filterBar}>
          {STATUS_FILTERS.map((f) => (
            <Pressable
              key={f.id}
              style={[styles.filterChip, statusFilter === f.id && styles.filterChipActive]}
              onPress={() => handleSelectStatus(f.id)}
            >
              <Text style={[styles.filterChipText, statusFilter === f.id && styles.filterChipTextActive]}>
                {f.label}
              </Text>
            </Pressable>
          ))}
        </ScrollView>

        {/* Reason Filters */}
        <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={[styles.filterBar, { marginTop: 6 }]}>
          {REASON_FILTERS.map((r) => (
            <Pressable
              key={r.id}
              style={[styles.subFilterChip, reasonFilter === r.id && styles.subFilterChipActive]}
              onPress={() => handleSelectReason(r.id)}
            >
              <Text style={[styles.subFilterChipText, reasonFilter === r.id && styles.subFilterChipTextActive]}>
                {r.label}
              </Text>
            </Pressable>
          ))}
        </ScrollView>
      </View>

      {/* Search Input */}
      <View style={styles.searchContainer}>
        <TextInput
          style={styles.searchInput}
          placeholder="Filter by city or district..."
          placeholderTextColor={colors.placeholder}
          value={areaSearch}
          onChangeText={setAreaSearch}
          onSubmitEditing={() => {
            setOffset(0);
            loadData();
          }}
          returnKeyType="search"
        />
      </View>

      {/* Matching Results Header */}
      <View style={styles.resultsHeader}>
        <Text style={styles.resultsCountText}>
          {facetFilter !== 'ALL'
            ? `${totalMatching} scan${totalMatching === 1 ? '' : 's'} in ${facetFilter.replace(/_/g, ' ')}`
            : `${totalMatching} scan${totalMatching === 1 ? '' : 's'} matching`}
          {totalMatching > 0 && ` (showing ${offset + 1}–${Math.min(offset + items.length, totalMatching)})`}
        </Text>
        {facetFilter !== 'ALL' && (
          <Pressable onPress={() => handleSelectFacet('ALL')} hitSlop={6}>
            <Text style={styles.facetClearBtn}>Clear Category ✕</Text>
          </Pressable>
        )}
      </View>

      {/* Item List / States */}
      <ScrollView
        style={styles.scroll}
        contentContainerStyle={styles.listContent}
        refreshControl={
          <RefreshControl refreshing={refreshing} onRefresh={() => loadData(true)} colors={[colors.netraTeal]} />
        }
      >
        {loading && !refreshing ? (
          <View style={styles.centerContainer}>
            <ActivityIndicator size="large" color={colors.netraTeal} />
            <Text style={styles.loadingText}>Loading authoritative review queue...</Text>
          </View>
        ) : loadError ? (
          <Card padding="lg" style={styles.errorCard}>
            <Text style={styles.errorTitle}>
              {loadError.type === 'auth'
                ? '🔒 Authentication Required'
                : loadError.type === 'forbidden'
                ? '🚫 Access Restricted'
                : loadError.type === 'validation'
                ? '⚠️ Filter Validation Error'
                : '⚠️ Review Queue Unavailable'}
            </Text>
            <Text style={styles.errorDescription}>
              {loadError.message}
            </Text>
            {loadError.status && (
              <Text style={styles.errorDetails}>HTTP Status {loadError.status}</Text>
            )}
            <View style={{ marginTop: spacing.md, width: '100%' }}>
              <PrimaryButton title="Retry Request" onPress={() => loadData()} />
            </View>
          </Card>
        ) : displayedItems.length === 0 ? (
          <EmptyState
            icon="⚖️"
            title="No inspections require review"
            subtitle={
              facetFilter !== 'ALL'
                ? `No queue items match category filter "${facetFilter.replace(/_/g, ' ')}".`
                : "All submitted inspections have passed, were found compliant, or have been fully adjudicated."
            }
            variant="neutral"
          />
        ) : (
          <>
            {displayedItems.map((item) => {
              const pStyle = getPriorityStyle(item.priority);
              const currentStatus = (item.review_status || item.status || 'OPEN').toUpperCase();
              const sStyle = getStatusBadgeStyle(currentStatus);
              const categoryBadge = getCategoryBadge(item);
              const reasons = item.reasons || item.review_reasons || [];
              const unconfirmedCount = item.unconfirmed_count ?? (item.findings?.filter((f) => f.needs_review)?.length || 0);

              // Determine truthful footer guidance
              let footerGuidance = 'All findings verified';
              if (categoryBadge.type === 'technical_recovery') {
                footerGuidance = '⚙️ Technical Recovery Task (Not a Statutory Violation)';
              } else if (categoryBadge.type === 'confirmed_violations') {
                footerGuidance = '🚨 Confirmed Legal Violation — Enforcement Action Pending';
              } else if (categoryBadge.type === 'recapture_required') {
                footerGuidance = '📷 Evidence Recapture Required';
              } else if (unconfirmedCount > 0) {
                footerGuidance = `⚠️ ${unconfirmedCount} unconfirmed finding(s) require review`;
              }

              return (
                <Pressable
                  key={item.scan_id ? `scan-${item.scan_id}` : (item.review_id || `${item.inspection_id}-${item.scan_id}`)}
                  onPress={() => onSelectInspection && onSelectInspection(item.inspection_id)}
                >
                  <Card padding="md" style={styles.itemCard}>
                    {/* Top Header: ID, Priority, Status */}
                    <View style={styles.rowBetween}>
                      <Text style={styles.inspectionId}>
                        INSP #{item.inspection_id} • SCAN #{item.scan_id}
                      </Text>
                      <View style={styles.rowAlign}>
                        <View style={[styles.badge, { backgroundColor: pStyle.bg, borderColor: pStyle.border }]}>
                          <Text style={[styles.badgeText, { color: pStyle.text }]}>
                            {item.priority?.toUpperCase() || 'MEDIUM'}
                          </Text>
                        </View>
                        <View style={[styles.badge, { backgroundColor: sStyle.bg, borderColor: sStyle.border, marginLeft: 6 }]}>
                          <Text style={[styles.badgeText, { color: sStyle.text }]}>
                            {currentStatus}
                          </Text>
                        </View>
                      </View>
                    </View>

                    {/* Category Badge */}
                    <View style={{ marginTop: 6, alignSelf: 'flex-start' }}>
                      <View
                        style={[
                          styles.categoryBadge,
                          { backgroundColor: categoryBadge.bg, borderColor: categoryBadge.border },
                        ]}
                      >
                        <Text style={[styles.categoryBadgeText, { color: categoryBadge.text }]}>
                          {categoryBadge.label}
                        </Text>
                      </View>
                    </View>

                    {/* Store & Date Info */}
                    <Text style={styles.storeName}>{item.store_name || 'Retail Establishment'}</Text>
                    <Text style={styles.metaText}>
                      {item.store_area ? `${item.store_area} • ` : ''}
                      {item.inspection_date ? String(item.inspection_date).slice(0, 10) : 'Recent'} • Officer: {item.inspector_name || 'Assigned Officer'}
                    </Text>

                    {/* Commodity / Product info */}
                    <Text style={styles.commodityText}>
                      📦 {item.commodity_generic || 'Pre-packaged Commodity'} {item.brand_name ? `(${item.brand_name})` : ''}
                    </Text>

                    {/* Actionable Reasons Box */}
                    {reasons.length > 0 && (
                      <View style={styles.reasonsBox}>
                        {reasons.map((r, idx) => {
                          const isTech = r.subtype === 'technical_recovery';
                          const isViolation = r.subtype === 'confirmed_violation' || r.subtype === 'unconfirmed_violation';
                          return (
                            <View key={idx} style={styles.reasonRow}>
                              <Text
                                style={[
                                  styles.reasonBullet,
                                  isTech ? { color: '#6D28D9' } : isViolation ? { color: colors.violation.text } : { color: colors.warning },
                                ]}
                              >
                                •
                              </Text>
                              <Text style={styles.reasonDetail}>{r.detail}</Text>
                            </View>
                          );
                        })}
                      </View>
                    )}

                    {/* Bottom Footer: Truthful Category Guidance & Action CTA */}
                    <View style={[styles.rowBetween, styles.cardFooter]}>
                      <Text
                        style={[
                          styles.unconfirmedText,
                          categoryBadge.type === 'technical_recovery'
                            ? { color: '#6D28D9' }
                            : categoryBadge.type === 'confirmed_violations'
                            ? { color: colors.violation.text }
                            : { color: colors.warning },
                        ]}
                      >
                        {footerGuidance}
                      </Text>
                      <Text style={styles.actionCta}>Open Adjudication →</Text>
                    </View>
                  </Card>
                </Pressable>
              );
            })}

            {/* Pagination Controls */}
            {totalMatching > PAGE_SIZE && (
              <View style={styles.paginationRow}>
                <Pressable
                  style={[styles.pageButton, offset === 0 && styles.pageButtonDisabled]}
                  disabled={offset === 0 || loading}
                  onPress={() => setOffset((prev) => Math.max(0, prev - PAGE_SIZE))}
                >
                  <Text style={[styles.pageButtonText, offset === 0 && styles.pageButtonTextDisabled]}>
                    ← Previous
                  </Text>
                </Pressable>

                <Text style={styles.pageInfoText}>
                  Page {Math.floor(offset / PAGE_SIZE) + 1} of {Math.ceil(totalMatching / PAGE_SIZE) || 1}
                </Text>

                <Pressable
                  style={[
                    styles.pageButton,
                    offset + items.length >= totalMatching && styles.pageButtonDisabled,
                  ]}
                  disabled={offset + items.length >= totalMatching || loading}
                  onPress={() => setOffset((prev) => prev + PAGE_SIZE)}
                >
                  <Text
                    style={[
                      styles.pageButtonText,
                      offset + items.length >= totalMatching && styles.pageButtonTextDisabled,
                    ]}
                  >
                    Next →
                  </Text>
                </Pressable>
              </View>
            )}
          </>
        )}
      </ScrollView>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: colors.background,
  },
  offlineBanner: {
    backgroundColor: colors.warningBg,
    borderColor: colors.warning,
    borderBottomWidth: 1,
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.xs,
  },
  offlineText: {
    fontSize: 11,
    color: colors.warning,
    fontWeight: '600',
    textAlign: 'center',
  },
  draftNoticeBanner: {
    backgroundColor: '#FFFDF5',
    borderColor: '#FDE68A',
    borderBottomWidth: 1,
    paddingHorizontal: spacing.md,
    paddingVertical: 6,
  },
  draftNoticeText: {
    fontSize: 11,
    color: '#92400E',
    fontWeight: '600',
    lineHeight: 15,
  },
  summaryScroll: {
    paddingHorizontal: spacing.md,
    paddingTop: spacing.sm,
    paddingBottom: 2,
    gap: spacing.xs,
  },
  summaryCard: {
    width: 104,
    alignItems: 'center',
    paddingVertical: 8,
    borderRadius: radius.md,
    borderWidth: 1,
    borderColor: colors.borderLight,
  },
  summaryCardSelected: {
    borderColor: colors.niyamBlue,
    borderWidth: 2,
    backgroundColor: '#F0F9FF',
  },
  summaryLabel: {
    fontSize: 10,
    fontWeight: '700',
    color: colors.textSecondary,
    marginBottom: 2,
    textAlign: 'center',
  },
  summaryValue: {
    fontSize: 18,
    fontWeight: '800',
  },
  summaryFacetDesc: {
    fontSize: 9,
    color: colors.textMuted,
    marginTop: 2,
  },
  filterSection: {
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.xs,
  },
  filterBar: {
    gap: spacing.xs,
  },
  filterChip: {
    paddingHorizontal: 12,
    paddingVertical: 6,
    borderRadius: radius.md,
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderWidth: 1,
  },
  filterChipActive: {
    backgroundColor: colors.niyamBlue,
    borderColor: colors.niyamBlue,
  },
  filterChipText: {
    fontSize: 12,
    fontWeight: '600',
    color: colors.textSecondary,
  },
  filterChipTextActive: {
    color: colors.white,
  },
  subFilterChip: {
    paddingHorizontal: 10,
    paddingVertical: 4,
    borderRadius: radius.sm,
    backgroundColor: colors.surface,
    borderColor: colors.borderLight,
    borderWidth: 1,
  },
  subFilterChipActive: {
    backgroundColor: colors.netraTeal,
    borderColor: colors.netraTeal,
  },
  subFilterChipText: {
    fontSize: 11,
    fontWeight: '500',
    color: colors.textMuted,
  },
  subFilterChipTextActive: {
    color: colors.white,
    fontWeight: '700',
  },
  searchContainer: {
    paddingHorizontal: spacing.md,
    paddingBottom: spacing.xs,
  },
  searchInput: {
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderWidth: 1,
    borderRadius: radius.md,
    paddingHorizontal: spacing.md,
    paddingVertical: 6,
    fontSize: 12,
    color: colors.text,
  },
  resultsHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    backgroundColor: '#F8FAFC',
    paddingHorizontal: spacing.md,
    paddingVertical: 8,
    borderBottomWidth: 1,
    borderColor: colors.borderLight,
  },
  resultsCountText: {
    fontSize: 12,
    fontWeight: '700',
    color: colors.textSecondary,
  },
  facetClearBtn: {
    fontSize: 11,
    fontWeight: '700',
    color: colors.niyamBlue,
  },
  paginationRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    paddingVertical: spacing.md,
    marginTop: spacing.sm,
    borderTopWidth: 1,
    borderTopColor: colors.borderLight,
  },
  pageButton: {
    paddingHorizontal: spacing.md,
    paddingVertical: 8,
    borderRadius: radius.md,
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderWidth: 1,
  },
  pageButtonDisabled: {
    opacity: 0.4,
    borderColor: colors.borderLight,
  },
  pageButtonText: {
    fontSize: 12,
    fontWeight: '700',
    color: colors.netraTeal,
  },
  pageButtonTextDisabled: {
    color: colors.textMuted,
  },
  pageInfoText: {
    fontSize: 12,
    fontWeight: '600',
    color: colors.textSecondary,
  },
  scroll: {
    flex: 1,
  },
  listContent: {
    padding: spacing.md,
    paddingBottom: spacing.xxxl + 40,
    gap: spacing.md,
  },
  centerContainer: {
    paddingVertical: spacing.xxl,
    alignItems: 'center',
    justifyContent: 'center',
  },
  loadingText: {
    marginTop: spacing.sm,
    fontSize: 13,
    color: colors.textMuted,
  },
  errorCard: {
    backgroundColor: '#FEF2F2',
    borderColor: '#FECACA',
    borderWidth: 1,
    alignItems: 'center',
    padding: spacing.lg,
  },
  errorTitle: {
    fontSize: 15,
    fontWeight: '800',
    color: colors.error,
    marginBottom: spacing.xs,
  },
  errorDescription: {
    fontSize: 12,
    color: colors.textSecondary,
    textAlign: 'center',
  },
  errorDetails: {
    fontSize: 11,
    color: colors.textMuted,
    marginTop: 4,
    textAlign: 'center',
  },
  itemCard: {
    backgroundColor: colors.surface,
    borderRadius: radius.lg,
    ...shadows.sm,
  },
  rowBetween: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
  },
  rowAlign: {
    flexDirection: 'row',
    alignItems: 'center',
  },
  inspectionId: {
    fontSize: 11,
    fontWeight: '800',
    color: colors.netraTeal,
    letterSpacing: 0.5,
  },
  badge: {
    paddingHorizontal: 6,
    paddingVertical: 2,
    borderRadius: radius.sm,
    borderWidth: 1,
  },
  badgeText: {
    fontSize: 9,
    fontWeight: '800',
    letterSpacing: 0.5,
  },
  categoryBadge: {
    paddingHorizontal: 8,
    paddingVertical: 2,
    borderRadius: radius.sm,
    borderWidth: 1,
  },
  categoryBadgeText: {
    fontSize: 9,
    fontWeight: '800',
    letterSpacing: 0.5,
  },
  storeName: {
    fontSize: 15,
    fontWeight: '700',
    color: colors.text,
    marginTop: 6,
  },
  metaText: {
    fontSize: 11,
    color: colors.textMuted,
    marginTop: 2,
  },
  commodityText: {
    fontSize: 12,
    color: colors.textSecondary,
    fontWeight: '600',
    marginTop: 4,
  },
  reasonsBox: {
    backgroundColor: '#F8FAFC',
    borderRadius: radius.sm,
    padding: spacing.sm,
    marginTop: spacing.sm,
    gap: 3,
  },
  reasonRow: {
    flexDirection: 'row',
    alignItems: 'flex-start',
  },
  reasonBullet: {
    fontSize: 12,
    marginRight: 4,
    fontWeight: '700',
  },
  reasonDetail: {
    fontSize: 11,
    color: colors.textSecondary,
    flex: 1,
    lineHeight: 15,
  },
  cardFooter: {
    marginTop: spacing.sm,
    paddingTop: spacing.xs,
    borderTopWidth: 1,
    borderTopColor: colors.borderLight,
  },
  unconfirmedText: {
    fontSize: 11,
    fontWeight: '600',
    flex: 1,
    marginRight: spacing.sm,
  },
  actionCta: {
    fontSize: 12,
    fontWeight: '700',
    color: colors.netraTeal,
  },
});
