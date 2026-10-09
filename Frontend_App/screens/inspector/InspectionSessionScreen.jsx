import React, { useState, useRef, useEffect } from 'react';
import {
  View,
  Text,
  ScrollView,
  Pressable,
  Image,
  Alert,
  ActivityIndicator,
  StyleSheet,
  TextInput,
} from 'react-native';
import { CameraView, useCameraPermissions } from 'expo-camera';
import { colors, spacing, typography, radius, shadows } from '../../theme';
import Header from '../../components/Header';
import Card from '../../components/Card';
import PrimaryButton from '../../components/PrimaryButton';
import VerdictBadge from '../../components/VerdictBadge';
import { enqueueInspection, enqueueScan } from '../../offline/queue';
import { useAppLock } from '../../hooks/useAppLock';
import { useAllowScreenCapture } from '../../hooks/useAllowScreenCapture';
import {
  createStore,
  createInspection,
  createScan,
  uploadScanImage,
  updateScanScope,
  assessScan,
} from '../../api/inspections';
import { persistOriginalCapture, createAnalysisImage } from '../../offline/evidenceManager';
import { assessCaptureQuality } from '../../offline/qualityGate';

let ImageManipulator = null;
try { ImageManipulator = require('expo-image-manipulator'); } catch { ImageManipulator = null; }

const PANELS = [
  { key: 'front', label: 'Front Panel', desc: 'Product name & brand' },
  { key: 'back', label: 'Back Panel', desc: 'Manufacturer details & ingredients' },
  { key: 'mrp', label: 'MRP Panel', desc: 'MRP, Unit Sale Price, Dates' },
  { key: 'batch', label: 'Batch / Barcode', desc: 'Batch code & barcode' },
];

function buildOfflineFindings({ commodity, brand, batch, hasSticker, isImported, isPerishable }) {
  const master = [
    { code: 'CHK01', name: 'Mandatory declarations on retail pre-packaged commodity', citation: 'Rule 6(1)', required: 'All statutory declarations present on PDP', severity: 'critical', defaultObserved: 'Pending server OCR text extraction (captured offline)' },
    { code: 'CHK02', name: 'Exemption carve-out for tobacco and tobacco products', citation: 'Rule 26(a)', required: 'Tobacco carve-out applicability verification', severity: 'advisory', defaultObserved: 'Scope verification' },
    { code: 'CHK03', name: 'Chapter II scope and retail quantity thresholds', citation: 'Rule 3', required: 'Pre-packaged commodity intended for retail sale', severity: 'critical', defaultObserved: 'Retail sale transaction confirmed' },
    { code: 'CHK04', name: 'Retail sale price (MRP) correctly expressed', citation: 'Rule 6(1)(e) & Rule 2(m)', required: 'MRP in Indian Rupees inclusive of all taxes', severity: 'critical', defaultObserved: 'Pending server OCR text extraction (captured offline)' },
    { code: 'CHK05', name: 'Prescribed standard units of weight, volume, or length', citation: 'Rule 12 & Rule 13', required: 'Standard metric units without non-standard qualifiers', severity: 'critical', defaultObserved: 'Pending metric unit verification' },
    { code: 'CHK06', name: 'Minimum height of letters on Principal Display Panel', citation: 'Rule 7(1) & Table-I', required: 'Letter height proportion matching Table-I standards', severity: 'major', defaultObserved: 'Pending optical font height verification' },
    { code: 'CHK06b', name: 'Minimum height of net quantity numerals', citation: 'Rule 7(2) read with Table-I', required: 'Numeral height matching GSR 629(E) standards', severity: 'major', defaultObserved: 'Pending numeral height measurement' },
    { code: 'CHK06b_hist', name: 'Historical minimum height of net quantity numerals', citation: 'Rule 7 & Table-II (pre-2018)', required: 'Pre-2018 historical numeral standard check', severity: 'major', defaultObserved: 'Historical table reference' },
    { code: 'CHK07', name: 'Character width proportion', citation: 'Rule 7(3)', required: 'Width at least one-third of character height', severity: 'minor', defaultObserved: 'Pending character width calculation' },
    { code: 'CHK08', name: 'Conspicuous contrast of declarations with background', citation: 'Rule 9(1)', required: 'High visual contrast against background', severity: 'minor', defaultObserved: 'Pending contrast ratio evaluation' },
    { code: 'CHK09', name: 'Clear surrounding space around net-quantity declaration', citation: 'Rule 8', required: 'Unobstructed surrounding boundary', severity: 'minor', defaultObserved: 'Pending clear space measurement' },
    { code: 'CHK22', name: 'Placement of mandatory declarations on Principal Display Panel', citation: 'Rule 8', required: 'Mandatory declarations grouped on PDP', severity: 'major', defaultObserved: 'Pending PDP layout analysis' },
    { code: 'CHK10', name: 'Standard prescribed packaging quantities', citation: 'Rule 5 read with the Second Schedule', required: 'Standard quantity schedules under Second Schedule', severity: 'minor', defaultObserved: 'Pending schedule comparison' },
    { code: 'CHK11', name: 'Permissible conditions for price alteration stickers', citation: 'Rule 6(3), 6(4), 6(4A)', required: 'Declarations must be indelible; no unauthorized stickers', severity: 'critical', defaultObserved: hasSticker ? 'Sticker found affixed over package' : 'No sticker alteration declared' },
    { code: 'CHK12', name: 'Country of origin declaration on imported commodities', citation: 'Rule 6(1)(aa)', required: 'Country of origin stated for all packages', severity: 'critical', defaultObserved: isImported ? 'Imported item — pending origin declaration check' : 'Domestic package' },
    { code: 'CHK13', name: 'Best before or use by date for perishable commodities', citation: 'Rule 6(1)(da)', required: 'Clear expiry or best before for perishables', severity: 'critical', defaultObserved: isPerishable ? 'Perishable item — pending date verification' : 'Non-perishable commodity' },
    { code: 'CHK14', name: 'Applicability proviso for medical devices', citation: 'Rule 2(h) Proviso', required: 'Medical devices regulatory carve-out', severity: 'advisory', defaultObserved: 'Scope verification' },
    { code: 'CHK15', name: 'Mandatory declarations on e-commerce product listings', citation: 'Rule 6(10)', required: 'E-commerce digital display declarations', severity: 'major', defaultObserved: 'Physical retail package sampled in store' },
    { code: 'CHK16', name: 'Marketplace search filter for country of origin', citation: 'Rule 6(10A)', required: 'Search filter requirement for e-commerce platforms', severity: 'major', defaultObserved: 'Physical retail package sampled in store' },
    { code: 'CHK17', name: 'Alignment with FSSAI statutory packaging advisories', citation: 'FSSAI Packaging Regulations & LM Alignment', required: 'Food safety advisory alignment', severity: 'advisory', defaultObserved: 'Pending regulatory review' },
    { code: 'CHK18', name: 'Graduated enforcement response and Section 36 sanctions', citation: 'Section 36', required: 'Statutory penalty classification', severity: 'critical', defaultObserved: 'Pending statutory review' },
    { code: 'CHK19', name: 'Unit Sale Price (USP) declaration', citation: 'Rule 6(1)(g)', required: 'Unit sale price declared in Rs per g/ml/piece', severity: 'major', defaultObserved: 'Pending USP extraction' },
    { code: 'CHK20', name: 'Dimensions declaration where size is relevant', citation: 'Rule 6(1)(m)', required: 'Dimensions declared in metric units', severity: 'minor', defaultObserved: 'Pending dimension extraction' },
    { code: 'CHK21', name: 'Special declaration standards for garments and hosiery goods', citation: 'Rule 6(1)(b) Proviso & Second Schedule Exemption', required: 'Garments and hosiery size standards', severity: 'major', defaultObserved: 'Non-apparel commodity' },
    { code: 'CHK23_hist', name: 'Origin marking on cosmetics (former Rule 6(8))', citation: 'Rule 6(8) (omitted w.e.f 21.09.2026 by GSR 826(E))', required: 'Historical cosmetics origin indicator', severity: 'major', defaultObserved: 'Historical rule check' },
    { code: 'CHK23', name: 'Origin marking on soap, cosmetics, toiletries', citation: 'Rule 6(4A)(d)', required: 'Vegetarian / non-vegetarian dot on specified items', severity: 'advisory', defaultObserved: 'Pending visual inspection' },
  ];

  // 05_SYSTEM_ARCHITECTURE §1.1 / Backend.md C4: the server is the ONLY
  // assessor. Offline capture renders the 19 rows as PENDING — these verdict
  // fields were previously seeded from device flags (CHK03 'pass', CHK05
  // 'fail' on an empty commodity, CHK13 'fail' on a sticker), which
  // masqueraded as engine verdicts and rolled the scan up to 'violation'
  // before any server assessment existed. Observations stay; verdicts wait.
  return master.map((m, idx) => ({
    id: idx + 1,
    check_id: m.code,
    title: m.name,
    citation: m.citation,
    engine_verdict: 'not_assessed',
    effective_verdict: 'not_assessed',
    human_verdict: null,
    observed: m.defaultObserved,
    required: m.required,
    severity: m.severity,
    reason: 'Captured offline — pending server OCR and statutory assessment.',
  }));
}

