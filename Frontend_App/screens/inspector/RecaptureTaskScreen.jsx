// screens/inspector/RecaptureTaskScreen.jsx — Phase 6B: Smart Recapture Camera Workflow
import React, { useCallback, useEffect, useState, useRef } from 'react';
import {
  View,
  Text,
  ScrollView,
  Pressable,
  ActivityIndicator,
  TextInput,
  Alert,
  StyleSheet,
} from 'react-native';
import { CameraView, useCameraPermissions } from 'expo-camera';
import { colors, spacing, typography, radius, shadows } from '../../theme';
import Header from '../../components/Header';
import Card from '../../components/Card';
import PrimaryButton from '../../components/PrimaryButton';
import {
  fetchCaptureTasks,
  fulfillCaptureTask,
  triggerReassessment,
} from '../../api/recapture';
import { uploadScanImage } from '../../api/inspections';
import {
  persistOriginalCapture,
  createAnalysisImage,
} from '../../offline/evidenceManager';
import { assessCaptureQuality } from '../../offline/qualityGate';
import {
  enqueueRecaptureTaskFulfillment,
  enqueueReassessmentTrigger,
  cacheRecaptureTasks,
  loadCachedRecaptureTasks,
  invalidateReviewQueue,
} from '../../offline/queue';

export default function RecaptureTaskScreen({ inspectionId, scanId, onBack, onComplete }) {
  const [tasks, setTasks] = useState([]);
  const [summary, setSummary] = useState({ total: 0, pending: 0, fulfilled: 0, skipped: 0 });
  const [loading, setLoading] = useState(true);
  const [activeTask, setActiveTask] = useState(null); // Task currently being captured
  const [cameraMode, setCameraMode] = useState(false);
  const [capturing, setCapturing] = useState(false);
  const [processingStatus, setProcessingStatus] = useState(null); // 'PERSISTING' | 'ANALYZING' | 'QUALITY_GATE' | 'UPLOADING' | 'REASSESSING'
  const [attemptsByTask, setAttemptsByTask] = useState({}); // { [taskId]: number }
  const [skipModalTask, setSkipModalTask] = useState(null);
  const [skipReason, setSkipReason] = useState('');
  const [submittingSkip, setSubmittingSkip] = useState(false);
  const [notification, setNotification] = useState(null);
  const [isOffline, setIsOffline] = useState(false);

  // Camera permissions and ref
  const [permission, requestPermission] = useCameraPermissions();
  const [cameraReady, setCameraReady] = useState(false);
  const cameraRef = useRef(null);

  const loadTasks = useCallback(async () => {
    setLoading(true);
    try {
      const data = await fetchCaptureTasks(inspectionId);
      const taskList = Array.isArray(data?.tasks) ? data.tasks : [];
      setTasks(taskList);
      setSummary({
        total: data?.total_tasks ?? taskList.length,
        pending: data?.pending ?? taskList.filter((t) => t.status === 'pending').length,
        fulfilled: data?.fulfilled ?? taskList.filter((t) => t.status === 'fulfilled').length,
        skipped: data?.skipped ?? taskList.filter((t) => t.status === 'skipped').length,
      });
      setIsOffline(false);
      await cacheRecaptureTasks(inspectionId, taskList);
    } catch (err) {
      // Offline fallback: load cached tasks
      const cached = await loadCachedRecaptureTasks(inspectionId);
      if (cached && cached.length > 0) {
        setTasks(cached);
        setSummary({
          total: cached.length,
          pending: cached.filter((t) => t.status === 'pending').length,
          fulfilled: cached.filter((t) => t.status === 'fulfilled').length,
          skipped: cached.filter((t) => t.status === 'skipped').length,
        });
      }
      setIsOffline(true);
      setNotification({
        type: 'warning',
        text: 'Offline mode: showing locally cached capture tasks.',
      });
    } finally {
      setLoading(false);
    }
  }, [inspectionId]);

  useEffect(() => {
    loadTasks();
  }, [loadTasks]);

  // Launch camera for a specific task
  const handleStartCapture = async (task) => {
    if (!permission?.granted) {
      const res = await requestPermission();
      if (!res.granted) {
        Alert.alert('Camera Permission Required', 'Camera access is required to capture evidence photos.');
        return;
      }
    }
    setActiveTask(task);
    setCameraMode(true);
    setCameraReady(false);
  };

  // Execute Photo Capture and Real Preservation Pipeline
  const handleTakePicture = async () => {
    if (!cameraRef.current || !cameraReady || capturing) return;

    setCapturing(true);
    const currentTaskId = activeTask.request_id;
    const currentAttempt = (attemptsByTask[currentTaskId] || 0) + 1;
    setAttemptsByTask((prev) => ({ ...prev, [currentTaskId]: currentAttempt }));

    try {
      // Step A: Camera capture
      setProcessingStatus('CAPTURING');
      const photo = await cameraRef.current.takePictureAsync({ quality: 0.8 });

      if (!photo?.uri) {
        throw new Error('Camera produced no image URI.');
      }

      // Step B: Persist original immutable camera capture
      setProcessingStatus('PERSISTING ORIGINAL EVIDENCE');
      const evidenceRecord = await persistOriginalCapture(photo, {
        panel: activeTask.target_panel || 'other',
        inspectionId: Number(inspectionId),
        scanId: activeTask.scan_id || scanId || null,
        taskId: currentTaskId,
        attemptNumber: currentAttempt,
      });

      // Step C: Create separate derived analysis image (1600px JPEG)
      setProcessingStatus('CREATING ANALYSIS DERIVATIVE');
      const processedRecord = await createAnalysisImage(evidenceRecord);

      // Step D: Evaluate Fast Image Quality Gate
      setProcessingStatus('EVALUATING QUALITY GATE');
      const qualityResult = await assessCaptureQuality(processedRecord);

      if (qualityResult.decision === 'RETAKE_REQUIRED') {
        // Quality failure: do NOT upload as successful evidence
        Alert.alert(
          `Quality Gate: Retake Required (Attempt ${currentAttempt})`,
          qualityResult.primary_guidance || 'Image quality insufficient for statutory verification. Please retake.'
        );
        setCapturing(false);
        setProcessingStatus(null);
        return;
      }

      // Step E: Upload evidence to server (or queue offline)
      setProcessingStatus('UPLOADING EVIDENCE');
      let uploadedImageId = null;
      const targetScanId = activeTask.scan_id || scanId;

      try {
        if (targetScanId) {
          const uploadRes = await uploadScanImage(
            targetScanId,
            activeTask.target_panel || 'other',
            processedRecord.analysis_uri || processedRecord.original_uri
          );
          uploadedImageId = uploadRes?.id || uploadRes?.image_id || null;
        }

        // Step F: Fulfill task on server
        setProcessingStatus('FULFILLING CAPTURE TASK');
        await fulfillCaptureTask(currentTaskId, {
          inspection_id: Number(inspectionId),
          scan_image_id: uploadedImageId,
          notes: `Fulfilled on attempt ${currentAttempt} via smart camera recapture.`,
        });

        // Step G: Trigger statutory reassessment
        setProcessingStatus('REASSESSING RULES');
        if (targetScanId) {
          await triggerReassessment({
            inspection_id: Number(inspectionId),
            scan_ids: [targetScanId],
          });
        }

        await invalidateReviewQueue(null, 'recapture_reassessment');
        setNotification({
          type: 'success',
          text: `Capture task fulfilled and scan reassessed (Attempt ${currentAttempt}).`,
        });
      } catch (uploadErr) {
        // Offline handling: queue fulfillment and reassessment locally
        await enqueueRecaptureTaskFulfillment({
          taskId: currentTaskId,
          inspectionId: Number(inspectionId),
          scanImageId: null,
          notes: `Fulfilled offline on attempt ${currentAttempt}`,
        });
        if (targetScanId) {
          await enqueueReassessmentTrigger({
            inspectionId: Number(inspectionId),
            scanIds: [targetScanId],
          });
        }
        setNotification({
          type: 'warning',
          text: `Recapture evidence preserved locally. Queued for synchronization.`,
        });
      }

      // Exit camera and refresh tasks
      setCameraMode(false);
      setActiveTask(null);
      await loadTasks();
      if (onComplete) onComplete();
    } catch (err) {
      Alert.alert('Capture Error', err?.message || 'Failed to process evidence capture.');
    } finally {
      setCapturing(false);
      setProcessingStatus(null);
    }
  };

  // Skip task handler
  const handleOpenSkipModal = (task) => {
    setSkipModalTask(task);
    setSkipReason('');
  };

  const handleSubmitSkip = async () => {
    if (!skipReason || skipReason.trim().length < 10) {
      Alert.alert('Reason Required', 'Mandatory skip justification must be at least 10 characters.');
      return;
    }

    setSubmittingSkip(true);
    try {
      await fulfillCaptureTask(skipModalTask.request_id, {
        inspection_id: Number(inspectionId),
        skip_reason: skipReason.trim(),
      });
      await invalidateReviewQueue(null, 'recapture_skip');
      setSkipModalTask(null);
      setNotification({
        type: 'success',
        text: `Task marked as skipped with statutory justification.`,
      });
      await loadTasks();
    } catch (err) {
      // Offline fallback
      await enqueueRecaptureTaskFulfillment({
        taskId: skipModalTask.request_id,
        inspectionId: Number(inspectionId),
        skipReason: skipReason.trim(),
      });
      setSkipModalTask(null);
      setNotification({
        type: 'warning',
        text: `Skip action queued locally for sync.`,
      });
    } finally {
      setSubmittingSkip(false);
    }
  };

  // =========================================================================
  // CAMERA VIEW (Camera Only)
  // =========================================================================
  if (cameraMode && activeTask) {
    const isCalibrationTask =
      activeTask.target_panel === 'calibration' ||
      activeTask.reason?.toLowerCase().includes('calibration') ||
      activeTask.reason?.toLowerCase().includes('scale') ||
      activeTask.reason?.toLowerCase().includes('chk12');

    return (
      <View style={styles.cameraContainer}>
        <CameraView
          style={StyleSheet.absoluteFillObject}
          facing="back"
          ref={cameraRef}
          onCameraReady={() => setCameraReady(true)}
        >
          {/* Overlay UI */}
          <View style={styles.cameraOverlay}>
            {/* Top Bar with Task Guidance */}
            <View style={styles.cameraTopBar}>
              <Pressable
                style={styles.closeBtn}
                onPress={() => {
                  setCameraMode(false);
                  setActiveTask(null);
                }}
              >
                <Text style={styles.closeBtnText}>✕ Cancel</Text>
              </Pressable>
              <View style={styles.taskTargetBadge}>
                <Text style={styles.taskTargetText}>
                  TARGET: {activeTask.target_panel?.toUpperCase()}
                </Text>
              </View>
            </View>

            {/* Framing Box / Reticle */}
            <View style={styles.reticleContainer}>
              <View style={styles.reticleBox}>
                <View style={[styles.corner, styles.cornerTL]} />
                <View style={[styles.corner, styles.cornerTR]} />
                <View style={[styles.corner, styles.cornerBL]} />
                <View style={[styles.corner, styles.cornerBR]} />
                <Text style={styles.reticleInstruction}>
                  {activeTask.suggested_action || `Align ${activeTask.target_panel} panel inside the frame`}
                </Text>
              </View>

              {/* Calibration Notice if applicable */}
              {isCalibrationTask && (
                <View style={styles.calibrationBanner}>
                  <Text style={styles.calibrationTitle}>⚖️ CALIBRATION REFERENCE REQUIRED</Text>
                  <Text style={styles.calibrationText}>
                    Place an ID-1 card (85.6 × 53.98 mm) or ₹5 coin (23 mm) coplanar with the package panel. Manually typed dimensions are prohibited under Legal Metrology Rules.
                  </Text>
                </View>
              )}
            </View>

            {/* Bottom Controls */}
            <View style={styles.cameraBottomBar}>
              {processingStatus ? (
                <View style={styles.processingIndicator}>
                  <ActivityIndicator size="small" color={colors.white} />
                  <Text style={styles.processingText}>{processingStatus}...</Text>
                </View>
              ) : (
                <View style={styles.shutterRow}>
                  <Pressable
                    style={[styles.shutterButton, !cameraReady && styles.shutterDisabled]}
                    onPress={handleTakePicture}
                    disabled={!cameraReady || capturing}
                  >
                    <View style={styles.shutterInner} />
                  </Pressable>
                  <Text style={styles.attemptText}>
                    Attempt #{(attemptsByTask[activeTask.request_id] || 0) + 1}
                  </Text>
                </View>
              )}
            </View>
          </View>
        </CameraView>
      </View>
    );
  }

  // =========================================================================
  // TASK LIST VIEW
  // =========================================================================
  return (
    <View style={styles.container}>
      <Header
        title="Smart Recapture Tasks"
        subtitle={`#INSP-${inspectionId} • ${summary.pending} pending capture(s)`}
        leftAction={{ icon: '←', onPress: onBack, label: 'Back' }}
      />

      {notification && (
        <View style={[styles.notificationBar, notification.type === 'warning' ? styles.notifWarning : styles.notifSuccess]}>
          <Text style={styles.notifText}>{notification.text}</Text>
          <Pressable onPress={() => setNotification(null)} hitSlop={8}>
            <Text style={styles.notifClose}>✕</Text>
          </Pressable>
        </View>
      )}

      {/* Task Summary Banner */}
      <View style={styles.summaryBar}>
        <View style={styles.summaryItem}>
          <Text style={styles.summaryNum}>{summary.total}</Text>
          <Text style={styles.summaryLbl}>TOTAL</Text>
        </View>
        <View style={styles.summaryDivider} />
        <View style={styles.summaryItem}>
          <Text style={[styles.summaryNum, { color: colors.warning }]}>{summary.pending}</Text>
          <Text style={styles.summaryLbl}>PENDING</Text>
        </View>
        <View style={styles.summaryDivider} />
        <View style={styles.summaryItem}>
          <Text style={[styles.summaryNum, { color: colors.pass.text }]}>{summary.fulfilled}</Text>
          <Text style={styles.summaryLbl}>FULFILLED</Text>
        </View>
        <View style={styles.summaryDivider} />
        <View style={styles.summaryItem}>
          <Text style={[styles.summaryNum, { color: colors.textMuted }]}>{summary.skipped}</Text>
          <Text style={styles.summaryLbl}>SKIPPED</Text>
        </View>
      </View>

      <ScrollView style={styles.scroll} contentContainerStyle={styles.content}>
        {loading ? (
          <View style={styles.centerContainer}>
            <ActivityIndicator size="large" color={colors.netraTeal} />
            <Text style={styles.loadingText}>Loading statutory capture tasks...</Text>
          </View>
        ) : tasks.length === 0 ? (
          <Card padding="lg" style={styles.emptyCard}>
            <Text style={styles.emptyIcon}>✓</Text>
            <Text style={styles.emptyTitle}>All Evidence Complete</Text>
            <Text style={styles.emptySub}>
              No additional panel captures or calibration references are required for this inspection.
            </Text>
          </Card>
        ) : (
          tasks.map((task) => {
            const isFulfilled = task.status === 'fulfilled';
            const isSkipped = task.status === 'skipped';
            const attempts = attemptsByTask[task.request_id] || 0;
            const isRepeatedFailure = attempts >= 3;

            return (
              <Card key={task.request_id} padding="md" style={styles.taskCard}>
                {/* Header: Panel, Priority, Status */}
                <View style={styles.rowBetween}>
                  <View style={styles.rowAlign}>
                    <View style={styles.panelBadge}>
                      <Text style={styles.panelBadgeText}>{task.target_panel?.toUpperCase()}</Text>
                    </View>
                    <View style={[styles.priorityBadge, task.priority === 'high' ? styles.pHigh : styles.pMed]}>
                      <Text style={[styles.priorityText, task.priority === 'high' ? styles.pHighText : styles.pMedText]}>
                        {task.priority?.toUpperCase()}
                      </Text>
                    </View>
                  </View>
                  <View style={[styles.statusBadge, isFulfilled ? styles.sFulfilled : isSkipped ? styles.sSkipped : styles.sPending]}>
                    <Text style={[styles.statusText, isFulfilled ? styles.sFulfilledText : isSkipped ? styles.sSkippedText : styles.sPendingText]}>
                      {task.status?.toUpperCase() || 'PENDING'}
                    </Text>
                  </View>
                </View>

                {/* Reason & Guidance */}
                <Text style={styles.taskReason}>{task.reason}</Text>
                <Text style={styles.taskSuggested}>💡 {task.suggested_action}</Text>

                {/* Expected Evidence */}
                {task.expected_evidence && (
                  <View style={styles.evidenceReqBox}>
                    <Text style={styles.evidenceReqTitle}>Expected Statutory Evidence:</Text>
                    <Text style={styles.evidenceReqText}>{task.expected_evidence}</Text>
                  </View>
                )}

                {/* Repeated Attempt Warning */}
                {isRepeatedFailure && !isFulfilled && !isSkipped && (
                  <View style={styles.loopWarningBox}>
                    <Text style={styles.loopWarningTitle}>⚠️ MANUAL OFFICER REVIEW REQUIRED</Text>
                    <Text style={styles.loopWarningText}>
                      Repeated capture attempts ({attempts}) have not resolved this assessment gap. Please skip with explanation or resolve through officer adjudication.
                    </Text>
                  </View>
                )}

                {/* Action Buttons */}
                {!isFulfilled && !isSkipped && (
                  <View style={styles.taskActionRow}>
                    <Pressable
                      style={styles.skipBtn}
                      onPress={() => handleOpenSkipModal(task)}
                    >
                      <Text style={styles.skipBtnText}>Skip Task</Text>
                    </Pressable>
                    <PrimaryButton
                      title="📷 Capture Evidence"
                      onPress={() => handleStartCapture(task)}
                    />
                  </View>
                )}
              </Card>
            );
          })
        )}
      </ScrollView>

      {/* --- SKIP MODAL --- */}
      {skipModalTask && (
        <View style={styles.modalOverlay}>
          <Card padding="lg" style={styles.modalCard}>
            <Text style={styles.modalTitle}>Skip Capture Task</Text>
            <Text style={styles.modalSub}>
              Skipping a capture request requires a statutory explanation (e.g. package damaged, panel missing from retail sample).
            </Text>

            <Text style={styles.formLabel}>Mandatory Officer Justification (min 10 chars):</Text>
            <TextInput
              style={styles.modalInput}
              multiline
              numberOfLines={3}
              placeholder="State rationale for skipping this capture..."
              placeholderTextColor={colors.placeholder}
              value={skipReason}
              onChangeText={setSkipReason}
            />

            <View style={styles.modalBtnRow}>
              <Pressable style={styles.cancelBtn} onPress={() => setSkipModalTask(null)}>
                <Text style={styles.cancelBtnText}>Cancel</Text>
              </Pressable>
              <PrimaryButton
                title={submittingSkip ? 'Submitting...' : 'Confirm Skip'}
                onPress={handleSubmitSkip}
                disabled={submittingSkip || skipReason.trim().length < 10}
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
  cameraContainer: {
    flex: 1,
    backgroundColor: '#000000',
  },
  cameraOverlay: {
    flex: 1,
    justifyContent: 'space-between',
    padding: spacing.lg,
  },
  cameraTopBar: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    paddingTop: 30,
  },
  closeBtn: {
    backgroundColor: 'rgba(0, 0, 0, 0.6)',
    paddingHorizontal: 12,
    paddingVertical: 6,
    borderRadius: radius.md,
  },
  closeBtnText: {
    color: colors.white,
    fontSize: 12,
    fontWeight: '700',
  },
  taskTargetBadge: {
    backgroundColor: colors.netraTeal,
    paddingHorizontal: 10,
    paddingVertical: 4,
    borderRadius: radius.sm,
  },
  taskTargetText: {
    color: colors.white,
    fontSize: 11,
    fontWeight: '800',
    letterSpacing: 0.5,
  },
  reticleContainer: {
    alignItems: 'center',
    justifyContent: 'center',
  },
  reticleBox: {
    width: '90%',
    height: 320,
    borderWidth: 1,
    borderColor: 'rgba(255, 255, 255, 0.4)',
    borderRadius: radius.lg,
    justifyContent: 'flex-end',
    alignItems: 'center',
    padding: spacing.md,
    position: 'relative',
  },
  corner: {
    position: 'absolute',
    width: 20,
    height: 20,
    borderColor: colors.saffron,
  },
  cornerTL: { top: -1, left: -1, borderTopWidth: 3, borderLeftWidth: 3 },
  cornerTR: { top: -1, right: -1, borderTopWidth: 3, borderRightWidth: 3 },
  cornerBL: { bottom: -1, left: -1, borderBottomWidth: 3, borderLeftWidth: 3 },
  cornerBR: { bottom: -1, right: -1, borderBottomWidth: 3, borderRightWidth: 3 },
  reticleInstruction: {
    color: colors.white,
    fontSize: 12,
    fontWeight: '600',
    backgroundColor: 'rgba(0, 0, 0, 0.7)',
    paddingHorizontal: 12,
    paddingVertical: 6,
    borderRadius: radius.sm,
    textAlign: 'center',
  },
  calibrationBanner: {
    backgroundColor: 'rgba(15, 42, 68, 0.85)',
    borderColor: colors.saffron,
    borderWidth: 1,
    borderRadius: radius.md,
    padding: spacing.sm,
    marginTop: spacing.md,
    width: '90%',
  },
  calibrationTitle: {
    color: colors.saffron,
    fontSize: 11,
    fontWeight: '800',
    marginBottom: 2,
  },
  calibrationText: {
    color: colors.white,
    fontSize: 10,
    lineHeight: 14,
  },
  cameraBottomBar: {
    paddingBottom: 40,
    alignItems: 'center',
  },
  shutterRow: {
    alignItems: 'center',
  },
  shutterButton: {
    width: 72,
    height: 72,
    borderRadius: 36,
    backgroundColor: 'rgba(255, 255, 255, 0.3)',
    alignItems: 'center',
    justifyContent: 'center',
  },
  shutterInner: {
    width: 58,
    height: 58,
    borderRadius: 29,
    backgroundColor: colors.white,
  },
  shutterDisabled: {
    opacity: 0.5,
  },
  attemptText: {
    color: colors.white,
    fontSize: 11,
    fontWeight: '600',
    marginTop: 6,
  },
  processingIndicator: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: 'rgba(0, 0, 0, 0.8)',
    paddingHorizontal: 16,
    paddingVertical: 10,
    borderRadius: radius.lg,
    gap: spacing.sm,
  },
  processingText: {
    color: colors.white,
    fontSize: 12,
    fontWeight: '700',
  },
  notificationBar: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.sm,
  },
  notifWarning: {
    backgroundColor: '#FFFBEB',
    borderColor: '#FDE68A',
    borderBottomWidth: 1,
  },
  notifSuccess: {
    backgroundColor: '#ECFDF5',
    borderColor: '#A7F3D0',
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
  summaryBar: {
    flexDirection: 'row',
    backgroundColor: colors.surface,
    paddingVertical: spacing.sm,
    paddingHorizontal: spacing.md,
    borderBottomWidth: 1,
    borderBottomColor: colors.borderLight,
    alignItems: 'center',
  },
  summaryItem: {
    flex: 1,
    alignItems: 'center',
  },
  summaryNum: {
    fontSize: 16,
    fontWeight: '800',
    color: colors.niyamBlue,
  },
  summaryLbl: {
    fontSize: 9,
    fontWeight: '700',
    color: colors.textMuted,
    marginTop: 1,
  },
  summaryDivider: {
    width: 1,
    height: 24,
    backgroundColor: colors.borderLight,
  },
  scroll: {
    flex: 1,
  },
  content: {
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
    marginTop: spacing.md,
    fontSize: 13,
    color: colors.textMuted,
  },
  emptyCard: {
    alignItems: 'center',
    paddingVertical: spacing.xxl,
  },
  emptyIcon: {
    fontSize: 32,
    color: colors.pass.text,
    marginBottom: spacing.sm,
  },
  emptyTitle: {
    fontSize: 16,
    fontWeight: '800',
    color: colors.niyamBlue,
    marginBottom: 4,
  },
  emptySub: {
    fontSize: 12,
    color: colors.textMuted,
    textAlign: 'center',
    lineHeight: 16,
  },
  taskCard: {
    backgroundColor: colors.surface,
    borderRadius: radius.lg,
    ...shadows.sm,
    gap: spacing.xs,
  },
  rowBetween: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
  },
  rowAlign: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
  },
  panelBadge: {
    backgroundColor: '#F1F5F9',
    borderColor: '#CBD5E1',
    borderWidth: 1,
    borderRadius: radius.sm,
    paddingHorizontal: 8,
    paddingVertical: 2,
  },
  panelBadgeText: {
    fontSize: 11,
    fontWeight: '800',
    color: colors.textSecondary,
  },
  priorityBadge: {
    borderRadius: radius.sm,
    paddingHorizontal: 6,
    paddingVertical: 2,
    borderWidth: 1,
  },
  pHigh: { backgroundColor: '#FEE2E2', borderColor: '#FCA5A5' },
  pMed: { backgroundColor: '#FEF3C7', borderColor: '#FCD34D' },
  priorityText: { fontSize: 9, fontWeight: '800' },
  pHighText: { color: '#B91C1C' },
  pMedText: { color: '#B45309' },
  statusBadge: {
    borderRadius: radius.sm,
    paddingHorizontal: 6,
    paddingVertical: 2,
    borderWidth: 1,
  },
  sPending: { backgroundColor: '#FFFBEB', borderColor: '#FDE68A' },
  sFulfilled: { backgroundColor: '#ECFDF5', borderColor: '#A7F3D0' },
  sSkipped: { backgroundColor: '#F1F5F9', borderColor: '#CBD5E1' },
  statusText: { fontSize: 9, fontWeight: '800' },
  sPendingText: { color: '#B45309' },
  sFulfilledText: { color: '#047857' },
  sSkippedText: { color: '#64748B' },
  taskReason: {
    fontSize: 13,
    fontWeight: '700',
    color: colors.text,
    marginTop: 4,
    lineHeight: 18,
  },
  taskSuggested: {
    fontSize: 12,
    color: colors.netraTeal,
    marginTop: 2,
    lineHeight: 16,
  },
  evidenceReqBox: {
    backgroundColor: '#F8FAFC',
    borderRadius: radius.sm,
    padding: spacing.sm,
    marginTop: spacing.xs,
  },
  evidenceReqTitle: {
    fontSize: 10,
    fontWeight: '700',
    color: colors.textMuted,
  },
  evidenceReqText: {
    fontSize: 11,
    color: colors.textSecondary,
    marginTop: 2,
  },
  loopWarningBox: {
    backgroundColor: '#FEF2F2',
    borderColor: '#FECACA',
    borderWidth: 1,
    borderRadius: radius.md,
    padding: spacing.sm,
    marginTop: spacing.xs,
  },
  loopWarningTitle: {
    fontSize: 11,
    fontWeight: '800',
    color: '#B91C1C',
  },
  loopWarningText: {
    fontSize: 11,
    color: '#7F1D1D',
    marginTop: 2,
    lineHeight: 15,
  },
  taskActionRow: {
    flexDirection: 'row',
    justifyContent: 'flex-end',
    alignItems: 'center',
    gap: spacing.md,
    marginTop: spacing.sm,
    paddingTop: spacing.xs,
    borderTopWidth: 1,
    borderTopColor: colors.borderLight,
  },
  skipBtn: {
    paddingHorizontal: 12,
    paddingVertical: 8,
  },
  skipBtnText: {
    fontSize: 12,
    fontWeight: '600',
    color: colors.textMuted,
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
});
