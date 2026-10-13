// screens/inspector/ReviewDetailScreen.jsx — Phase 6A: Review Detail & Adjudication
import React, { useCallback, useEffect, useState } from 'react';
import {
  View,
  Text,
  ScrollView,
  Pressable,
  ActivityIndicator,
  TextInput,
  Image,
  Alert,
  StyleSheet,
} from 'react-native';
import { colors, spacing, typography, radius, shadows } from '../../theme';
import Header from '../../components/Header';
import Card from '../../components/Card';
import VerdictBadge from '../../components/VerdictBadge';
import PrimaryButton from '../../components/PrimaryButton';
import {
  fetchInspectionReviewDetail,
  adjudicateReview,
  resolveReviewConflict,
} from '../../api/review';
import {
  enqueueAdjudication,
  enqueueConflictResolution,
} from '../../offline/queue';

export default function ReviewDetailScreen({ inspectionId, onBack, onAdjudicated, onOpenRecapture, onOpenReport }) {
  const [detail, setDetail] = useState(null);
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [activeModal, setActiveModal] = useState(null); // 'override' | 'resolve_all' | 'conflict'
  const [selectedFinding, setSelectedFinding] = useState(null);
  const [selectedConflict, setSelectedConflict] = useState(null);

  // Adjudication form state
  const [overrideVerdict, setOverrideVerdict] = useState('pass');
  const [overrideReason, setOverrideReason] = useState('');
  const [generalAction, setGeneralAction] = useState('resolve');
  const [generalReason, setGeneralReason] = useState('');
  const [resolutionNotes, setResolutionNotes] = useState('');

  // Conflict form state
  const [conflictResolution, setConflictResolution] = useState('accept_panel_a');
  const [conflictResolvedValue, setConflictResolvedValue] = useState('');
  const [conflictReason, setConflictReason] = useState('');

  const [notification, setNotification] = useState(null);

  const loadDetail = useCallback(async () => {
    setLoading(true);
    try {
      const data = await fetchInspectionReviewDetail(inspectionId);
      setDetail(data);
    } catch (err) {
      setNotification({
        type: 'error',
        text: 'Failed to load inspection review detail from backend. Check connectivity.',
      });
    } finally {
      setLoading(false);
    }
  }, [inspectionId]);

  useEffect(() => {
    loadDetail();
  }, [loadDetail]);

  // Handle Finding Accept
  const handleAcceptFinding = async (finding) => {
    const defaultReason = `Confirmed automated finding for check ${finding.check_id}`;
    setSubmitting(true);
    try {
      await adjudicateReview({
        inspection_id: Number(inspectionId),
        action: 'accept',
        finding_id: finding.id,
        reason: defaultReason,
        human_verdict: finding.engine_verdict,
      });
      setNotification({ type: 'success', text: `Check ${finding.check_id} accepted as ${finding.engine_verdict}.` });
      await loadDetail();
    } catch (err) {
      // Offline fallback
      await enqueueAdjudication({
        inspection_id: Number(inspectionId),
        action: 'accept',
        finding_id: finding.id,
        reason: defaultReason,
        human_verdict: finding.engine_verdict,
      });
      setNotification({
        type: 'warning',
        text: `Adjudication queued locally for sync. Automated finding preserved.`,
      });
    } finally {
      setSubmitting(false);
    }
  };

  // Open Override Modal
  const openOverrideModal = (finding) => {
    setSelectedFinding(finding);
    setOverrideVerdict(finding.engine_verdict === 'fail' ? 'pass' : 'fail');
    setOverrideReason('');
    setActiveModal('override');
  };

  // Submit Override Finding
  const handleSubmitOverride = async () => {
    if (!overrideReason || overrideReason.trim().length < 10) {
      Alert.alert('Reason Required', 'Statutory override requires an explanation of at least 10 characters.');
      return;
    }

    setSubmitting(true);
    try {
      await adjudicateReview({
        inspection_id: Number(inspectionId),
        action: 'override',
        finding_id: selectedFinding.id,
        reason: overrideReason.trim(),
        human_verdict: overrideVerdict,
      });
      setActiveModal(null);
      setNotification({
        type: 'success',
        text: `Check ${selectedFinding.check_id} overridden to ${overrideVerdict.toUpperCase()}. Original engine verdict preserved.`,
      });
      await loadDetail();
      if (onAdjudicated) onAdjudicated();
    } catch (err) {
      // Offline fallback
      await enqueueAdjudication({
        inspection_id: Number(inspectionId),
        action: 'override',
        finding_id: selectedFinding.id,
        reason: overrideReason.trim(),
        human_verdict: overrideVerdict,
      });
      setActiveModal(null);
      setNotification({
        type: 'warning',
        text: `Override queued locally for sync. Will be processed when connected.`,
      });
    } finally {
      setSubmitting(false);
    }
  };

  // Submit Inspection Level Adjudication (Resolve, Escalate, Dismiss)
  const handleSubmitGeneralAction = async () => {
    if (!generalReason || generalReason.trim().length < 10) {
      Alert.alert('Reason Required', 'Mandatory explanation must be at least 10 characters.');
      return;
    }

    setSubmitting(true);
    try {
      await adjudicateReview({
        inspection_id: Number(inspectionId),
        action: generalAction,
        reason: generalReason.trim(),
        resolution_notes: resolutionNotes.trim() || undefined,
      });
      setActiveModal(null);
      setNotification({
        type: 'success',
        text: `Inspection review successfully marked as ${generalAction.toUpperCase()}.`,
      });
      await loadDetail();
      if (onAdjudicated) onAdjudicated();
    } catch (err) {
      // Offline fallback
      await enqueueAdjudication({
        inspection_id: Number(inspectionId),
        action: generalAction,
        reason: generalReason.trim(),
        resolution_notes: resolutionNotes.trim() || undefined,
      });
      setActiveModal(null);
      setNotification({
        type: 'warning',
        text: `Action queued locally for sync. Status will update after upload.`,
      });
    } finally {
      setSubmitting(false);
    }
  };

  // Submit Conflict Resolution
  const handleSubmitConflictResolution = async () => {
    if (!conflictReason || conflictReason.trim().length < 10) {
      Alert.alert('Reason Required', 'Conflict resolution explanation must be at least 10 characters.');
      return;
    }

    setSubmitting(true);
    try {
      await resolveReviewConflict({
        inspection_id: Number(inspectionId),
        conflict_id: selectedConflict.conflict_id,
        resolution: conflictResolution,
        resolved_value: conflictResolvedValue.trim() || undefined,
        reason: conflictReason.trim(),
      });
      setActiveModal(null);
      setNotification({ type: 'success', text: 'Cross-panel conflict resolved and audit trail updated.' });
      await loadDetail();
    } catch (err) {
      await enqueueConflictResolution({
        inspection_id: Number(inspectionId),
        conflict_id: selectedConflict.conflict_id,
        resolution: conflictResolution,
        resolved_value: conflictResolvedValue.trim() || undefined,
        reason: conflictReason.trim(),
      });
      setActiveModal(null);
      setNotification({
        type: 'warning',
        text: `Conflict resolution queued locally for sync.`,
      });
    } finally {
      setSubmitting(false);
    }
  };

  if (loading) {
    return (
      <View style={styles.container}>
        <Header title="Inspection Review" leftAction={{ icon: '←', onPress: onBack, label: 'Back' }} />
        <View style={styles.centerContainer}>
          <ActivityIndicator size="large" color={colors.netraTeal} />
          <Text style={styles.loadingText}>Fetching review details...</Text>
        </View>
      </View>
    );
  }

  if (!detail) {
    return (
      <View style={styles.container}>
        <Header title="Inspection Review" leftAction={{ icon: '←', onPress: onBack, label: 'Back' }} />
        <View style={styles.centerContainer}>
          <Text style={styles.errorText}>Inspection details could not be loaded.</Text>
          <PrimaryButton title="Retry" onPress={loadDetail} />
        </View>
      </View>
    );
  }

  // Extract scans, images, and findings
  const scanReviews = detail.scan_reviews || [];
  const assessment = detail.assessment;
  const history = detail.review_history || [];

  return (
    <View style={styles.container}>
      <Header
        title={`Review #INSP-${detail.inspection_id}`}
        subtitle={`${detail.store?.name || 'Store'} • ${detail.inspection_date ? String(detail.inspection_date).slice(0, 10) : ''}`}
        leftAction={{ icon: '←', onPress: onBack, label: 'Back' }}
      />

      {notification && (
        <View style={[styles.notificationBar, notification.type === 'error' ? styles.notifError : notification.type === 'warning' ? styles.notifWarning : styles.notifSuccess]}>
          <Text style={styles.notifText}>{notification.text}</Text>
          <Pressable onPress={() => setNotification(null)} hitSlop={8}>
            <Text style={styles.notifClose}>✕</Text>
          </Pressable>
        </View>
      )}

      <ScrollView style={styles.scroll} contentContainerStyle={styles.content}>
        {/* Overall Assessment Banner */}
        <Card padding="md" style={styles.assessmentBanner}>
          <View style={styles.rowBetween}>
            <View>
              <Text style={styles.bannerSub}>Automated Assessment Verdict</Text>
              <Text style={styles.bannerTitle}>
                {assessment?.overall_verdict?.toUpperCase() || (scanReviews[0]?.overall_result?.toUpperCase()) || 'NOT ASSESSED'}
              </Text>
            </View>
            <VerdictBadge
              result={assessment?.overall_verdict || scanReviews[0]?.overall_result || 'not_assessed'}
              size="lg"
            />
          </View>
          <View style={[styles.rowBetween, { marginTop: spacing.sm, paddingTop: spacing.xs, borderTopWidth: 1, borderTopColor: colors.borderLight }]}>
            <Text style={styles.metaLabel}>Rule-Pack: <Text style={styles.metaValue}>2026.09.v1</Text></Text>
            <Text style={styles.metaLabel}>
              Completeness: <Text style={styles.metaValue}>{assessment?.completeness || `${scanReviews[0]?.checks_assessed || 0}/${scanReviews[0]?.checks_total || 19}`}</Text>
            </Text>
          </View>
        </Card>

        {/* Action Bar for Inspection-Level Adjudication */}
        <View style={styles.actionBar}>
          <Pressable
            style={[styles.actionButton, { backgroundColor: colors.netraTeal }]}
            onPress={() => onOpenRecapture && onOpenRecapture(inspectionId, scanReviews[0]?.scan_id)}
          >
            <Text style={styles.actionBtnText}>📷 Recapture</Text>
          </Pressable>
          <Pressable
            style={[styles.actionButton, { backgroundColor: colors.pass.text }]}
            onPress={() => {
              setGeneralAction('resolve');
              setGeneralReason('');
              setResolutionNotes('');
              setActiveModal('general');
            }}
          >
            <Text style={styles.actionBtnText}>✓ Resolve</Text>
          </Pressable>
          <Pressable
            style={[styles.actionButton, { backgroundColor: colors.warning }]}
            onPress={() => {
              setGeneralAction('escalate');
              setGeneralReason('');
              setResolutionNotes('');
              setActiveModal('general');
            }}
          >
            <Text style={styles.actionBtnText}>⚠️ Escalate</Text>
          </Pressable>
          <Pressable
            style={[styles.actionButton, { backgroundColor: colors.textMuted }]}
            onPress={() => {
              setGeneralAction('dismiss');
              setGeneralReason('');
              setResolutionNotes('');
              setActiveModal('general');
            }}
          >
            <Text style={styles.actionBtnText}>✕ Dismiss</Text>
          </Pressable>
          {onOpenReport && (
            <Pressable
              style={[styles.actionButton, { backgroundColor: colors.surfaceVariant, borderWidth: 1, borderColor: colors.borderLight }]}
              onPress={() => onOpenReport(inspectionId)}
            >
              <Text style={[styles.actionBtnText, { color: colors.niyamBlue }]}>📄 Dossier</Text>
            </Pressable>
          )}
        </View>

        {/* Scan Reviews Loop */}
        {scanReviews.map((sr, sIdx) => (
          <View key={sr.scan_id || sIdx} style={styles.scanSection}>
            <View style={styles.sectionTitleRow}>
              <Text style={styles.sectionTitle}>
                PACKAGE #{sIdx + 1}: {sr.commodity_generic || 'Pre-packaged Commodity'}
              </Text>
              <Text style={styles.scanResultTag}>{sr.overall_result?.toUpperCase()}</Text>
            </View>

            {/* Reasons for review */}
            {sr.reasons && sr.reasons.length > 0 && (
              <Card padding="sm" style={styles.reasonsCard}>
                <Text style={styles.reasonsHeading}>Why this package requires review:</Text>
                {sr.reasons.map((r, rIdx) => (
                  <View key={rIdx} style={styles.reasonLine}>
                    <Text style={styles.bullet}>•</Text>
                    <Text style={styles.reasonText}>{r.detail}</Text>
                  </View>
                ))}
              </Card>
            )}

            {/* Findings Breakdown */}
            <Text style={styles.subSectionTitle}>STATUTORY FINDINGS ({sr.findings?.length || 0})</Text>
            {(sr.findings || []).map((f) => {
              const hasHumanVerdict = !!f.human_verdict;
              const isOverridden = hasHumanVerdict && f.human_verdict !== f.engine_verdict;
              const needsReview = f.needs_review || f.engine_verdict === 'not_assessed' || f.engine_verdict === 'fail';

              return (
                <Card key={f.finding_id || f.check_id} padding="md" style={styles.findingCard}>
                  <View style={styles.rowBetween}>
                    <View style={{ flex: 1 }}>
                      <Text style={styles.checkId}>{f.check_id}: {f.title || `Check ${f.check_id}`}</Text>
                      <Text style={styles.citationText}>{f.citation || f.rule || 'Rule 6(1)'}</Text>
                    </View>
                    <View style={{ alignItems: 'flex-end', gap: 4 }}>
                      <VerdictBadge result={f.effective_verdict || f.verdict || 'not_assessed'} size="sm" />
                      {f.confidence != null && (
                        <Text style={styles.confidenceText}>Conf: {Math.round(f.confidence * 100)}%</Text>
                      )}
                    </View>
                  </View>

                  {/* Dual Verdict Display: Automated vs Officer */}
                  <View style={styles.dualVerdictBox}>
                    <View style={styles.verdictCol}>
                      <Text style={styles.verdictLabel}>Automated Result</Text>
                      <Text style={styles.verdictVal}>{f.engine_verdict?.toUpperCase() || f.verdict?.toUpperCase()}</Text>
                    </View>
                    <View style={styles.verdictDivider} />
                    <View style={styles.verdictCol}>
                      <Text style={styles.verdictLabel}>Officer Decision</Text>
                      <Text style={[styles.verdictVal, hasHumanVerdict ? { color: colors.niyamBlue, fontWeight: '800' } : { color: colors.textMuted }]}>
                        {hasHumanVerdict ? f.human_verdict.toUpperCase() : 'PENDING'}
                      </Text>
                    </View>
                  </View>

                  {/* Remediation guidance if failed */}
                  {f.remediation && (
                    <Text style={styles.remediationText}>💡 {f.remediation}</Text>
                  )}

                  {/* Finding-level Adjudication buttons */}
                  <View style={styles.findingActions}>
                    <Pressable
                      style={[styles.smallBtn, { backgroundColor: colors.pass.fill, borderColor: colors.pass.border }]}
                      onPress={() => handleAcceptFinding(f)}
                      disabled={submitting}
                    >
                      <Text style={[styles.smallBtnText, { color: colors.pass.text }]}>✓ Confirm Engine</Text>
                    </Pressable>
                    <Pressable
                      style={[styles.smallBtn, { backgroundColor: colors.info.fill, borderColor: colors.info.border }]}
                      onPress={() => openOverrideModal(f)}
                      disabled={submitting}
                    >
                      <Text style={[styles.smallBtnText, { color: colors.info.text }]}>✎ Override</Text>
                    </Pressable>
                    <Pressable
                      style={[styles.smallBtn, { backgroundColor: '#F0FDFA', borderColor: colors.netraTeal }]}
                      onPress={() => onOpenRecapture && onOpenRecapture(inspectionId, sr.scan_id)}
                      disabled={submitting}
                    >
                      <Text style={[styles.smallBtnText, { color: colors.netraTeal }]}>📷 Recapture</Text>
                    </Pressable>
                  </View>
                </Card>
              );
            })}
          </View>
        ))}

        {/* Audit Trail Section */}
        {history.length > 0 && (
          <View style={styles.auditSection}>
            <Text style={styles.subSectionTitle}>AUDIT LOG ({history.length} EVENTS)</Text>
            {history.map((h, hIdx) => (
              <Card key={h.seq || hIdx} padding="sm" style={styles.auditCard}>
                <View style={styles.rowBetween}>
                  <Text style={styles.auditAction}>{h.action?.toUpperCase()}</Text>
                  <Text style={styles.auditTime}>{h.timestamp ? String(h.timestamp).slice(0, 19).replace('T', ' ') : ''}</Text>
                </View>
                {h.reason && <Text style={styles.auditReason}>Reason: {h.reason}</Text>}
                {h.new_value && <Text style={styles.auditValue}>State: {h.new_value}</Text>}
              </Card>
            ))}
          </View>
        )}
      </ScrollView>

      {/* --- OVERRIDE MODAL --- */}
      {activeModal === 'override' && selectedFinding && (
        <View style={styles.modalOverlay}>
          <Card padding="lg" style={styles.modalCard}>
            <Text style={styles.modalTitle}>Override Finding: {selectedFinding.check_id}</Text>
            <Text style={styles.modalSub}>
              Engine evaluated: {selectedFinding.engine_verdict?.toUpperCase()}. Officer override will be recorded in the audit trail without modifying the engine verdict.
            </Text>

            <Text style={styles.formLabel}>Select Human Verdict:</Text>
            <View style={styles.verdictSelector}>
              {['pass', 'fail', 'not_assessed'].map((v) => (
                <Pressable
                  key={v}
                  style={[styles.verdictOption, overrideVerdict === v && styles.verdictOptionActive]}
                  onPress={() => setOverrideVerdict(v)}
                >
                  <Text style={[styles.verdictOptionText, overrideVerdict === v && styles.verdictOptionTextActive]}>
                    {v.toUpperCase()}
                  </Text>
                </Pressable>
              ))}
            </View>

            <Text style={styles.formLabel}>Mandatory Officer Justification (min 10 chars):</Text>
            <TextInput
              style={styles.modalInput}
              multiline
              numberOfLines={3}
              placeholder="State statutory legal rationale for this override..."
              placeholderTextColor={colors.placeholder}
              value={overrideReason}
              onChangeText={setOverrideReason}
            />

            <View style={styles.modalBtnRow}>
              <Pressable style={styles.cancelBtn} onPress={() => setActiveModal(null)}>
                <Text style={styles.cancelBtnText}>Cancel</Text>
              </Pressable>
              <PrimaryButton
                title={submitting ? 'Submitting...' : 'Confirm Override'}
                onPress={handleSubmitOverride}
                disabled={submitting || overrideReason.trim().length < 10}
              />
            </View>
          </Card>
        </View>
      )}

      {/* --- GENERAL ADJUDICATION MODAL --- */}
      {activeModal === 'general' && (
        <View style={styles.modalOverlay}>
          <Card padding="lg" style={styles.modalCard}>
            <Text style={styles.modalTitle}>
              {generalAction === 'resolve' ? 'Resolve Inspection Review' : generalAction === 'escalate' ? 'Escalate for Senior Review' : 'Dismiss Review Flag'}
            </Text>

            <Text style={styles.formLabel}>Mandatory Officer Reason (min 10 chars):</Text>
            <TextInput
              style={styles.modalInput}
              multiline
              numberOfLines={3}
              placeholder="Explain the adjudication decision..."
              placeholderTextColor={colors.placeholder}
              value={generalReason}
              onChangeText={setGeneralReason}
            />

            <Text style={styles.formLabel}>Field / Resolution Notes (Optional):</Text>
            <TextInput
              style={styles.modalInput}
              multiline
              numberOfLines={2}
              placeholder="Additional notes for inspection record..."
              placeholderTextColor={colors.placeholder}
              value={resolutionNotes}
              onChangeText={setResolutionNotes}
            />

            <View style={styles.modalBtnRow}>
              <Pressable style={styles.cancelBtn} onPress={() => setActiveModal(null)}>
                <Text style={styles.cancelBtnText}>Cancel</Text>
              </Pressable>
              <PrimaryButton
                title={submitting ? 'Applying...' : 'Confirm Action'}
                onPress={handleSubmitGeneralAction}
                disabled={submitting || generalReason.trim().length < 10}
              />
            </View>
          </Card>
        </View>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: colors.background,
  },
  centerContainer: {
    flex: 1,
    alignItems: 'center',
    justifyContent: 'center',
    padding: spacing.xl,
  },
  loadingText: {
    marginTop: spacing.md,
    fontSize: 13,
    color: colors.textMuted,
  },
  errorText: {
    fontSize: 14,
    color: colors.error,
    marginBottom: spacing.md,
    textAlign: 'center',
  },
  notificationBar: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.sm,
  },
  notifSuccess: {
    backgroundColor: '#ECFDF5',
    borderColor: '#A7F3D0',
    borderBottomWidth: 1,
  },
  notifWarning: {
    backgroundColor: '#FFFBEB',
    borderColor: '#FDE68A',
    borderBottomWidth: 1,
  },
  notifError: {
    backgroundColor: '#FEF2F2',
    borderColor: '#FECACA',
    borderBottomWidth: 1,
  },
  notifText: {
    flex: 1,
    fontSize: 12,
    color: colors.text,
    fontWeight: '600',
  },
  notifClose: {
    fontSize: 14,
    color: colors.textMuted,
    paddingHorizontal: 8,
  },
  scroll: {
    flex: 1,
  },
  content: {
    padding: spacing.md,
    paddingBottom: spacing.xxxl + 40,
    gap: spacing.md,
  },
  assessmentBanner: {
    backgroundColor: colors.surface,
    borderRadius: radius.lg,
    ...shadows.sm,
  },
  bannerSub: {
    fontSize: 11,
    fontWeight: '700',
    color: colors.textMuted,
    letterSpacing: 0.5,
  },
  bannerTitle: {
    fontSize: 20,
    fontWeight: '800',
    color: colors.niyamBlue,
    marginTop: 2,
  },
  metaLabel: {
    fontSize: 11,
    color: colors.textMuted,
  },
  metaValue: {
    fontWeight: '700',
    color: colors.textSecondary,
  },
  actionBar: {
    flexDirection: 'row',
    gap: spacing.sm,
  },
  actionButton: {
    flex: 1,
    paddingVertical: 10,
    borderRadius: radius.md,
    alignItems: 'center',
    justifyContent: 'center',
  },
  actionBtnText: {
    color: colors.white,
    fontSize: 12,
    fontWeight: '700',
  },
  scanSection: {
    gap: spacing.sm,
    marginTop: spacing.sm,
  },
  sectionTitleRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
  },
  sectionTitle: {
    fontSize: 12,
    fontWeight: '800',
    color: colors.niyamBlue,
    letterSpacing: 0.8,
  },
  scanResultTag: {
    fontSize: 11,
    fontWeight: '800',
    color: colors.netraTeal,
  },
  reasonsCard: {
    backgroundColor: '#FFFBEB',
    borderColor: '#FDE68A',
    borderWidth: 1,
  },
  reasonsHeading: {
    fontSize: 11,
    fontWeight: '700',
    color: colors.warning,
    marginBottom: 4,
  },
  reasonLine: {
    flexDirection: 'row',
    alignItems: 'flex-start',
  },
  bullet: {
    fontSize: 12,
    color: colors.warning,
    marginRight: 4,
  },
  reasonText: {
    fontSize: 11,
    color: colors.textSecondary,
    flex: 1,
    lineHeight: 15,
  },
  subSectionTitle: {
    fontSize: 11,
    fontWeight: '700',
    color: colors.textMuted,
    letterSpacing: 0.8,
    marginTop: spacing.sm,
  },
  findingCard: {
    backgroundColor: colors.surface,
    borderRadius: radius.md,
    ...shadows.sm,
  },
  checkId: {
    fontSize: 13,
    fontWeight: '700',
    color: colors.text,
  },
  citationText: {
    fontSize: 11,
    color: colors.textMuted,
    marginTop: 2,
  },
  confidenceText: {
    fontSize: 10,
    color: colors.textSecondary,
    fontWeight: '600',
  },
  dualVerdictBox: {
    flexDirection: 'row',
    backgroundColor: '#F8FAFC',
    borderRadius: radius.sm,
    padding: spacing.xs,
    marginTop: spacing.sm,
  },
  verdictCol: {
    flex: 1,
    alignItems: 'center',
  },
  verdictDivider: {
    width: 1,
    backgroundColor: colors.borderLight,
  },
  verdictLabel: {
    fontSize: 9,
    fontWeight: '600',
    color: colors.textMuted,
  },
  verdictVal: {
    fontSize: 12,
    fontWeight: '700',
    color: colors.textSecondary,
    marginTop: 1,
  },
  remediationText: {
    fontSize: 11,
    color: colors.netraTeal,
    marginTop: 6,
    lineHeight: 15,
  },
  findingActions: {
    flexDirection: 'row',
    gap: spacing.sm,
    marginTop: spacing.sm,
    paddingTop: spacing.xs,
    borderTopWidth: 1,
    borderTopColor: colors.borderLight,
  },
  smallBtn: {
    flex: 1,
    paddingVertical: 6,
    borderRadius: radius.sm,
    borderWidth: 1,
    alignItems: 'center',
    justifyContent: 'center',
  },
  smallBtnText: {
    fontSize: 11,
    fontWeight: '700',
  },
  auditSection: {
    gap: spacing.xs,
    marginTop: spacing.md,
  },
  auditCard: {
    backgroundColor: '#F8FAFC',
  },
  auditAction: {
    fontSize: 11,
    fontWeight: '800',
    color: colors.niyamBlue,
  },
  auditTime: {
    fontSize: 10,
    color: colors.textMuted,
  },
  auditReason: {
    fontSize: 11,
    color: colors.textSecondary,
    marginTop: 2,
  },
  auditValue: {
    fontSize: 10,
    color: colors.textMuted,
    marginTop: 1,
  },
  modalOverlay: {
    position: 'absolute',
    top: 0,
    bottom: 0,
    left: 0,
    right: 0,
    backgroundColor: 'rgba(0, 0, 0, 0.5)',
    justifyContent: 'center',
    alignItems: 'center',
    padding: spacing.md,
  },
  modalCard: {
    width: '100%',
    backgroundColor: colors.surface,
    borderRadius: radius.xl,
    ...shadows.lg,
  },
  modalTitle: {
    fontSize: 16,
    fontWeight: '800',
    color: colors.niyamBlue,
    marginBottom: 4,
  },
  modalSub: {
    fontSize: 11,
    color: colors.textMuted,
    marginBottom: spacing.md,
    lineHeight: 15,
  },
  formLabel: {
    fontSize: 11,
    fontWeight: '700',
    color: colors.textSecondary,
    marginBottom: 4,
    marginTop: 6,
  },
  verdictSelector: {
    flexDirection: 'row',
    gap: spacing.xs,
    marginBottom: spacing.sm,
  },
  verdictOption: {
    flex: 1,
    paddingVertical: 8,
    borderRadius: radius.sm,
    borderWidth: 1,
    borderColor: colors.border,
    alignItems: 'center',
  },
  verdictOptionActive: {
    backgroundColor: colors.niyamBlue,
    borderColor: colors.niyamBlue,
  },
  verdictOptionText: {
    fontSize: 11,
    fontWeight: '700',
    color: colors.textSecondary,
  },
  verdictOptionTextActive: {
    color: colors.white,
  },
  modalInput: {
    backgroundColor: '#F8FAFC',
    borderColor: colors.border,
    borderWidth: 1,
    borderRadius: radius.md,
    padding: spacing.sm,
    fontSize: 12,
    color: colors.text,
    textAlignVertical: 'top',
    marginBottom: spacing.sm,
  },
  modalBtnRow: {
    flexDirection: 'row',
    justifyContent: 'flex-end',
    alignItems: 'center',
    gap: spacing.md,
    marginTop: spacing.md,
  },
  cancelBtn: {
    paddingHorizontal: 12,
    paddingVertical: 8,
  },
  cancelBtnText: {
    fontSize: 12,
    fontWeight: '600',
    color: colors.textMuted,
  },
  rowBetween: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
  },
});