export default function InspectionSessionScreen({
  inspectionSession,
  onPackageAssessed,
  onCompleteInspection,
  onCancel,
  onViewFindings,
}) {
  const [permission, requestPermission] = useCameraPermissions();
  const [activePanel, setActivePanel] = useState('front');
  const [panelPhotos, setPanelPhotos] = useState({}); // { front: uri, back: uri, ... } (derived analysis representation for display)
  const [panelEvidence, setPanelEvidence] = useState({}); // { front: EvidenceRecord, ... } (immutable original camera captures)
  const [capturing, setCapturing] = useState(false);

  // Package fields
  const [commodity, setCommodity] = useState('');
  const [brand, setBrand] = useState('');
  const [batch, setBatch] = useState('');
  const [panelShape, setPanelShape] = useState('rectangular'); // rectangular, cylindrical, other

  // Scope flags (Legal Metrology Rule 6)
  const [isImported, setIsImported] = useState(false);
  const [isPerishable, setIsPerishable] = useState(false);
  const [hasSticker, setHasSticker] = useState(false);
  const [isBlownMoulded, setIsBlownMoulded] = useState(false);

  // Submitting / assessing package state
  const [assessing, setAssessing] = useState(false);

  // Completed packages in this visit
  const [packages, setPackages] = useState(inspectionSession?.scans || []);

  // Synchronize packages and clear in-flight package state when session changes
  useEffect(() => {
    setPackages(inspectionSession?.scans || []);
  }, [inspectionSession?.id, inspectionSession?.serverInspectionId, inspectionSession?.scans]);

  useEffect(() => {
    setPanelPhotos({});
    setPanelEvidence({});
    setCommodity('');
    setBrand('');
    setBatch('');
    setHasSticker(false);
    setIsImported(false);
    setIsPerishable(false);
    setIsBlownMoulded(false);
    setActivePanel('front');
  }, [inspectionSession?.id, inspectionSession?.serverInspectionId]);

  const cameraRef = useRef(null);
  const assessingRef = useRef(false);
  useAppLock({ enabled: true });

  // The evidence-capture surface must stay screen-recordable: this re-clears
  // Android FLAG_SECURE on mount and on every app resume, and never sets it.
  // (The screen previously called preventScreenCaptureAsync, which blacked out
  // the recording of EVERY screen until the app was force-stopped.)
  useAllowScreenCapture();

  // Viewfinder status — the preview surface is black by nature, so before the
  // first frame (or when the OS refuses the camera) the officer would otherwise
  // stare at a featureless black card.
  const [cameraReady, setCameraReady] = useState(false);
  const [cameraError, setCameraError] = useState(null);

  const handleCapturePhoto = async () => {
    if (!cameraRef.current || capturing) return;
    if (cameraError) {
      Alert.alert('Camera unavailable', cameraError);
      return;
    }
    if (!cameraReady) {
      Alert.alert('Camera starting', 'The camera is still starting up. Please try again in a moment.');
      return;
    }
    setCapturing(true);
    const tCaptureStart = Date.now();
    try {
      // Step A: Camera capture. No `skipProcessing`: the camera's processing pipeline physically
      // applies the EXIF orientation to the initial JPEG stream.
      const photo = await cameraRef.current.takePictureAsync({ quality: 0.8 });
      const tCaptureEnd = Date.now();
      if (photo?.uri) {
        // Step B: Immediately establish an immutable local evidence copy in durable storage.
        // Original bytes are preserved untouched without any downsampling or compression.
        const tPersistStart = Date.now();
        const evidenceRecord = await persistOriginalCapture(photo, {
          panel: activePanel,
          inspectionId: inspectionSession?.serverInspectionId || inspectionSession?.id || null,
          scanId: null,
        });
        const tPersistEnd = Date.now();

        // Step C: Create a separate, derived analysis image (1600px width, 80% quality JPEG)
        // for downstream OCR and quality gating. The original capture remains pristine.
        const tAnalysisStart = Date.now();
        const processedRecord = await createAnalysisImage(evidenceRecord);
        const tAnalysisEnd = Date.now();

        // Step D: Fast Image Quality Gate evaluation on derived analysis image
        const tQualityStart = Date.now();
        const qualityResult = await assessCaptureQuality(processedRecord);
        const tQualityEnd = Date.now();

        processedRecord.quality_gate = qualityResult;

        const captureMs = tCaptureEnd - tCaptureStart;
        const persistMs = tPersistEnd - tPersistStart;
        const analysisMs = tAnalysisEnd - tAnalysisStart;
        const qualityMs = tQualityEnd - tQualityStart;

        console.log('[PERF_EVIDENCE] capture_preservation_and_quality_completed', JSON.stringify({
          panel: activePanel,
          originalUri: processedRecord.original_uri,
          analysisUri: processedRecord.analysis_uri,
          width: processedRecord.width,
          height: processedRecord.height,
          fileSizeBytes: processedRecord.file_size_bytes,
          captureDurationMs: captureMs,
          persistOriginalMs: persistMs,
          createAnalysisMs: analysisMs,
          qualityGateMs: qualityMs,
          qualityDecision: qualityResult.decision,
          primaryGuidance: qualityResult.primary_guidance,
          totalPreservationMs: tQualityEnd - tCaptureStart,
        }));

        setPanelEvidence((prev) => ({
          ...prev,
          [activePanel]: processedRecord,
        }));

        setPanelPhotos((prev) => ({
          ...prev,
          [activePanel]: processedRecord.analysis_uri || processedRecord.original_uri,
        }));

        // Quality-aware advance:
        // If RETAKE_REQUIRED, keep the inspector on activePanel so they see guidance and retake immediately.
        // If READY or READY_WITH_WARNINGS, advance to the next panel.
        if (qualityResult.decision !== 'RETAKE_REQUIRED') {
          const idx = PANELS.findIndex((p) => p.key === activePanel);
          if (idx < PANELS.length - 1) {
            setActivePanel(PANELS[idx + 1].key);
          }
        }
      }
    } catch (e) {
      console.error('[Session] Capture / evidence preservation error:', e);
      Alert.alert(
        'Capture Failed',
        e?.message || 'Could not preserve photo evidence. Please check storage and try again.'
      );
    } finally {
      setCapturing(false);
    }
  };

  const handleAssessCurrentPackage = async () => {
    if (assessingRef.current) return;
    const photoUris = Object.values(panelPhotos).filter(Boolean);
    if (photoUris.length === 0) {
      Alert.alert('Evidence Required', 'Please take at least one panel photograph before assessment.');
      return;
    }

    const tFlowStart = Date.now();
    const bench = {
      panelCount: photoUris.length,
      inspectionCreationMs: 0,
      scanCreationMs: 0,
      panelUploads: {},
      totalUploadMs: 0,
      serverAssessMs: 0,
      totalInspectorWaitMs: 0,
    };

    assessingRef.current = true;
    setAssessing(true);
    let serverErrorDetail = null;
    try {
      let serverScanId = null;
      let assessedScan = null;
      let usedServerId = inspectionSession?.serverInspectionId;

      // 1. If online and no serverInspectionId yet, create inspection on the server
      if (!usedServerId) {
        try {
          let storeId = inspectionSession?.store?.id;
          if (!storeId || (typeof storeId === 'number' && storeId < 0)) {
            // Local store intake — create store on server first
            const createdStore = await createStore({
              name: inspectionSession?.store?.name || 'Local Retail Store',
              store_type: inspectionSession?.store?.store_type || 'Kirana / General Store',
              address: inspectionSession?.store?.address || undefined,
              city: inspectionSession?.store?.city || 'Hyderabad',
              district: inspectionSession?.store?.district || 'Hyderabad',
              state: 'Telangana',
              latitude: inspectionSession?.coords?.latitude || undefined,
              longitude: inspectionSession?.coords?.longitude || undefined,
              geofence_radius_m: 150,
            });
            if (createdStore?.id) {
              storeId = createdStore.id;
              if (inspectionSession?.store) inspectionSession.store.id = storeId;
            }
          }

          if (storeId && typeof storeId === 'number' && storeId > 0) {
            const tInspStart = Date.now();
            const newInsp = await createInspection({
              store_id: Number(storeId),
              transaction_type: inspectionSession?.transaction_type || 'retail_sale',
              latitude: inspectionSession?.coords?.latitude,
              longitude: inspectionSession?.coords?.longitude,
              gps_accuracy_m: inspectionSession?.coords?.accuracy,
              local_created_at: inspectionSession?.started_at || new Date().toISOString(),
            });
            bench.inspectionCreationMs = Date.now() - tInspStart;
            usedServerId = newInsp?.id || newInsp?.inspection_id;
            if (usedServerId && inspectionSession) {
              inspectionSession.serverInspectionId = usedServerId;
            }
          }
        } catch (inspErr) {
          serverErrorDetail = inspErr?.response?.data?.detail || inspErr?.message || 'Failed to start server inspection session';
          console.warn('[Session] createInspection failed (offline?):', serverErrorDetail);
        }
      }

      // 2. Honest Geometry: No fabricated dimensions (C14 / Legal Metrology Act)
      // When no calibrated physical reference (ID-1 card / ₹5 coin) or verified declared
      // dimension is provided, scale_source must be 'none'. Downstream statutory typography
      // checks will honestly report 'not_assessed' rather than manufactured facts.
      const geometry = {
        panel_shape: panelShape || 'rectangular',
        panel_height_mm: null,
        panel_width_mm: null,
        panel_diameter_mm: null,
        total_surface_area_cm2: null,
        is_blown_moulded: Boolean(isBlownMoulded),
        scale_source: 'none',
      };

      // 3. Try server createScan -> scope -> upload images -> assessScan
      if (usedServerId) {
        try {
          const tScanStart = Date.now();
          const scanRes = await createScan(usedServerId, {
            commodity_generic: commodity.trim() || null,
            brand_name: brand.trim() || null,
            batch_number: batch.trim() || null,
            geometry,
          });
          bench.scanCreationMs = Date.now() - tScanStart;
          serverScanId = scanRes?.id || scanRes?.scan_id;

          if (serverScanId) {
            // Persist declared scope flags
            await updateScanScope(serverScanId, {
              is_imported: isImported,
              is_perishable: isPerishable,
              has_sticker: hasSticker,
            });

            // Upload all captured panel images concurrently in parallel for max speed
            const tUploadsStart = Date.now();
            const uploadTasks = Object.entries(panelEvidence)
              .filter(([_, ev]) => Boolean(ev?.analysis_uri || ev?.original_uri))
              .map(async ([panelKey, ev]) => {
                const tPStart = Date.now();
                const uploadUri = ev.analysis_uri || ev.original_uri;
                const res = await uploadScanImage(serverScanId, panelKey, uploadUri);
                const tPEnd = Date.now();
                bench.panelUploads[panelKey] = tPEnd - tPStart;
                return res;
              });
            await Promise.all(uploadTasks);
            bench.totalUploadMs = Date.now() - tUploadsStart;

            // Run authoritative statutory assessment across all 19 rules
            const tAssessStart = Date.now();
            assessedScan = await assessScan(serverScanId);
            bench.serverAssessMs = Date.now() - tAssessStart;
          }
        } catch (serverErr) {
          // Distinguish "server reachable but failed" from "device offline /
          // timeout" so the inspector sees the honest reason, not a vague
          // "offline" for what was a 500, or vice versa.
          const resp = serverErr?.response;
          if (resp) {
            serverErrorDetail = `Server error (HTTP ${resp.status})${resp?.data?.detail ? ': ' + resp.data.detail : ''}`;
          } else if (serverErr?.request || serverErr?.code === 'ECONNABORTED' || /timeout|network/i.test(serverErr?.message || '')) {
            serverErrorDetail = 'Device offline or the server took too long to respond.';
          } else {
            serverErrorDetail = serverErr?.message || 'Server assessment request failed';
          }
          console.warn('[Session] Live server assessment failed, falling back to local:', serverErrorDetail);
        }
      } else if (!serverErrorDetail) {
        serverErrorDetail = 'No active server connection (offline mode)';
      }

      bench.totalInspectorWaitMs = Date.now() - tFlowStart;
      console.log('[PERF_BASELINE] package_assessment_flow', JSON.stringify(bench));

      let scanItem;
      if (assessedScan && Array.isArray(assessedScan.findings)) {
        // Authoritative server assessment result
        scanItem = {
          id: `pkg-${serverScanId || Date.now()}`,
          server_id: serverScanId,
          commodity_generic: assessedScan.commodity_generic || (commodity.trim() ? commodity.trim() : null),
          brand_name: assessedScan.brand_name || (brand.trim() ? brand.trim() : null),
          batch_number: assessedScan.batch_number || (batch.trim() ? batch.trim() : null),
          geometry,
          panelPhotos: { ...panelPhotos },
          panelEvidence: { ...panelEvidence },
          files: Object.values(panelEvidence).filter(Boolean).map((e) => ({
            uri: e.original_uri,
            panel: e.panel,
            original_uri: e.original_uri,
            analysis_uri: e.analysis_uri,
            width: e.width,
            height: e.height,
            file_size_bytes: e.file_size_bytes,
            captured_at: e.captured_at,
            capture_source: e.capture_source,
          })),
          is_imported: isImported,
          is_perishable: isPerishable,
          has_sticker: hasSticker,
          overall_result: assessedScan.overall_result,
          violation_limb: assessedScan.violation_limb,
          checks_assessed: assessedScan.checks_assessed,
          checks_total: assessedScan.checks_total || 26,
          findings: assessedScan.findings,
          created_at: assessedScan.created_at || new Date().toISOString(),
        };
      } else {
        // Offline assessment fallback — never invent a false compliant verdict
        const offlineFindings = buildOfflineFindings({
          commodity: commodity.trim(),
          brand: brand.trim(),
          batch: batch.trim(),
          hasSticker,
          isImported,
          isPerishable,
        });
        scanItem = {
          id: `pkg-${serverScanId || Date.now()}`,
          server_id: serverScanId,
          commodity_generic: commodity.trim() || null,
          brand_name: brand.trim() || null,
          batch_number: batch.trim() || null,
          geometry,
          panelPhotos: { ...panelPhotos },
          panelEvidence: { ...panelEvidence },
          files: Object.values(panelEvidence).filter(Boolean).map((e) => ({
            uri: e.original_uri,
            panel: e.panel,
            original_uri: e.original_uri,
            analysis_uri: e.analysis_uri,
            width: e.width,
            height: e.height,
            file_size_bytes: e.file_size_bytes,
            captured_at: e.captured_at,
            capture_source: e.capture_source,
          })),
          is_imported: isImported,
          is_perishable: isPerishable,
          has_sticker: hasSticker,
          overall_result: 'not_assessed',
          checks_assessed: 0,
          checks_total: 26,
          findings: offlineFindings,
          is_offline: true,
          created_at: new Date().toISOString(),
        };

        // Alert the inspector with the reason why live evaluation couldn't complete
        Alert.alert(
          'Assessment Pending — Not Scored Yet',
          `The statutory checks could not be assessed live right now:\n• ${serverErrorDetail}\n\nYour photos and package details are saved. Nothing has been scored, so no verdict is invented. Once you are online, open this package and tap "Run Server Assessment" — the engine will read YOUR actual photos and produce a genuine result.`,
          [{ text: 'Review Package' }]
        );
      }

      const updated = [...packages, scanItem];
      setPackages(updated);

      // Reset current package inputs for next item
      setPanelPhotos({});
      setPanelEvidence({});
      setCommodity('');
      setBrand('');
      setBatch('');
      setHasSticker(false);
      setIsImported(false);
      setIsPerishable(false);
      setIsBlownMoulded(false);
      setActivePanel('front');

      // Navigate to findings review for this package
      if (onPackageAssessed) {
        onPackageAssessed(scanItem, updated);
      }
    } catch (e) {
      Alert.alert('Assessment Error', `Failed to assess package: ${e?.message || 'Unknown error'}. Saved to offline queue.`);
    } finally {
      assessingRef.current = false;
      setAssessing(false);
    }
  };

  const handleFinishInspection = () => {
    if (packages.length === 0 && Object.keys(panelPhotos).length === 0) {
      Alert.alert(
        'Conclude Visit (0 Scans)',
        'No packages were scanned. If the merchant/owner refused inspection or no packaged goods are available, you can record this visit with an official reason.',
        [
          {
            text: '🚫 Owner Refused Inspection',
            style: 'destructive',
            onPress: () => {
              onCompleteInspection({
                ...inspectionSession,
                scans: [],
                refusal_reason: 'Merchant / Shop owner refused inspection under Legal Metrology Act, 2009',
                signature_status: 'refused',
                notes: 'Merchant / Shop owner refused statutory inspection under Legal Metrology Act, 2009.',
                finished_at: new Date().toISOString(),
              });
            },
          },
          {
            text: '🏬 Store Closed / No Stock',
            onPress: () => {
              onCompleteInspection({
                ...inspectionSession,
                scans: [],
                refusal_reason: 'Store closed or no pre-packaged retail commodities found on premises',
                signature_status: 'unavailable',
                notes: 'Store closed or no pre-packaged retail stock available for sampling.',
                finished_at: new Date().toISOString(),
              });
            },
          },
          { text: 'Keep Scanning', style: 'cancel' },
        ]
      );
      return;
    }

    onCompleteInspection({
      ...inspectionSession,
      scans: packages,
      finished_at: new Date().toISOString(),
    });
  };

  const handleRecordRefusal = () => {
    Alert.alert(
      'Record Owner Refusal',
      'Conclude this visit immediately and record that the merchant/shopkeeper refused inspection?',
      [
        {
          text: 'Record Refusal & Conclude',
          style: 'destructive',
          onPress: () => {
            onCompleteInspection({
              ...inspectionSession,
              scans: packages,
              refusal_reason: 'Merchant / Shop owner refused statutory inspection under Legal Metrology Act',
              signature_status: 'refused',
              notes: 'Merchant / Shop owner refused statutory inspection under Legal Metrology Act, 2009.',
              finished_at: new Date().toISOString(),
            });
          },
        },
        { text: 'Cancel', style: 'cancel' },
      ]
    );
  };

  const capturedCount = Object.keys(panelPhotos).length;

  return (
    <View style={styles.container}>
      <Header
        title="Active Inspection Visit"
        subtitle={`${inspectionSession?.store?.name || 'Retail Store'} • Step 2 of 3`}
      />

      <ScrollView style={styles.scroll} contentContainerStyle={styles.content}>
        {/* Store Summary Banner */}
        <Card padding="sm" style={styles.storeBanner}>
          <View style={styles.rowBetween}>
            <View style={{ flex: 1 }}>
              <Text style={styles.storeBannerTitle}>{inspectionSession?.store?.name}</Text>
              <Text style={styles.storeBannerSub}>
                {inspectionSession?.store?.city || 'Telangana'} • {inspectionSession?.transaction_type}
              </Text>
            </View>
            <View style={styles.packageCountBadge}>
              <Text style={styles.packageCountText}>{packages.length} Packages Inspected</Text>
            </View>
          </View>
        </Card>

        {/* Existing Scanned Packages List */}
        {packages.length > 0 && (
          <View style={{ marginBottom: spacing.md }}>
            <Text style={styles.sectionHeader}>PACKAGES SAMPLED THIS VISIT</Text>
            {packages.map((pkg, idx) => {
              const bName = (pkg.brand_name && !pkg.brand_name.toLowerCase().includes('unspecified')) ? pkg.brand_name.trim() : null;
              const cName = (pkg.commodity_generic && !pkg.commodity_generic.toLowerCase().includes('unspecified')) ? pkg.commodity_generic.trim() : null;
              const pkgTitle = bName && cName ? `${bName} — ${cName}` : (cName || (bName ? `${bName} • Commodity not determined` : 'Commodity not determined'));
              const checksCount = pkg.checks_total || (Array.isArray(pkg.findings) ? pkg.findings.length : 26);

              return (
                <Card key={pkg.id || idx} padding="sm" style={styles.packageCard}>
                  <View style={styles.rowBetween}>
                    <View style={{ flex: 1 }}>
                      <Text style={{ fontSize: 13, fontWeight: '700', color: colors.text }}>
                        #{idx + 1}: {pkgTitle}
                      </Text>
                      <Text style={{ fontSize: 11, color: colors.textMuted, marginTop: 1 }}>
                        Batch: {(pkg.batch_number && pkg.batch_number !== 'N/A' && !pkg.batch_number.toLowerCase().includes('unspecified')) ? pkg.batch_number : 'Batch not observed'} • {checksCount} Checks Evaluated
                      </Text>
                    </View>
                  <View style={[styles.rowAlign, { gap: 6 }]}>
                    <VerdictBadge result={pkg.overall_result || 'not_assessed'} size="sm" />
                    <VerdictBadge status={pkg.server_id ? 'synced' : 'not_synced'} size="sm" />
                    {onViewFindings && (
                      <Pressable
                        onPress={() => onViewFindings(pkg)}
                        style={styles.viewFindingsBtn}
                        hitSlop={8}
                      >
                        <Text style={styles.viewFindingsText}>Findings →</Text>
                      </Pressable>
                    )}
                  </View>
                </View>
              </Card>
            );
          })}
          </View>
        )}

        {/* Current Package Intake Card */}
        <Text style={styles.sectionHeader}>
          {packages.length === 0 ? 'SCAN FIRST SAMPLE PACKAGE' : `SCAN PACKAGE #${packages.length + 1}`}
        </Text>

        {/* Panel Selector Chips */}
        <View style={styles.panelSelector}>
          {PANELS.map((p) => {
            const hasPhoto = !!panelPhotos[p.key];
            const isAct = activePanel === p.key;
            return (
              <Pressable
                key={p.key}
                onPress={() => setActivePanel(p.key)}
                style={[
                  styles.panelChip,
                  isAct && styles.panelChipActive,
                  hasPhoto && styles.panelChipDone,
                ]}
              >
                <Text
                  style={[
                    styles.panelChipText,
                    isAct && styles.panelChipTextActive,
                    hasPhoto && styles.panelChipTextDone,
                  ]}
                >
                  {hasPhoto ? '✓ ' : ''}{p.label}
                </Text>
              </Pressable>
            );
          })}
        </View>

        {/* Camera Viewport or Permissions Request */}
        <Card padding="none" style={styles.cameraCard}>
          {!permission?.granted ? (
            <View style={styles.permissionBox}>
              <Text style={{ fontSize: 32, marginBottom: spacing.sm }}>📷</Text>
              <Text style={[typography.h4, { textAlign: 'center', marginBottom: spacing.xs }]}>
                Camera Access Needed
              </Text>
              <Text style={[typography.caption, { textAlign: 'center', marginBottom: spacing.md }]}>
                Legal Metrology statutory inspection requires camera access to capture evidence panels.
              </Text>
              <PrimaryButton title="Grant Camera Permission" onPress={requestPermission} />
            </View>
          ) : panelPhotos[activePanel] ? (
            <View style={styles.previewBox}>
              <Image source={{ uri: panelPhotos[activePanel] }} style={styles.previewImage} resizeMode="cover" />
              <View style={styles.previewOverlay}>
                <View style={{ flex: 1, marginRight: spacing.sm }}>
                  <Text style={styles.previewLabel}>{PANELS.find((p) => p.key === activePanel)?.label} Captured</Text>
                  {panelEvidence[activePanel]?.quality_gate && (
                    <View style={[
                      styles.qualityBadge,
                      panelEvidence[activePanel].quality_gate.decision === 'READY' && styles.qualityBadgeReady,
                      panelEvidence[activePanel].quality_gate.decision === 'READY_WITH_WARNINGS' && styles.qualityBadgeWarning,
                      panelEvidence[activePanel].quality_gate.decision === 'RETAKE_REQUIRED' && styles.qualityBadgeRetake,
                    ]}>
                      <Text style={[
                        styles.qualityBadgeText,
                        panelEvidence[activePanel].quality_gate.decision === 'READY' && styles.qualityTextReady,
                        panelEvidence[activePanel].quality_gate.decision === 'READY_WITH_WARNINGS' && styles.qualityTextWarning,
                        panelEvidence[activePanel].quality_gate.decision === 'RETAKE_REQUIRED' && styles.qualityTextRetake,
                      ]}>
                        {panelEvidence[activePanel].quality_gate.decision === 'READY'
                          ? '✓ Image ready'
                          : panelEvidence[activePanel].quality_gate.decision === 'READY_WITH_WARNINGS'
                          ? `⚠️ Usable: ${panelEvidence[activePanel].quality_gate.primary_guidance}`
                          : `⛔ Retake: ${panelEvidence[activePanel].quality_gate.primary_guidance}`}
                      </Text>
                    </View>
                  )}
                </View>
                <Pressable
                  onPress={() => {
                    setPanelPhotos((prev) => ({ ...prev, [activePanel]: null }));
                    setPanelEvidence((prev) => ({ ...prev, [activePanel]: null }));
                  }}
                  style={[
                    styles.retakeBtn,
                    panelEvidence[activePanel]?.quality_gate?.decision === 'RETAKE_REQUIRED' && styles.retakeBtnUrgent,
                  ]}
                  accessibilityRole="button"
                  accessibilityLabel="Retake photo"
                >
                  <Text style={[
                    styles.retakeText,
                    panelEvidence[activePanel]?.quality_gate?.decision === 'RETAKE_REQUIRED' && styles.retakeTextUrgent,
                  ]}>
                    Retake Photo
                  </Text>
                </Pressable>
              </View>
            </View>
          ) : (
            <View style={styles.cameraBox}>
              <CameraView
                ref={cameraRef}
                style={styles.cameraView}
                facing="back"
                onCameraReady={() => setCameraReady(true)}
                onMountError={(e) => setCameraError(e?.message || 'The camera could not be started on this device.')}
              />
              {/* Viewfinder status. Without it a preview that is slow to open —
                  or a camera the OS refuses — is a plain black rectangle, which
                  is what makes a screen-recorded demo look "hidden". */}
              {!cameraReady && !cameraError && (
                <View style={styles.cameraStatusOverlay} pointerEvents="none">
                  <ActivityIndicator color={colors.saffron} />
                  <Text style={styles.cameraStatusText}>Starting camera…</Text>
                </View>
              )}
              {!!cameraError && (
                <View style={styles.cameraStatusOverlay}>
                  <Text style={styles.cameraStatusText}>{cameraError}</Text>
                  <Text style={[styles.cameraStatusText, styles.cameraStatusHint]}>
                    Retake after closing other camera apps, or record the reading manually.
                  </Text>
                </View>
              )}
              {/* Guidance overlay bracket */}
              <View style={styles.bracketOverlay} pointerEvents="none">
                <View style={styles.bracketGuide}>
                  <Text style={styles.bracketGuideText}>
                    Align {PANELS.find((p) => p.key === activePanel)?.label} within frame
                  </Text>
                </View>
              </View>
              {/* Shutter bar */}
              <View style={styles.shutterBar}>
                <Pressable
                  onPress={handleCapturePhoto}
                  disabled={capturing || !cameraReady || !!cameraError}
                  style={[styles.shutterBtn, (!cameraReady || !!cameraError) && styles.shutterBtnDisabled]}
                  accessibilityRole="button"
                  accessibilityLabel="Capture photo"
                  accessibilityState={{ disabled: capturing || !cameraReady || !!cameraError }}
                >
                  {capturing ? (
                    <ActivityIndicator color={colors.white} />
                  ) : (
                    <View style={styles.shutterInner} />
                  )}
                </Pressable>
              </View>
            </View>
          )}
        </Card>

        {/* Package Metadata Form */}
        <Text style={[styles.sectionTitle, { marginTop: spacing.md }]}>PACKAGE PARTICULARS</Text>
        <Card padding="md" style={{ marginBottom: spacing.md }}>
          <View style={styles.rowBetween}>
            <View style={{ flex: 1, marginRight: spacing.sm }}>
              <Text style={styles.inputLabel}>Commodity Generic Name</Text>
              <TextInput
                placeholder="e.g. Wheat Flour, Biscuits"
                placeholderTextColor={colors.placeholder}
                value={commodity}
                onChangeText={setCommodity}
                style={styles.input}
              />
            </View>
            <View style={{ flex: 1 }}>
              <Text style={styles.inputLabel}>Brand Name</Text>
              <TextInput
                placeholder="e.g. Aashirvaad"
                placeholderTextColor={colors.placeholder}
                value={brand}
                onChangeText={setBrand}
                style={styles.input}
              />
            </View>
          </View>

          <View style={{ marginTop: spacing.sm }}>
            <Text style={styles.inputLabel}>Batch / Lot Number</Text>
            <TextInput
              placeholder="e.g. B-2026-08 (from barcode/inkjet)"
              placeholderTextColor={colors.placeholder}
              value={batch}
              onChangeText={setBatch}
              style={styles.input}
            />
          </View>

          {/* Statutory Scope Flags */}
          <Text style={[styles.inputLabel, { marginTop: spacing.md, marginBottom: spacing.xs }]}>
            STATUTORY CONDITIONS (RULE 6)
          </Text>
          <View style={styles.flagsGrid}>
            <Pressable
              onPress={() => setIsImported(!isImported)}
              style={[styles.flagPill, isImported && styles.flagPillActive]}
            >
              <Text style={[styles.flagText, isImported && styles.flagTextActive]}>
                {isImported ? '✓ ' : ''}Imported Package (Rule 6(1)(a))
              </Text>
            </Pressable>

            <Pressable
              onPress={() => setIsPerishable(!isPerishable)}
              style={[styles.flagPill, isPerishable && styles.flagPillActive]}
            >
              <Text style={[styles.flagText, isPerishable && styles.flagTextActive]}>
                {isPerishable ? '✓ ' : ''}Perishable Food (Best Before)
              </Text>
            </Pressable>

            <Pressable
              onPress={() => setHasSticker(!hasSticker)}
              style={[styles.flagPill, hasSticker && styles.flagPillAlert]}
            >
              <Text style={[styles.flagText, hasSticker && styles.flagTextAlert]}>
                {hasSticker ? '⚠️ ' : ''}Price Sticker Affixed (Section 36)
              </Text>
            </Pressable>
          </View>
        </Card>

        {/* Package Action Buttons */}
        <View style={{ gap: spacing.sm, marginBottom: spacing.xl }}>
          <PrimaryButton
            title={assessing ? 'Evaluating Statutory Checks…' : 'Assess Package (Statutory Rule Checks) →'}
            onPress={handleAssessCurrentPackage}
            disabled={assessing || capturedCount === 0}
            accessibilityLabel="Run Legal Metrology statutory checks"
          />

          <Pressable
            onPress={handleFinishInspection}
            style={styles.finishBtn}
            accessibilityRole="button"
            accessibilityLabel="Finish visit and submit all packages"
          >
            <Text style={styles.finishBtnText}>
              ✓ Conclude Inspection Visit ({packages.length} Packages Ready) →
            </Text>
          </Pressable>

          <Pressable
            onPress={handleRecordRefusal}
            style={styles.refusalBtn}
            accessibilityRole="button"
            accessibilityLabel="Record merchant refusal"
          >
            <Text style={styles.refusalBtnText}>
              🚫 Record Merchant Refusal / Non-Cooperation
            </Text>
          </Pressable>
        </View>
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
    paddingBottom: spacing.xxxl + 64,
  },
  storeBanner: {
    backgroundColor: colors.surface,
    borderColor: colors.borderLight,
    borderWidth: 1,
    marginBottom: spacing.md,
  },
  storeBannerTitle: {
    fontSize: 15,
    fontWeight: '700',
    color: colors.niyamBlue,
  },
  storeBannerSub: {
    fontSize: 11,
    color: colors.textMuted,
    marginTop: 1,
  },
  packageCountBadge: {
    backgroundColor: colors.info.fill,
    borderColor: colors.info.border,
    borderWidth: 1,
    paddingHorizontal: 8,
    paddingVertical: 4,
    borderRadius: radius.sm,
  },
  packageCountText: {
    fontSize: 11,
    fontWeight: '700',
    color: colors.info.text,
  },
  sectionHeader: {
    fontSize: 11,
    fontWeight: '700',
    color: colors.textMuted,
    letterSpacing: 0.8,
    marginBottom: spacing.xs,
    textTransform: 'uppercase',
  },
  packageCard: {
    marginBottom: spacing.xs,
  },
  viewFindingsBtn: {
    marginLeft: spacing.sm,
    paddingHorizontal: 8,
    paddingVertical: 4,
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderWidth: 1,
    borderRadius: radius.sm,
  },
  viewFindingsText: {
    fontSize: 11,
    fontWeight: '600',
    color: colors.netraTeal,
  },
  panelSelector: {
    flexDirection: 'row',
    gap: spacing.xs,
    marginBottom: spacing.sm,
  },
  panelChip: {
    flex: 1,
    paddingVertical: 7,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: colors.white,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: radius.sm,
  },
  panelChipActive: {
    borderColor: colors.netraTeal,
    backgroundColor: '#F0FDFA',
  },
  panelChipDone: {
    borderColor: colors.pass.border,
    backgroundColor: colors.pass.fill,
  },
  panelChipText: {
    fontSize: 10,
    fontWeight: '600',
    color: colors.textMuted,
  },
  panelChipTextActive: {
    color: colors.netraTeal,
    fontWeight: '700',
  },
  panelChipTextDone: {
    color: colors.pass.text,
    fontWeight: '700',
  },
  cameraCard: {
    height: 240,
    overflow: 'hidden',
    borderRadius: radius.md,
    backgroundColor: '#000',
    marginBottom: spacing.md,
  },
  permissionBox: {
    flex: 1,
    backgroundColor: colors.surface,
    padding: spacing.lg,
    alignItems: 'center',
    justifyContent: 'center',
  },
  cameraBox: {
    flex: 1,
    position: 'relative',
  },
  cameraView: {
    flex: 1,
  },
  // Status placed over the black viewfinder: "Starting camera…" until the first
  // frame, and the failure reason when the camera cannot start at all.
  cameraStatusOverlay: {
    ...StyleSheet.absoluteFillObject,
    alignItems: 'center',
    justifyContent: 'center',
    paddingHorizontal: spacing.lg,
    backgroundColor: 'rgba(0,0,0,0.55)',
  },
  cameraStatusText: {
    color: colors.white,
    fontSize: 13,
    fontWeight: '600',
    textAlign: 'center',
    marginTop: spacing.xs,
  },
  cameraStatusHint: {
    fontSize: 10.5,
    fontWeight: '400',
    color: 'rgba(255,255,255,0.75)',
    marginTop: spacing.xs,
  },
  shutterBtnDisabled: {
    opacity: 0.4,
  },
  bracketOverlay: {
    ...StyleSheet.absoluteFillObject,
    alignItems: 'center',
    justifyContent: 'center',
  },
  bracketGuide: {
    width: '78%',
    height: '68%',
    borderWidth: 1.5,
    borderColor: colors.saffron,
    borderRadius: radius.md,
    borderStyle: 'dashed',
    alignItems: 'center',
    justifyContent: 'flex-end',
    paddingBottom: 8,
  },
  bracketGuideText: {
    color: colors.white,
    fontSize: 10,
    fontWeight: '700',
    backgroundColor: 'rgba(0,0,0,0.6)',
    paddingHorizontal: 6,
    paddingVertical: 2,
    borderRadius: radius.sm,
  },
  shutterBar: {
    position: 'absolute',
    bottom: 12,
    left: 0,
    right: 0,
    alignItems: 'center',
  },
  shutterBtn: {
    width: 56,
    height: 56,
    borderRadius: 28,
    borderWidth: 3,
    borderColor: colors.white,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: 'rgba(255,255,255,0.2)',
  },
  shutterInner: {
    width: 42,
    height: 42,
    borderRadius: 21,
    backgroundColor: colors.white,
  },
  previewBox: {
    flex: 1,
    position: 'relative',
  },
  previewImage: {
    width: '100%',
    height: '100%',
    // NOTE: resizeMode is a PROP on <Image>, not a style key. React Native
    // 0.81 silently ignores it here, which left the evidence preview showing at
    // the wrong scale inside the fixed-height viewfinder card. The prop is set
    // where the image is rendered.
  },
  previewOverlay: {
    position: 'absolute',
    bottom: 0,
    left: 0,
    right: 0,
    backgroundColor: 'rgba(15,42,68,0.85)',
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.sm,
  },
  previewLabel: {
    color: colors.white,
    fontSize: 12,
    fontWeight: '700',
  },
  retakeBtn: {
    backgroundColor: colors.surface,
    paddingHorizontal: 10,
    paddingVertical: 4,
    borderRadius: radius.sm,
  },
  retakeText: {
    color: colors.niyamBlue,
    fontSize: 11,
    fontWeight: '700',
  },
  retakeBtnUrgent: {
    backgroundColor: '#dc2626',
    borderWidth: 1,
    borderColor: '#fca5a5',
  },
  retakeTextUrgent: {
    color: colors.white,
    fontWeight: '800',
  },
  qualityBadge: {
    paddingHorizontal: 8,
    paddingVertical: 3,
    borderRadius: radius.xs,
    marginTop: 3,
    alignSelf: 'flex-start',
  },
  qualityBadgeReady: {
    backgroundColor: 'rgba(34, 197, 94, 0.25)',
    borderColor: '#22c55e',
    borderWidth: 1,
  },
  qualityBadgeWarning: {
    backgroundColor: 'rgba(234, 179, 8, 0.25)',
    borderColor: '#eab308',
    borderWidth: 1,
  },
  qualityBadgeRetake: {
    backgroundColor: 'rgba(239, 68, 68, 0.25)',
    borderColor: '#ef4444',
    borderWidth: 1,
  },
  qualityBadgeText: {
    fontSize: 10,
    fontWeight: '700',
  },
  qualityTextReady: {
    color: '#4ade80',
  },
  qualityTextWarning: {
    color: '#fde047',
  },
  qualityTextRetake: {
    color: '#f87171',
  },
  sectionTitle: {
    fontSize: 11,
    fontWeight: '700',
    color: colors.textMuted,
    letterSpacing: 0.8,
    marginBottom: spacing.xs,
    textTransform: 'uppercase',
  },
  inputLabel: {
    fontSize: 11,
    fontWeight: '600',
    color: colors.textSecondary,
    marginBottom: 2,
  },
  input: {
    height: 38,
    borderColor: colors.border,
    borderWidth: 1,
    borderRadius: radius.sm,
    paddingHorizontal: spacing.sm,
    fontSize: 13,
    color: colors.text,
    backgroundColor: colors.white,
  },
  flagsGrid: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: spacing.xs,
  },
  flagPill: {
    paddingHorizontal: spacing.sm,
    paddingVertical: 6,
    borderRadius: radius.sm,
    borderWidth: 1,
    borderColor: colors.border,
    backgroundColor: colors.white,
  },
  flagPillActive: {
    borderColor: colors.netraTeal,
    backgroundColor: '#F0FDFA',
  },
  flagPillAlert: {
    borderColor: colors.violation.border,
    backgroundColor: colors.violation.fill,
  },
  flagText: {
    fontSize: 11,
    color: colors.textSecondary,
    fontWeight: '600',
  },
  flagTextActive: {
    color: colors.netraTeal,
    fontWeight: '700',
  },
  flagTextAlert: {
    color: colors.violation.text,
    fontWeight: '700',
  },
  finishBtn: {
    backgroundColor: colors.surface,
    borderColor: colors.netraTeal,
    borderWidth: 1.5,
    borderRadius: radius.md,
    paddingVertical: spacing.md,
    alignItems: 'center',
    justifyContent: 'center',
  },
  finishBtnText: {
    color: colors.netraTeal,
    fontSize: 14,
    fontWeight: '700',
  },
  refusalBtn: {
    backgroundColor: '#FEF2F2',
    borderColor: colors.violation.border,
    borderWidth: 1,
    borderRadius: radius.md,
    paddingVertical: spacing.md,
    alignItems: 'center',
    justifyContent: 'center',
    marginTop: spacing.xs,
  },
  refusalBtnText: {
    color: colors.violation.text,
    fontSize: 13,
    fontWeight: '700',
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
});
