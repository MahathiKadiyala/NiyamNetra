import React, { useCallback, useEffect, useState } from 'react';
import {
  View,
  Text,
  ScrollView,
  Pressable,
  RefreshControl,
  ActivityIndicator,
  StyleSheet,
} from 'react-native';
import { colors, spacing, typography, radius, shadows } from '../../theme';
import Header from '../../components/Header';
import Card from '../../components/Card';
import VerdictBadge from '../../components/VerdictBadge';
import PrimaryButton from '../../components/PrimaryButton';
import EmptyState from '../../components/EmptyState';
import { useSync } from '../../offline/SyncProvider';
import { fetchTodayStats, fetchInspectionsList, fetchPendingScans, assessScan } from '../../api/inspections';
import { fetchMe } from '../../api/admin';
import { fetchReviewSummary } from '../../api/review';

export default function HomeScreen({ navigation, onStartInspection, onResumeInspection, onOpenReviewQueue, onOpenReport, activeInspection }) {
  const [officer, setOfficer] = useState(null);
  const [todayStats, setTodayStats] = useState(null);
  const [reviewSummary, setReviewSummary] = useState(null);
  const [recentInspections, setRecentInspections] = useState([]);
  const [pendingScans, setPendingScans] = useState([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [assessing, setAssessing] = useState(false);
  const [assessProgress, setAssessProgress] = useState(null);
  const { pending, isSyncing, syncNow } = useSync();

  const loadData = useCallback(async (isRefresh = false) => {
    if (isRefresh) setRefreshing(true);
    else setLoading(true);

    try {
      const [userRes, statsRes, inspRes, pendingRes, reviewRes] = await Promise.allSettled([
        fetchMe(),
        fetchTodayStats(),
        fetchInspectionsList(),
        fetchPendingScans(),
        fetchReviewSummary(),
      ]);

      if (userRes.status === 'fulfilled' && userRes.value) {
        setOfficer(userRes.value);
      }
      if (statsRes.status === 'fulfilled' && statsRes.value) {
        setTodayStats(statsRes.value);
      }
      if (inspRes.status === 'fulfilled' && inspRes.value) {
        const list = Array.isArray(inspRes.value) ? inspRes.value : [];
        setRecentInspections(list.slice(0, 5));
      }
      if (pendingRes.status === 'fulfilled' && pendingRes.value) {
        setPendingScans(Array.isArray(pendingRes.value) ? pendingRes.value : []);
      }
      if (reviewRes.status === 'fulfilled' && reviewRes.value) {
        setReviewSummary(reviewRes.value);
      }
    } catch (e) {
      // offline fallback
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    loadData();
  }, [loadData]);

  const runServerAssessment = useCallback(async () => {
    if (assessing || pendingScans.length === 0) return;
    setAssessing(true);
    setAssessProgress(`Preparing ${pendingScans.length} scans...`);

    const scanIds = pendingScans.map((s) => s.id).filter(Boolean);
    const total = scanIds.length;
    let completed = 0;
    const concurrency = 2;
    let nextIdx = 0;

    const assessWithRetry = async (scanId, retries = 3) => {
      for (let attempt = 1; attempt <= retries; attempt++) {
        try {
          return await assessScan(scanId);
        } catch (err) {
          const status = err?.status || err?.response?.status;
          if (status === 503 && attempt < retries) {
            const retryHeader = err?.headers?.['retry-after'] || err?.response?.headers?.['retry-after'];
            const waitSec = Math.min(10, Math.max(2, parseInt(retryHeader, 10) || attempt * 2));
            setAssessProgress(`Capacity busy, retrying scan ${scanId} in ${waitSec}s...`);
            await new Promise((r) => setTimeout(r, waitSec * 1000));
            continue;
          }
          if (attempt === retries) throw err;
        }
      }
    };

    const worker = async () => {
      while (nextIdx < scanIds.length) {
        const cur = nextIdx++;
        const id = scanIds[cur];
        completed += 1;
        setAssessProgress(`Assessing ${completed} of ${total}...`);
        try {
          await assessWithRetry(id);
        } catch (err) {
          console.warn(`[HomeScreen] Assessment failed for scan ${id}:`, err?.message || err);
        }
      }
    };

    try {
      const workers = Array.from({ length: Math.min(concurrency, scanIds.length) }, () => worker());
      await Promise.all(workers);
    } catch (e) {
      console.warn('[HomeScreen] runServerAssessment error:', e);
    } finally {
      setAssessing(false);
      setAssessProgress(null);
      await loadData(true);
    }
  }, [assessing, pendingScans, loadData]);

  const refusalsToday = todayStats?.counts?.refusals ?? recentInspections.filter(
    (i) => i.signature_status === 'refused' || i.result === 'refused' || i.overall_result === 'refused'
  ).length;

  const counts = todayStats?.counts || {
    total: recentInspections.length,
    compliant: recentInspections.filter((i) => (i.result || i.overall_result) === 'compliant').length,
    violation: recentInspections.filter((i) => (i.result || i.overall_result) === 'violation').length,
    not_assessed: 0,
    refusals: refusalsToday,
  };

  return (
    <View style={styles.container}>
      <Header
        title="Inspector Dashboard"
        subtitle={officer?.employee_id ? `${officer.employee_id} • ${officer.jurisdiction || 'Field Office'}` : 'Legal Metrology Enforcement'}
      />

      <ScrollView
        style={styles.scroll}
        contentContainerStyle={styles.content}
        refreshControl={
          <RefreshControl refreshing={refreshing} onRefresh={() => loadData(true)} tintColor={colors.netraTeal} />
        }
      >
        {/* Active Inspection Banner (if a visit is in progress) */}
        {activeInspection && (
          <Card
            padding="md"
            style={{
              marginBottom: spacing.lg,
              borderColor: colors.saffron,
              borderWidth: 1.5,
              backgroundColor: '#FFFDF7',
            }}
          >
            <View style={styles.rowBetween}>
              <View style={{ flex: 1 }}>
                <View style={styles.rowAlign}>
                  <Text style={styles.activePill}>ACTIVE VISIT IN PROGRESS</Text>
                </View>
                <Text style={[typography.h3, { marginTop: spacing.xs }]}>
                  {activeInspection.store?.name || 'Inspection in progress'}
                </Text>
                <Text style={styles.captionText}>
                  {activeInspection.scans?.length || 0} packages sampled • {activeInspection.transaction_type}
                </Text>
              </View>
              <Pressable
                onPress={onResumeInspection}
                style={styles.resumeButton}
                accessibilityRole="button"
                accessibilityLabel="Resume active inspection"
              >
                <Text style={styles.resumeButtonText}>Resume →</Text>
              </Pressable>
            </View>
          </Card>
        )}

        {/* Primary Action Button */}
        <View style={{ marginBottom: spacing.lg }}>
          <PrimaryButton
            title="+ Start New Store Inspection"
            onPress={onStartInspection}
            accessibilityLabel="Start a new store inspection visit"
          />
        </View>

        {/* Daily Summary Statistics */}
        <Text style={styles.sectionHeader}>TODAY'S INSPECTION SUMMARY</Text>
        <View style={styles.statsGrid}>
          <Card padding="md" style={styles.statCard}>
            <Text style={styles.statLabel}>Total Inspected</Text>
            <Text style={[typography.statNumber, { color: colors.niyamBlue }]}>
              {counts.total ?? 0}
            </Text>
            <Text style={styles.statSub}>Stores/Visits</Text>
          </Card>

          <Card padding="md" style={styles.statCard}>
            <Text style={styles.statLabel}>Compliant</Text>
            <Text style={[typography.statNumber, { color: colors.pass.text }]}>
              {counts.compliant ?? 0}
            </Text>
            <Text style={[styles.statSub, { color: colors.pass.text }]}>Rule 6 Conforming</Text>
          </Card>

          <Card padding="md" style={styles.statCard}>
            <Text style={styles.statLabel}>Violations Flagged</Text>
            <Text style={[typography.statNumber, { color: colors.violation.text }]}>
              {counts.violation ?? 0}
            </Text>
            <Text style={[styles.statSub, { color: colors.violation.text }]}>Section 36 Action</Text>
          </Card>

          <Card padding="md" style={styles.statCard}>
            <Text style={styles.statLabel}>Refusals</Text>
            <Text style={[typography.statNumber, { color: colors.review.text }]}>
              {refusalsToday ?? 0}
            </Text>
            <Text style={[styles.statSub, { color: colors.review.text }]}>Non-cooperation</Text>
          </Card>

          <Card padding="md" style={styles.statCard}>
            <Text style={styles.statLabel}>Pending Assessment</Text>
            <Text style={[typography.statNumber, { color: pendingScans.length > 0 ? colors.warning : colors.textMuted }]}>
              {pendingScans.length}
            </Text>
            <Text style={styles.statSub}>Server Queue</Text>
          </Card>

          <Card padding="md" style={styles.statCard}>
            <Text style={styles.statLabel}>Offline Queue</Text>
            <Text style={[typography.statNumber, { color: pending > 0 ? colors.warning : colors.textMuted }]}>
              {pending}
            </Text>
            <Text style={styles.statSub}>{isSyncing ? 'Syncing now…' : 'Queued offline'}</Text>
          </Card>
        </View>

        {/* Officer Review Queue Card */}
        <Pressable onPress={onOpenReviewQueue} hitSlop={4}>
          <Card
            padding="md"
            style={{
              marginBottom: spacing.md,
              borderColor: colors.saffron,
              borderWidth: 1.5,
              backgroundColor: '#FFFDF5',
            }}
          >
            <View style={styles.rowBetween}>
              <View style={{ flex: 1, marginRight: spacing.md }}>
                <View style={styles.rowAlign}>
                  <Text style={{ fontSize: 16, marginRight: 6 }}>⚖️</Text>
                  <Text style={{ fontSize: 13, fontWeight: '800', color: colors.niyamBlue }}>
                    Officer Review Queue
                  </Text>
                  {(reviewSummary?.total ?? 0) > 0 && (
                    <View style={{ backgroundColor: '#FEE2E2', borderColor: '#FCA5A5', borderWidth: 1, marginLeft: 6, paddingHorizontal: 6, paddingVertical: 1, borderRadius: 4 }}>
                      <Text style={{ fontSize: 10, fontWeight: '800', color: '#B91C1C' }}>
                        {reviewSummary.total} PENDING
                      </Text>
                    </View>
                  )}
                </View>
                <Text style={{ fontSize: 11, color: colors.textSecondary, marginTop: 4 }}>
                  Adjudicate incomplete evidence, low confidence, and unconfirmed violations.
                </Text>
              </View>
              <Text style={{ fontSize: 13, fontWeight: '700', color: colors.netraTeal }}>
                Open →
              </Text>
            </View>
          </Card>
        </Pressable>

        {/* Run Server Assessment Card */}
        {pendingScans.length > 0 && (
          <Card
            padding="md"
            style={{
              marginBottom: spacing.lg,
              borderColor: colors.netraTeal,
              borderWidth: 1.5,
              backgroundColor: '#F0FDFA',
            }}
          >
            <View style={styles.rowBetween}>
              <View style={{ flex: 1, marginRight: spacing.md }}>
                <Text style={{ fontSize: 13, fontWeight: '700', color: colors.niyamBlue }}>
                  ⚡ Server Assessment Available
                </Text>
                <Text style={{ fontSize: 11, color: colors.textSecondary, marginTop: 2 }}>
                  {assessing
                    ? assessProgress || `Assessing ${pendingScans.length} pending scans…`
                    : `${pendingScans.length} scanned package(s) awaiting cloud OCR rule checks.`}
                </Text>
              </View>
              <Pressable
                onPress={runServerAssessment}
                disabled={assessing}
                accessibilityRole="button"
                accessibilityLabel={`Run server assessment for ${pendingScans.length} pending scans`}
                style={{
                  backgroundColor: assessing ? colors.textMuted : colors.netraTeal,
                  paddingVertical: 10,
                  paddingHorizontal: 14,
                  borderRadius: radius.md,
                  justifyContent: 'center',
                  alignItems: 'center',
                  minHeight: 44,
                  minWidth: 100,
                }}
              >
                {assessing ? (
                  <ActivityIndicator size="small" color={colors.white} />
                ) : (
                  <Text style={{ color: colors.white, fontSize: 12, fontWeight: '700' }}>
                    Run Assess ({pendingScans.length})
                  </Text>
                )}
              </Pressable>
            </View>
          </Card>
        )}

        {/* Statutory Scope Info Card */}
        <Card padding="md" style={styles.infoCard}>
          <View style={styles.rowAlign}>
            <Text style={{ fontSize: 18, marginRight: spacing.sm }}>⚖️</Text>
            <View style={{ flex: 1 }}>
              <Text style={{ fontSize: 13, fontWeight: '700', color: colors.niyamBlue }}>
                Legal Metrology (Packaged Commodities) Rules 2011
              </Text>
              <Text style={{ fontSize: 11, color: colors.textSecondary, marginTop: 2, lineHeight: 15 }}>
                Authoritative Statutory Rule Checks (Rule Pack 2026.09.v1) active. Pre-packaged retail goods require all mandatory declarations before sale.
              </Text>
            </View>
          </View>
        </Card>

        {/* Recent Inspections Header */}
        <View style={[styles.rowBetween, { marginTop: spacing.lg, marginBottom: spacing.sm }]}>
          <Text style={styles.sectionHeader}>RECENT INSPECTIONS</Text>
          {recentInspections.length > 0 && (
            <Pressable onPress={() => navigation?.navigate?.('Pass')} hitSlop={8}>
              <Text style={{ fontSize: 12, fontWeight: '600', color: colors.netraTeal }}>View All →</Text>
            </Pressable>
          )}
        </View>

        {/* Recent Inspections List */}
        {loading && !refreshing ? (
          <View style={{ paddingVertical: spacing.xl, alignItems: 'center' }}>
            <ActivityIndicator size="small" color={colors.netraTeal} />
          </View>
        ) : recentInspections.length === 0 ? (
          <EmptyState
            icon="📋"
            title="No inspections recorded today"
            subtitle="Tap '+ Start New Store Inspection' above to begin an establishment visit."
            variant="neutral"
          />
        ) : (
          recentInspections.map((item) => {
            const dateStr = item.date || item.created_at || item.local_created_at || '';
            const isRefusal = item.signature_status === 'refused' || item.result === 'refused' || item.overall_result === 'refused';
            const verdict = isRefusal ? 'refused' : (item.overall_result || item.result || 'not_assessed');
            return (
              <Pressable
                key={item.id || item.client_uuid}
                onPress={() => item.id && onOpenReport && onOpenReport(item.id)}
              >
                <Card padding="md" style={styles.inspectionCard}>
                  <View style={styles.rowBetween}>
                    <View style={{ flex: 1 }}>
                      <Text style={{ fontSize: 14, fontWeight: '700', color: colors.text, marginBottom: 2 }}>
                        {item.store_name || item.store?.name || 'Retail Establishment'}
                      </Text>
                      <Text style={{ fontSize: 11, color: colors.textMuted }}>
                        {dateStr ? String(dateStr).slice(0, 10) : 'Today'} • {isRefusal ? 'Inspection Refused' : (item.transaction_type || 'Retail Sale')}
                      </Text>
                    </View>
                    <View style={{ alignItems: 'flex-end', gap: 4 }}>
                      <VerdictBadge status={item.status === 'submitted' ? 'submitted' : 'in_progress'} size="sm" />
                      <VerdictBadge result={verdict} size="sm" />
                    </View>
                  </View>
                </Card>
              </Pressable>
            );
          })
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
  scroll: {
    flex: 1,
  },
  content: {
    padding: spacing.lg,
    paddingBottom: spacing.xxxl + 60,
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
  activePill: {
    fontSize: 9,
    fontWeight: '800',
    letterSpacing: 0.8,
    color: colors.warning,
    backgroundColor: colors.warningBg,
    paddingHorizontal: 6,
    paddingVertical: 2,
    borderRadius: radius.sm,
  },
  resumeButton: {
    backgroundColor: colors.niyamBlue,
    paddingHorizontal: 12,
    paddingVertical: 8,
    borderRadius: radius.md,
  },
  resumeButtonText: {
    color: colors.white,
    fontSize: 12,
    fontWeight: '700',
  },
  sectionHeader: {
    fontSize: 11,
    fontWeight: '700',
    color: colors.textMuted,
    letterSpacing: 0.8,
    textTransform: 'uppercase',
  },
  statsGrid: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: spacing.sm,
    marginTop: spacing.sm,
    marginBottom: spacing.lg,
  },
  statCard: {
    flexBasis: '48%',
    flexGrow: 1,
    alignItems: 'flex-start',
  },
  statLabel: {
    fontSize: 11,
    fontWeight: '600',
    color: colors.textSecondary,
    marginBottom: 4,
  },
  statSub: {
    fontSize: 10,
    color: colors.textMuted,
    marginTop: 2,
  },
  infoCard: {
    backgroundColor: colors.info.fill,
    borderColor: colors.info.border,
    borderWidth: 1,
  },
  captionText: {
    fontSize: 11,
    color: colors.textSecondary,
    marginTop: 2,
  },
  inspectionCard: {
    marginBottom: spacing.sm,
  },
});
