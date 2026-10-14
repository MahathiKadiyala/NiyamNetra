import React, { useState } from 'react';
import { View, StyleSheet, Alert } from 'react-native';
import HomeScreen from './HomeScreen';
import NewInspectionScreen from './NewInspectionScreen';
import InspectionSessionScreen from './InspectionSessionScreen';
import FindingsScreen from './FindingsScreen';
import InspectionSummaryScreen from './InspectionSummaryScreen';
import ReviewQueueScreen from './ReviewQueueScreen';
import ReviewDetailScreen from './ReviewDetailScreen';
import RecaptureTaskScreen from './RecaptureTaskScreen';
import InspectionReportScreen from './InspectionReportScreen';
import { fetchInspectionDetails } from '../../api/inspections';
import { colors } from '../../theme';

export default function InspectorFlow({ navigation }) {
  // Navigation mode within inspector workflow:
  // 'home' | 'new_inspection' | 'session' | 'findings' | 'summary' | 'review_queue' | 'review_detail' | 'recapture_task' | 'report_dossier'
  const [mode, setMode] = useState('home');

  // Active store inspection session state
  const [activeSession, setActiveSession] = useState(null);

  // Selected package for findings drill-down
  const [selectedScan, setSelectedScan] = useState(null);

  // Selected inspection for review & adjudication & reports
  const [reviewInspectionId, setReviewInspectionId] = useState(null);
  const [recaptureScanId, setRecaptureScanId] = useState(null);
  const [reportInspectionId, setReportInspectionId] = useState(null);

  // --- Handlers ---
  const handleStartInspection = () => {
    setActiveSession(null);
    setSelectedScan(null);
    setMode('new_inspection');
  };

  const handleResumeInspection = () => {
    if (activeSession) setMode('session');
    else setMode('home');
  };

  const handleResumeDraft = async (inspectionId) => {
    if (!inspectionId) return;
    try {
      setSelectedScan(null);
      const details = await fetchInspectionDetails(inspectionId);
      if (details?.status === 'draft') {
        const reconstructedScans = (details.scans || []).map((s) => ({
          id: s.id,
          server_id: s.id,
          commodity_generic: s.commodity_generic,
          brand_name: s.brand_name,
          batch_number: s.batch_number,
          overall_result: s.overall_result || 'not_assessed',
          checks_total: s.checks_total || 19,
          checks_assessed: s.checks_assessed || 0,
          findings: s.findings || [],
          panelPhotos: (s.images || []).reduce((acc, img) => {
            acc[img.panel] = `/scans/${s.id}/images/${img.id}`;
            return acc;
          }, {}),
          panelEvidence: {},
          files: (s.images || []).map((img) => ({
            panel: img.panel,
            uri: `/scans/${s.id}/images/${img.id}`,
          })),
          created_at: s.created_at,
        }));

        setActiveSession({
          id: details.id,
          serverInspectionId: details.id,
          store: {
            id: details.store_id || details.store?.id,
            name: details.store_name || details.store?.name || 'Retail Establishment',
            address: details.store_address || details.store?.address || '',
            city: details.store_city || details.store?.city || '',
            district: details.store_district || details.store?.district || '',
            state: details.store_state || details.store?.state || '',
            pincode: details.store_pincode || details.store?.pincode || '',
          },
          transaction_type: details.transaction_type || 'retail_sale',
          coords: {
            latitude: details.latitude,
            longitude: details.longitude,
            accuracy: details.gps_accuracy_m,
          },
          started_at: details.local_created_at || details.inspection_date,
          signature_status: details.signature_status || 'signed',
          notes: details.notes || '',
          scans: reconstructedScans,
        });
        setSelectedScan(null);
        setMode('session');
      } else {
        Alert.alert(
          'Inspection Finalized',
          'This inspection has already been submitted and cannot be reopened as an active draft. Opening final inspection dossier.'
        );
        handleOpenInspectionReport(inspectionId);
      }
    } catch (err) {
      console.warn('[InspectorFlow] Failed to resume draft:', err?.message || err);
      Alert.alert('Resume Failed', 'Unable to load draft inspection details. Please check your network connection.');
    }
  };

  const handleOpenReviewQueue = () => {
    setMode('review_queue');
  };

  const handleSelectReviewInspection = (inspectionId) => {
    setReviewInspectionId(inspectionId);
    setMode('review_detail');
  };

  const handleBackFromReviewQueue = () => {
    setMode('home');
  };

  const handleBackFromReviewDetail = () => {
    setMode('review_queue');
  };

  const handleOpenRecapture = (inspectionId, scanId) => {
    setReviewInspectionId(inspectionId);
    setRecaptureScanId(scanId || null);
    setMode('recapture_task');
  };

  const handleBackFromRecapture = () => {
    setMode('review_detail');
  };

  const handleOpenInspectionReport = (inspectionId) => {
    setReportInspectionId(inspectionId);
    setMode('report_dossier');
  };

  const handleBackFromReport = () => {
    setMode('home');
  };

  const handleInspectionSessionStarted = (sessionData) => {
    setSelectedScan(null);
    setActiveSession({
      ...sessionData,
      scans: [],
    });
    setMode('session');
  };

  const handlePackageAssessed = (newScan, updatedScans) => {
    setActiveSession((prev) => ({
      ...prev,
      scans: updatedScans,
    }));
    setSelectedScan(newScan);
    setMode('findings');
  };

  const handleViewFindings = (scan) => {
    setSelectedScan(scan);
    setMode('findings');
  };

  const handleSaveFindings = (updatedFindings) => {
    if (!selectedScan || !activeSession) return;
    const failCount = updatedFindings.filter((f) => f.effective_verdict === 'fail' || f.effective_verdict === 'violation').length;
    const notAssessed = updatedFindings.filter((f) => f.effective_verdict === 'not_assessed').length;
    const rollup = failCount > 0 ? 'violation' : (notAssessed === 0 ? 'compliant' : 'not_assessed');
    const updatedScans = (activeSession.scans || []).map((s) =>
      s.id === selectedScan.id
        ? {
            ...s,
            findings: updatedFindings,
            overall_result: rollup,
          }
        : s
    );
    setActiveSession((prev) => ({
      ...prev,
      scans: updatedScans,
    }));
  };

  const handleCompleteInspection = (sessionSummary) => {
    setActiveSession(sessionSummary);
    setMode('summary');
  };

  const handleInspectionFinalized = () => {
    setActiveSession(null);
    setSelectedScan(null);
    setMode('home');
  };

  const handleCancelNew = () => {
    setMode('home');
  };

  const handleBackToSession = () => {
    setMode('session');
  };

  return (
    <View style={styles.container}>
      {mode === 'home' && (
        <HomeScreen
          navigation={navigation}
          activeInspection={activeSession}
          onStartInspection={handleStartInspection}
          onResumeInspection={handleResumeInspection}
          onResumeDraft={handleResumeDraft}
          onOpenReviewQueue={handleOpenReviewQueue}
          onOpenReport={handleOpenInspectionReport}
        />
      )}

      {mode === 'new_inspection' && (
        <NewInspectionScreen
          navigation={navigation}
          onStartInspectionSession={handleInspectionSessionStarted}
          onCancel={handleCancelNew}
        />
      )}

      {mode === 'session' && (
        <InspectionSessionScreen
          key={activeSession?.serverInspectionId || activeSession?.id || 'session'}
          inspectionSession={activeSession}
          onPackageAssessed={handlePackageAssessed}
          onViewFindings={handleViewFindings}
          onCompleteInspection={handleCompleteInspection}
          onCancel={handleCancelNew}
        />
      )}

      {mode === 'findings' && (
        <FindingsScreen
          key={selectedScan?.server_id || selectedScan?.id || 'scan'}
          scan={selectedScan}
          onBack={handleBackToSession}
          onSaveFindings={handleSaveFindings}
        />
      )}

      {mode === 'summary' && (
        <InspectionSummaryScreen
          inspectionSession={activeSession}
          onInspectionFinalized={handleInspectionFinalized}
          onBackToSession={handleBackToSession}
          onViewReport={handleOpenInspectionReport}
        />
      )}

      {mode === 'review_queue' && (
        <ReviewQueueScreen
          onSelectInspection={handleSelectReviewInspection}
          onBack={handleBackFromReviewQueue}
        />
      )}

      {mode === 'review_detail' && (
        <ReviewDetailScreen
          inspectionId={reviewInspectionId}
          onBack={handleBackFromReviewDetail}
          onAdjudicated={() => {}}
          onOpenRecapture={handleOpenRecapture}
          onOpenReport={handleOpenInspectionReport}
        />
      )}

      {mode === 'recapture_task' && (
        <RecaptureTaskScreen
          inspectionId={reviewInspectionId}
          scanId={recaptureScanId}
          onBack={handleBackFromRecapture}
          onComplete={handleBackFromRecapture}
        />
      )}

      {mode === 'report_dossier' && (
        <InspectionReportScreen
          inspectionId={reportInspectionId}
          onBack={handleBackFromReport}
          onResumeDraft={handleResumeDraft}
          onOpenReviewDetail={handleSelectReviewInspection}
          onOpenRecapture={handleOpenRecapture}
        />
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: colors.background,
  },
});
