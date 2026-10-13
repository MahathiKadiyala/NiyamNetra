// screens/inspector/InspectionReportScreen.jsx — Phase 6C: Compliance Reports & Inspection Dossier UI
import React, { useState, useEffect, useCallback } from 'react';
import {
  View,
  Text,
  ScrollView,
  Pressable,
  ActivityIndicator,
  Share,
  Linking,
  Alert,
  StyleSheet,
} from 'react-native';
import { colors, spacing, typography, radius } from '../../theme';
import Header from '../../components/Header';
import Card from '../../components/Card';
import VerdictBadge from '../../components/VerdictBadge';
import PrimaryButton from '../../components/PrimaryButton';
import {
  fetchComplianceSummary,
  fetchViolationDossier,
  getInspectionPdfUrl,
} from '../../api/enforcement';
import {
  cacheReportSummary,
  loadCachedReportSummary,
  cacheViolationDossier,
  loadCachedViolationDossier,
} from '../../offline/queue';
import { useSync } from '../../offline/SyncProvider';

export default function InspectionReportScreen({
  inspectionId,
  onBack,
  onResumeDraft,
  onOpenReviewDetail,
  onOpenRecapture,
}) {
  const { isOnline } = useSync();
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState(null);
  const [isCached, setIsCached] = useState(false);

  // Live state
  const [summary, setSummary] = useState(null);
  const [dossier, setDossier] = useState(null);
  const [activeTab, setActiveTab] = useState('summary'); // 'summary' | 'dossier' | 'review' | 'notice'

  const loadData = useCallback(async () => {
    if (!inspectionId) return;
    setError(null);

    // Try online fetch first if online
    if (isOnline) {
      try {
        const [sumData, dosData] = await Promise.all([
          fetchComplianceSummary(inspectionId).catch(() => null),
          fetchViolationDossier(inspectionId).catch(() => null),
        ]);

        if (sumData) {
          setSummary(sumData);
          await cacheReportSummary(inspectionId, sumData);
        }
        if (dosData) {
          setDossier(dosData);
          await cacheViolationDossier(inspectionId, dosData);
        }

        if (sumData || dosData) {
          setIsCached(false);
          setLoading(false);
          setRefreshing(false);
          return;
        }
      } catch (err) {
        console.warn('[Report] Online fetch failed, falling back to cache:', err);
      }
    }

    // Offline / fallback cache
    try {
      const cachedSum = await loadCachedReportSummary(inspectionId);
      const cachedDos = await loadCachedViolationDossier(inspectionId);
      if (cachedSum || cachedDos) {
        setSummary(cachedSum);
        setDossier(cachedDos);
        setIsCached(true);
        setError(null);
      } else {
        setError('Report data unavailable offline. Connect to sync or view once online.');
      }
    } catch (cacheErr) {
      setError('Unable to load inspection report data.');
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [inspectionId, isOnline]);

  useEffect(() => {
    loadData();
  }, [loadData]);

  const handleRefresh = () => {
    setRefreshing(true);
    loadData();
  };

  const handleOpenPdf = async () => {
    const pdfUrl = getInspectionPdfUrl(inspectionId);
    try {
      const supported = await Linking.canOpenURL(pdfUrl);
      if (supported) {
        await Linking.openURL(pdfUrl);
      } else {
        await Share.share({
          title: `Inspection Report #${inspectionId}`,
          message: `Official Inspection Report #${inspectionId}: ${pdfUrl}`,
          url: pdfUrl,
        });
      }
    } catch (err) {
      Alert.alert('Export Notice', `Report link ready: ${pdfUrl}`);
    }
  };

  const handleExportJson = async () => {
    try {
      const payload = {
        inspection_id: inspectionId,
        summary,
        dossier,
        exported_at: new Date().toISOString(),
        provenance: 'NiyamNetra Legal Metrology Verification System',
        rule_pack_version: summary?.package_summaries?.[0]?.violation_dossier?.rule_pack_version || '2026.09.v1',
      };
      await Share.share({
        title: `Inspection Data #${inspectionId}`,
        message: JSON.stringify(payload, null, 2),
      });
    } catch (err) {
      Alert.alert('Export Error', 'Unable to share JSON export.');
    }
  };

  // Derive aggregates
  const packages = summary?.package_summaries || [];
  const totalPass = packages.reduce((acc, p) => acc + (p.pass_count || 0), 0);
  const totalFail = packages.reduce((acc, p) => acc + (p.fail_count || 0), 0);
  const totalNotAssessed = packages.reduce((acc, p) => acc + (p.not_assessed_count || 0), 0);
  const overallVerdict = summary?.overall_verdict || (totalFail > 0 ? 'VIOLATION' : (totalNotAssessed > 0 ? 'REVIEW_REQUIRED' : 'COMPLIANT'));
  const s36 = summary?.section_36_guidance || dossier?.section_36_guidance;
  const store = summary?.store;
  const inspector = summary?.inspector;

  return (
    <View style={styles.container}>
      <Header
        title={`Inspection Report #${inspectionId}`}
        subtitle="Statutory Compliance & Legal Dossier"
        onBack={onBack}
        rightAction={{
          label: 'PDF',
          icon: '📄',
          onPress: handleOpenPdf,
        }}
      />

      {isCached && (
        <View style={styles.cachedBanner}>
          <Text style={styles.cachedText}>
            ⚡ Viewing Offline Cached Report (Last synchronized data)
          </Text>
        </View>
      )}

      {/* Navigation Sub-Tabs */}
      <View style={styles.tabBar}>
        <Pressable
          style={[styles.tabItem, activeTab === 'summary' && styles.tabItemActive]}
          onPress={() => setActiveTab('summary')}
        >
          <Text style={[styles.tabText, activeTab === 'summary' && styles.tabTextActive]}>
            Overview
          </Text>
        </Pressable>
        <Pressable
          style={[styles.tabItem, activeTab === 'dossier' && styles.tabItemActive]}
          onPress={() => setActiveTab('dossier')}
        >
          <Text style={[styles.tabText, activeTab === 'dossier' && styles.tabTextActive]}>
            Violations ({totalFail})
          </Text>
        </Pressable>
        <Pressable
          style={[styles.tabItem, activeTab === 'review' && styles.tabItemActive]}
          onPress={() => setActiveTab('review')}
        >
          <Text style={[styles.tabText, activeTab === 'review' && styles.tabTextActive]}>
            Review Needs ({totalNotAssessed})
          </Text>
        </Pressable>
        <Pressable
          style={[styles.tabItem, activeTab === 'notice' && styles.tabItemActive]}
          onPress={() => setActiveTab('notice')}
        >
          <Text style={[styles.tabText, activeTab === 'notice' && styles.tabTextActive]}>
            Draft Notice
          </Text>
        </Pressable>
      </View>

      <ScrollView style={styles.scroll} contentContainerStyle={styles.content}>
        {loading ? (
          <View style={styles.centered}>
            <ActivityIndicator size="large" color={colors.niyamBlue} />
            <Text style={{ marginTop: spacing.md, color: colors.textSecondary }}>
              Loading official compliance report...
            </Text>
          </View>
        ) : error ? (
          <Card padding="lg" style={styles.errorCard}>
            <Text style={styles.errorTitle}>Report Unavailable</Text>
            <Text style={styles.errorBody}>{error}</Text>
            <PrimaryButton
              title="Retry Loading"
              onPress={handleRefresh}
              style={{ marginTop: spacing.md }}
            />
          </Card>
        ) : (
          <>
            {/* TAB 1: OVERVIEW & STATUTORY SUMMARY */}
            {activeTab === 'summary' && (
              <>
                {(summary?.status === 'draft' || summary?.display_status === 'In Progress') && (
                  <Card padding="md" style={{ marginBottom: spacing.md, backgroundColor: '#FFFBEB', borderColor: '#F59E0B', borderWidth: 1.5 }}>
                    <View style={styles.rowBetween}>
                      <View style={{ flex: 1, marginRight: spacing.sm }}>
                        <Text style={{ fontSize: 13, fontWeight: '800', color: '#92400E' }}>
                          📝 Draft Inspection Visit In Progress
                        </Text>
                        <Text style={{ fontSize: 11, color: '#B45309', marginTop: 2 }}>
                          This inspection has not been submitted yet. You can resume scanning, review findings, and finalize the visit.
                        </Text>
                      </View>
                      {onResumeDraft && (
                        <Pressable
                          onPress={() => onResumeDraft(inspectionId)}
                          style={{
                            backgroundColor: colors.netraTeal,
                            paddingHorizontal: 12,
                            paddingVertical: 8,
                            borderRadius: radius.sm,
                          }}
                        >
                          <Text style={{ color: colors.white, fontSize: 12, fontWeight: '700' }}>Resume →</Text>
                        </Pressable>
                      )}
                    </View>
                  </Card>
                )}
                <Card padding="md" style={styles.summaryCard}>
                  <View style={styles.rowBetween}>
                    <View style={{ flex: 1 }}>
                      <Text style={styles.establishmentName}>
                        {store?.name || 'Retail Establishment'}
                      </Text>
                      <Text style={styles.establishmentAddress}>
                        {store?.address ? `${store.address}, ` : ''}{store?.city || 'Telangana'}
                        {store?.pincode ? ` - ${store.pincode}` : ''}
                      </Text>
                      <Text style={styles.metaRow}>
                        Date: {summary?.inspection_date || 'N/A'} • Officer: {inspector?.full_name || 'Inspector'}
                      </Text>
                    </View>
                    <View style={{ alignItems: 'flex-end', gap: 6 }}>
                      <VerdictBadge result={overallVerdict.toLowerCase()} size="md" />
                      <Text style={styles.rulePackBadge}>Pack 2026.09.v1</Text>
                    </View>
                  </View>

                  {/* High level metrics */}
                  <View style={styles.statsRow}>
                    <View style={[styles.statBox, { borderColor: colors.pass.border, backgroundColor: colors.pass.fill }]}>
                      <Text style={[styles.statNum, { color: colors.pass.text }]}>{totalPass}</Text>
                      <Text style={styles.statLabel}>PASS</Text>
                    </View>
                    <View style={[styles.statBox, { borderColor: colors.violation.border, backgroundColor: colors.violation.fill }]}>
                      <Text style={[styles.statNum, { color: colors.violation.text }]}>{totalFail}</Text>
                      <Text style={styles.statLabel}>FAIL</Text>
                    </View>
                    <View style={[styles.statBox, { borderColor: colors.notAssessed.border, backgroundColor: colors.notAssessed.fill }]}>
                      <Text style={[styles.statNum, { color: colors.textMuted }]}>{totalNotAssessed}</Text>
                      <Text style={styles.statLabel}>NEEDS REVIEW</Text>
                    </View>
                  </View>
                </Card>

                {/* Section 36 Guidance */}
                {s36?.applicable && (
                  <Card padding="md" style={[styles.s36Card, { borderColor: colors.violation.border }]}>
                    <Text style={styles.s36Title}>⚖️ Section 36 Enforcement Guidance</Text>
                    <Text style={styles.s36Summary}>{s36.summary}</Text>
                    {s36.recommended_action && (
                      <View style={styles.s36ActionBox}>
                        <Text style={styles.s36ActionLabel}>
                          RECOMMENDED STATUTORY ACTION: {s36.recommended_action.toUpperCase().replace(/_/g, ' ')}
                        </Text>
                        <Text style={styles.s36ActionDetail}>{s36.action_detail}</Text>
                      </View>
                    )}
                    {s36.limbs && s36.limbs.map((limb, idx) => (
                      <View key={idx} style={styles.limbItem}>
                        <Text style={styles.limbBadge}>Limb {limb.limb}</Text>
                        <Text style={styles.limbDesc}>{limb.description} ({limb.violation_count} count)</Text>
                      </View>
                    ))}
                    <Text style={styles.legalDisclaimer}>{s36.legal_disclaimer}</Text>
                  </Card>
                )}

                {/* Packages in Inspection */}
                <Text style={styles.sectionHeader}>PACKAGES INSPECTED ({packages.length})</Text>
                {packages.length === 0 ? (
                  <Card padding="md">
                    <Text style={styles.mutedText}>No individual commodity scans recorded.</Text>
                  </Card>
                ) : (
                  packages.map((pkg) => (
                    <Card key={pkg.scan_id} padding="md" style={styles.packageCard}>
                      <View style={styles.rowBetween}>
                        <View style={{ flex: 1 }}>
                          <Text style={styles.pkgCommodity}>{pkg.commodity || 'Commodity Package'}</Text>
                          <Text style={styles.pkgMeta}>
                            Brand: {pkg.brand || 'Unbranded'} • Batch: {pkg.batch || 'N/A'}
                          </Text>
                          <Text style={styles.pkgSub}>
                            {pkg.pass_count} Pass • {pkg.fail_count} Fail • {pkg.not_assessed_count} Incomplete
                          </Text>
                        </View>
                        <VerdictBadge result={pkg.overall_result} size="sm" />
                      </View>
                    </Card>
                  ))
                )}

                {/* Export Options */}
                <View style={styles.exportRow}>
                  <Pressable style={styles.exportBtn} onPress={handleOpenPdf}>
                    <Text style={styles.exportBtnText}>📄 Export Official PDF</Text>
                  </Pressable>
                  <Pressable style={[styles.exportBtn, styles.exportBtnSecondary]} onPress={handleExportJson}>
                    <Text style={styles.exportBtnTextSecondary}>💾 Export Structured JSON</Text>
                  </Pressable>
                </View>
              </>
            )}

            {/* TAB 2: VIOLATION DOSSIER */}
            {activeTab === 'dossier' && (
              <>
                <Card padding="md" style={styles.integrityCard}>
                  <View style={styles.rowBetween}>
                    <Text style={styles.integrityTitle}>🔒 Audit-Ready Evidence Package</Text>
                    <Text style={styles.integrityBadge}>Integrity-Verifiable</Text>
                  </View>
                  <Text style={styles.integrityBody}>
                    All violation entries are referenced to immutable original camera captures,
                    timestamped sensor metadata, and rule engine ledger digests.
                  </Text>
                </Card>

                {totalFail === 0 ? (
                  <Card padding="lg" style={{ alignItems: 'center', marginTop: spacing.md }}>
                    <Text style={{ fontSize: 32 }}>🎉</Text>
                    <Text style={[typography.h3, { marginTop: spacing.sm }]}>No Confirmed Violations</Text>
                    <Text style={[typography.bodySecondary, { textAlign: 'center', marginTop: 4 }]}>
                      All assessed declarations satisfy the Legal Metrology (Packaged Commodities) Rules, 2011.
                    </Text>
                  </Card>
                ) : (
                  packages
                    .filter((p) => (p.violation_dossier?.violation_items?.length || 0) > 0)
                    .map((pkg) => (
                      <View key={pkg.scan_id} style={{ marginTop: spacing.md }}>
                        <Text style={styles.pkgSectionTitle}>
                          Package: {pkg.commodity} ({pkg.brand || 'N/A'})
                        </Text>
                        {pkg.violation_dossier.violation_items.map((v, i) => (
                          <Card key={`${pkg.scan_id}-${v.check_id}-${i}`} padding="md" style={styles.violationCard}>
                            <View style={styles.rowBetween}>
                              <View style={styles.rowAlign}>
                                <Text style={styles.checkId}>{v.check_id}</Text>
                                <Text style={styles.vTitle}>{v.title}</Text>
                              </View>
                              <Text style={[styles.severityBadge, v.severity === 'critical' ? styles.sevCrit : styles.sevMajor]}>
                                {v.severity?.toUpperCase()}
                              </Text>
                            </View>

                            <Text style={styles.vCitation}>Citation: {v.citation || 'Rule 6, LM Rules 2011'}</Text>
                            <Text style={styles.vReason}>Reason: {v.reason || 'Declaration absent or non-compliant.'}</Text>

                            <View style={styles.evidenceRefBox}>
                              <Text style={styles.evidenceRefLabel}>Audit Reference:</Text>
                              <Text style={styles.evidenceRefVal}>{v.ledger_ref || `SCAN-${pkg.scan_id}-${v.check_id}`}</Text>
                              {v.limb && (
                                <Text style={styles.evidenceLimb}>Applicable Section: Section {v.limb}</Text>
                              )}
                            </View>
                          </Card>
                        ))}
                      </View>
                    ))
                )}
              </>
            )}

            {/* TAB 3: REVIEW NEEDS & GAPS */}
            {activeTab === 'review' && (
              <>
                <Card padding="md" style={styles.reviewNoticeCard}>
                  <Text style={styles.reviewNoticeTitle}>🔍 Incomplete Declarations / Needs Review</Text>
                  <Text style={styles.reviewNoticeBody}>
                    Items with insufficient panel visibility, low OCR confidence, or unassessed rules
                    require officer verification or targeted evidence recapture.
                  </Text>
                  {onOpenReviewDetail && (
                    <PrimaryButton
                      title="Open Review & Adjudication Screen"
                      onPress={() => onOpenReviewDetail(inspectionId)}
                      style={{ marginTop: spacing.md }}
                    />
                  )}
                </Card>

                {packages.map((pkg) => (
                  <Card key={pkg.scan_id} padding="md" style={{ marginTop: spacing.sm }}>
                    <View style={styles.rowBetween}>
                      <View style={{ flex: 1 }}>
                        <Text style={styles.pkgCommodity}>{pkg.commodity}</Text>
                        <Text style={styles.pkgMeta}>
                          {pkg.not_assessed_count} unassessed or uncertain rule check(s)
                        </Text>
                      </View>
                      {onOpenRecapture && (
                        <Pressable
                          style={styles.recaptureBtn}
                          onPress={() => onOpenRecapture(inspectionId, pkg.scan_id)}
                        >
                          <Text style={styles.recaptureBtnText}>📷 Recapture</Text>
                        </Pressable>
                      )}
                    </View>
                  </Card>
                ))}
              </>
            )}

            {/* TAB 4: DRAFT STATUTORY NOTICE */}
            {activeTab === 'notice' && (
              <Card padding="lg" style={styles.noticeContainer}>
                <View style={styles.draftWatermark}>
                  <Text style={styles.draftText}>DRAFT — REQUIRES OFFICER REVIEW</Text>
                </View>

                <Text style={styles.noticeHeader}>GOVERNMENT OF TELANGANA</Text>
                <Text style={styles.noticeSubHeader}>DEPARTMENT OF LEGAL METROLOGY</Text>
                <Text style={styles.noticeSubHeader}>OFFICE OF THE INSPECTOR OF LEGAL METROLOGY</Text>

                <View style={styles.divider} />

                <Text style={styles.noticeSubject}>
                  INSPECTION MEMO / NOTICE OF NON-COMPLIANCE (PROVISIONAL)
                </Text>

                <Text style={styles.noticeBody}>
                  To: The Occupier / Representative{'\n'}
                  Establishment: {store?.name || 'Retail Establishment'}{'\n'}
                  Address: {store?.address || 'Premises'}, {store?.city || 'Telangana'}{'\n'}
                  Date of Inspection: {summary?.inspection_date || 'N/A'}{'\n'}
                  Inspection ID: {inspectionId}
                </Text>

                <Text style={styles.noticeBody}>
                  Take notice that during the inspection conducted on the above date, the following
                  pre-packaged commodities were examined under Section 15 of the Legal Metrology Act, 2009
                  read with the Legal Metrology (Packaged Commodities) Rules, 2011:
                </Text>

                <View style={styles.noticeFindingsBox}>
                  {packages.map((p, idx) => (
                    <Text key={p.scan_id} style={styles.noticeFindingItem}>
                      {idx + 1}. {p.commodity} ({p.brand || 'Unbranded'}) — Status: {p.overall_result.toUpperCase()}
                      {p.fail_count > 0 ? ` (${p.fail_count} violation items)` : ''}
                    </Text>
                  ))}
                </View>

                {s36?.applicable && (
                  <Text style={styles.noticeBody}>
                    Prima facie non-compliances attract provisions under Section {s36.limbs?.map((l) => l.limb).join(', ') || '36'} of
                    the Legal Metrology Act, 2009.
                  </Text>
                )}

                <View style={styles.officerSignatureArea}>
                  <Text style={styles.signatureLine}>__________________________________</Text>
                  <Text style={styles.signatureLabel}>
                    Inspecting Legal Metrology Officer{'\n'}
                    {inspector?.full_name || 'Authorized Inspector'}{'\n'}
                    (Signature & Seal Required for Issuance)
                  </Text>
                </View>

                <View style={styles.statutoryFooter}>
                  <Text style={styles.statutoryFooterText}>
                    LEGAL BOUNDARY NOTICE: This document is an automated draft decision-support record.
                    NiyamNetra does not issue formal notices, penalties, or seizure orders. Any statutory
                    action must be independently sanctioned and executed by the authorized officer.
                  </Text>
                </View>
              </Card>
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
  scroll: {
    flex: 1,
  },
  content: {
    padding: spacing.md,
    paddingBottom: spacing.xxl,
  },
  cachedBanner: {
    backgroundColor: colors.review.fill,
    borderColor: colors.review.border,
    borderWidth: 1,
    paddingVertical: 6,
    paddingHorizontal: spacing.md,
  },
  cachedText: {
    fontSize: 11,
    fontWeight: '600',
    color: colors.review.text,
    textAlign: 'center',
  },
  tabBar: {
    flexDirection: 'row',
    backgroundColor: colors.surface,
    borderBottomWidth: 1,
    borderBottomColor: colors.borderLight,
  },
  tabItem: {
    flex: 1,
    paddingVertical: 12,
    alignItems: 'center',
    borderBottomWidth: 2,
    borderBottomColor: 'transparent',
  },
  tabItemActive: {
    borderBottomColor: colors.niyamBlue,
  },
  tabText: {
    fontSize: 12,
    fontWeight: '600',
    color: colors.textSecondary,
  },
  tabTextActive: {
    color: colors.niyamBlue,
    fontWeight: '700',
  },
  centered: {
    padding: spacing.xl,
    alignItems: 'center',
    justifyContent: 'center',
  },
  errorCard: {
    backgroundColor: colors.violation.fill,
    borderColor: colors.violation.border,
    borderWidth: 1,
  },
  errorTitle: {
    fontSize: 16,
    fontWeight: '700',
    color: colors.violation.text,
  },
  errorBody: {
    fontSize: 13,
    color: colors.text,
    marginTop: 4,
  },
  summaryCard: {
    marginBottom: spacing.md,
  },
  establishmentName: {
    fontSize: 16,
    fontWeight: '800',
    color: colors.text,
  },
  establishmentAddress: {
    fontSize: 12,
    color: colors.textSecondary,
    marginTop: 2,
  },
  metaRow: {
    fontSize: 11,
    color: colors.textMuted,
    marginTop: 4,
  },
  rulePackBadge: {
    fontSize: 10,
    fontWeight: '700',
    color: colors.textSecondary,
    backgroundColor: colors.surfaceVariant,
    paddingHorizontal: 6,
    paddingVertical: 2,
    borderRadius: radius.xs,
  },
  statsRow: {
    flexDirection: 'row',
    gap: spacing.sm,
    marginTop: spacing.md,
    paddingTop: spacing.md,
    borderTopWidth: 1,
    borderTopColor: colors.borderLight,
  },
  statBox: {
    flex: 1,
    padding: spacing.sm,
    borderRadius: radius.sm,
    borderWidth: 1,
    alignItems: 'center',
  },
  statNum: {
    fontSize: 20,
    fontWeight: '800',
  },
  statLabel: {
    fontSize: 10,
    fontWeight: '700',
    color: colors.textSecondary,
    marginTop: 2,
  },
  s36Card: {
    marginBottom: spacing.md,
    borderWidth: 1,
  },
  s36Title: {
    fontSize: 14,
    fontWeight: '700',
    color: colors.violation.text,
  },
  s36Summary: {
    fontSize: 12,
    color: colors.text,
    marginTop: 4,
    lineHeight: 18,
  },
  s36ActionBox: {
    backgroundColor: colors.surfaceVariant,
    padding: spacing.sm,
    borderRadius: radius.sm,
    marginTop: spacing.sm,
  },
  s36ActionLabel: {
    fontSize: 11,
    fontWeight: '800',
    color: colors.text,
  },
  s36ActionDetail: {
    fontSize: 11,
    color: colors.textSecondary,
    marginTop: 2,
    lineHeight: 16,
  },
  limbItem: {
    flexDirection: 'row',
    alignItems: 'center',
    marginTop: 6,
    gap: 6,
  },
  limbBadge: {
    fontSize: 10,
    fontWeight: '700',
    backgroundColor: colors.violation.border,
    color: colors.surface,
    paddingHorizontal: 6,
    paddingVertical: 1,
    borderRadius: radius.xs,
  },
  limbDesc: {
    fontSize: 11,
    color: colors.textSecondary,
    flex: 1,
  },
  legalDisclaimer: {
    fontSize: 10,
    color: colors.textMuted,
    fontStyle: 'italic',
    marginTop: spacing.sm,
  },
  sectionHeader: {
    fontSize: 12,
    fontWeight: '800',
    color: colors.textSecondary,
    marginTop: spacing.sm,
    marginBottom: spacing.xs,
    letterSpacing: 0.5,
  },
  packageCard: {
    marginBottom: spacing.xs,
  },
  pkgCommodity: {
    fontSize: 14,
    fontWeight: '700',
    color: colors.text,
  },
  pkgMeta: {
    fontSize: 11,
    color: colors.textSecondary,
    marginTop: 2,
  },
  pkgSub: {
    fontSize: 10,
    color: colors.textMuted,
    marginTop: 2,
  },
  exportRow: {
    flexDirection: 'row',
    gap: spacing.sm,
    marginTop: spacing.md,
  },
  exportBtn: {
    flex: 1,
    backgroundColor: colors.niyamBlue,
    paddingVertical: 12,
    borderRadius: radius.md,
    alignItems: 'center',
  },
  exportBtnSecondary: {
    backgroundColor: colors.surfaceVariant,
    borderWidth: 1,
    borderColor: colors.borderLight,
  },
  exportBtnText: {
    fontSize: 12,
    fontWeight: '700',
    color: colors.surface,
  },
  exportBtnTextSecondary: {
    fontSize: 12,
    fontWeight: '700',
    color: colors.text,
  },
  integrityCard: {
    backgroundColor: colors.surfaceVariant,
    borderColor: colors.borderLight,
    borderWidth: 1,
    marginBottom: spacing.xs,
  },
  integrityTitle: {
    fontSize: 13,
    fontWeight: '700',
    color: colors.text,
  },
  integrityBadge: {
    fontSize: 10,
    fontWeight: '700',
    color: colors.niyamBlue,
    backgroundColor: colors.surface,
    paddingHorizontal: 6,
    paddingVertical: 2,
    borderRadius: radius.xs,
  },
  integrityBody: {
    fontSize: 11,
    color: colors.textSecondary,
    marginTop: 4,
    lineHeight: 16,
  },
  pkgSectionTitle: {
    fontSize: 13,
    fontWeight: '700',
    color: colors.textSecondary,
    marginBottom: 4,
  },
  violationCard: {
    marginBottom: spacing.xs,
    borderLeftWidth: 3,
    borderLeftColor: colors.violation.border,
  },
  checkId: {
    fontSize: 12,
    fontWeight: '800',
    color: colors.violation.text,
    marginRight: 6,
  },
  vTitle: {
    fontSize: 13,
    fontWeight: '700',
    color: colors.text,
  },
  severityBadge: {
    fontSize: 10,
    fontWeight: '800',
    paddingHorizontal: 6,
    paddingVertical: 1,
    borderRadius: radius.xs,
  },
  sevCrit: {
    backgroundColor: colors.violation.fill,
    color: colors.violation.text,
  },
  sevMajor: {
    backgroundColor: colors.review.fill,
    color: colors.review.text,
  },
  vCitation: {
    fontSize: 11,
    color: colors.niyamBlue,
    fontWeight: '600',
    marginTop: 4,
  },
  vReason: {
    fontSize: 11,
    color: colors.textSecondary,
    marginTop: 2,
    lineHeight: 16,
  },
  evidenceRefBox: {
    backgroundColor: colors.surfaceVariant,
    padding: 6,
    borderRadius: radius.xs,
    marginTop: 6,
  },
  evidenceRefLabel: {
    fontSize: 9,
    fontWeight: '700',
    color: colors.textMuted,
  },
  evidenceRefVal: {
    fontSize: 10,
    fontFamily: 'monospace',
    color: colors.text,
  },
  evidenceLimb: {
    fontSize: 10,
    fontWeight: '600',
    color: colors.violation.text,
    marginTop: 2,
  },
  reviewNoticeCard: {
    backgroundColor: colors.review.fill,
    borderColor: colors.review.border,
    borderWidth: 1,
    marginBottom: spacing.sm,
  },
  reviewNoticeTitle: {
    fontSize: 14,
    fontWeight: '700',
    color: colors.review.text,
  },
  reviewNoticeBody: {
    fontSize: 11,
    color: colors.text,
    marginTop: 4,
    lineHeight: 16,
  },
  recaptureBtn: {
    backgroundColor: colors.surfaceVariant,
    paddingHorizontal: 10,
    paddingVertical: 6,
    borderRadius: radius.xs,
    borderWidth: 1,
    borderColor: colors.borderLight,
  },
  recaptureBtnText: {
    fontSize: 11,
    fontWeight: '700',
    color: colors.niyamBlue,
  },
  noticeContainer: {
    position: 'relative',
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderWidth: 1,
  },
  draftWatermark: {
    backgroundColor: colors.violation.fill,
    paddingVertical: 4,
    paddingHorizontal: 8,
    borderRadius: radius.xs,
    alignSelf: 'center',
    marginBottom: spacing.md,
  },
  draftText: {
    fontSize: 11,
    fontWeight: '800',
    color: colors.violation.text,
    letterSpacing: 0.5,
  },
  noticeHeader: {
    fontSize: 13,
    fontWeight: '800',
    textAlign: 'center',
    color: colors.text,
  },
  noticeSubHeader: {
    fontSize: 11,
    fontWeight: '700',
    textAlign: 'center',
    color: colors.textSecondary,
    marginTop: 2,
  },
  divider: {
    height: 1,
    backgroundColor: colors.border,
    marginVertical: spacing.md,
  },
  noticeSubject: {
    fontSize: 12,
    fontWeight: '800',
    textAlign: 'center',
    color: colors.text,
    marginBottom: spacing.md,
    textDecorationLine: 'underline',
  },
  noticeBody: {
    fontSize: 11,
    color: colors.text,
    lineHeight: 18,
    marginBottom: spacing.sm,
  },
  noticeFindingsBox: {
    backgroundColor: colors.surfaceVariant,
    padding: spacing.sm,
    borderRadius: radius.xs,
    marginVertical: spacing.sm,
  },
  noticeFindingItem: {
    fontSize: 11,
    color: colors.text,
    lineHeight: 18,
  },
  officerSignatureArea: {
    marginTop: spacing.xl,
    alignItems: 'flex-end',
  },
  signatureLine: {
    color: colors.textMuted,
  },
  signatureLabel: {
    fontSize: 10,
    color: colors.textSecondary,
    textAlign: 'right',
    marginTop: 4,
    lineHeight: 14,
  },
  statutoryFooter: {
    marginTop: spacing.lg,
    paddingTop: spacing.sm,
    borderTopWidth: 1,
    borderTopColor: colors.borderLight,
  },
  statutoryFooterText: {
    fontSize: 9,
    color: colors.textMuted,
    fontStyle: 'italic',
    textAlign: 'center',
    lineHeight: 13,
  },
  rowBetween: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'flex-start',
  },
  rowAlign: {
    flexDirection: 'row',
    alignItems: 'center',
  },
});
