import React, { useRef, useState, useEffect, useMemo } from 'react';
import {
  View,
  Text,
  ScrollView,
  Pressable,
  Modal,
  TextInput,
  Alert,
  ActivityIndicator,
  StyleSheet,
  Image,
} from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { colors, spacing, typography, radius, shadows } from '../../theme';
import Header from '../../components/Header';
import Card from '../../components/Card';
import VerdictBadge from '../../components/VerdictBadge';
import PrimaryButton from '../../components/PrimaryButton';
import { overrideFinding, assessScan, fetchScanDetails, verifyEvidence, fetchRuleInfo } from '../../api/inspections';
import { API_BASE_URL } from '../../api/config';
import { getAccessToken } from '../../api/client';

// The 26 authoritative statutory checks under active rule pack 2026.09.v1
export const STATUTORY_CHECKS_MASTER = [
  {
    code: 'CHK01',
    name: 'Mandatory Declarations on Retail Pre-Packaged Commodity',
    citation: 'Rule 6(1)',
    defaultObserved: 'Pending OCR extraction from principal display panel',
    required: 'All statutory declarations present on PDP',
    severity: 'critical',
  },
  {
    code: 'CHK02',
    name: 'Tobacco Products Exemption Carve-Out',
    citation: 'Rule 26(a)',
    defaultObserved: 'Scope verification',
    required: 'Tobacco carve-out applicability verification',
    severity: 'advisory',
  },
  {
    code: 'CHK03',
    name: 'Chapter II Scope & Quantity Thresholds',
    citation: 'Rule 3 — Retail Sale Scope',
    defaultObserved: 'Retail sale transaction verification',
    required: 'Pre-packaged commodity intended for retail sale',
    severity: 'critical',
  },
  {
    code: 'CHK04',
    name: 'Retail Sale Price (MRP) Correctly Expressed',
    citation: 'Rule 6(1)(e) read with Rule 2(m)',
    defaultObserved: 'Pending OCR extraction from MRP panel',
    required: 'MRP in Indian Rupees inclusive of all taxes',
    severity: 'critical',
  },
  {
    code: 'CHK05',
    name: 'Prescribed Standard Units of Weight, Volume, or Length',
    citation: 'Rule 12 & Rule 13',
    defaultObserved: 'Pending metric unit verification',
    required: 'Standard metric units without non-standard qualifiers',
    severity: 'critical',
  },
  {
    code: 'CHK06',
    name: 'Minimum Height of Letters on PDP',
    citation: 'Rule 7(1) & Table-I',
    defaultObserved: 'Pending optical font height verification',
    required: 'Letter height proportion matching Table-I standards',
    severity: 'major',
  },
  {
    code: 'CHK06b',
    name: 'Minimum Height of Net Quantity Numerals',
    citation: 'Rule 7(2) read with Table-I (GSR 629(E))',
    defaultObserved: 'Pending numeral height measurement',
    required: 'Numeral height matching Table-I standards',
    severity: 'major',
  },
  {
    code: 'CHK06b_hist',
    name: 'Historical Minimum Height of Numerals (Pre-2018 Table-II)',
    citation: 'Rule 7 & Table-II (pre-2018)',
    defaultObserved: 'Historical table reference',
    required: 'Pre-2018 historical numeral standard check',
    severity: 'major',
  },
  {
    code: 'CHK07',
    name: 'Character Width Proportion (>= 1/3 height)',
    citation: 'Rule 7(3)',
    defaultObserved: 'Pending character width calculation',
    required: 'Width at least one-third of character height',
    severity: 'minor',
  },
  {
    code: 'CHK08',
    name: 'Conspicuous Contrast with Background',
    citation: 'Rule 9(1)',
    defaultObserved: 'Pending contrast ratio evaluation',
    required: 'High visual contrast against background',
    severity: 'minor',
  },
  {
    code: 'CHK09',
    name: 'Clear Surrounding Space Around Net Quantity',
    citation: 'Rule 8',
    defaultObserved: 'Pending clear space measurement',
    required: 'Unobstructed surrounding boundary',
    severity: 'minor',
  },
  {
    code: 'CHK22',
    name: 'Placement of Mandatory Declarations on PDP',
    citation: 'Rule 8',
    defaultObserved: 'Pending PDP layout analysis',
    required: 'Mandatory declarations grouped on PDP',
    severity: 'major',
  },
  {
    code: 'CHK10',
    name: 'Standard Prescribed Packaging Quantities',
    citation: 'Rule 5 & Second Schedule',
    defaultObserved: 'Pending schedule comparison',
    required: 'Standard quantity schedules under Second Schedule',
    severity: 'minor',
  },
  {
    code: 'CHK11',
    name: 'Permissible Conditions for Price Alteration Stickers',
    citation: 'Rule 6(3), 6(4), 6(4A)',
    defaultObserved: 'Declared sticker inspection',
    required: 'Declarations must be indelible; no unauthorized stickers',
    severity: 'critical',
  },
  {
    code: 'CHK12',
    name: 'Country of Origin Declaration on Imported Packages',
    citation: 'Rule 6(1)(aa)',
    defaultObserved: 'Pending origin declaration check',
    required: 'Country of origin stated for all packages',
    severity: 'critical',
  },
  {
    code: 'CHK13',
    name: 'Best Before or Use By Date for Perishables',
    citation: 'Rule 6(1)(da)',
    defaultObserved: 'Pending date verification',
    required: 'Clear expiry or best before for perishables',
    severity: 'critical',
  },
  {
    code: 'CHK14',
    name: 'Medical Devices Applicability Proviso',
    citation: 'Rule 2(h) Proviso',
    defaultObserved: 'Scope verification',
    required: 'Medical devices regulatory carve-out',
    severity: 'advisory',
  },
  {
    code: 'CHK15',
    name: 'Mandatory Declarations on E-Commerce Listings',
    citation: 'Rule 6(10)',
    defaultObserved: 'Physical retail package sampled in store',
    required: 'E-commerce digital display declarations',
    severity: 'major',
  },
  {
    code: 'CHK16',
    name: 'E-Commerce Marketplace Search Filter for Origin',
    citation: 'Rule 6(10A)',
    defaultObserved: 'Physical retail package sampled in store',
    required: 'Search filter requirement for e-commerce platforms',
    severity: 'major',
  },
  {
    code: 'CHK17',
    name: 'Alignment with FSSAI Packaging Advisories',
    citation: 'FSSAI Packaging Regulations & LM Alignment',
    defaultObserved: 'Pending regulatory review',
    required: 'Food safety advisory alignment',
    severity: 'advisory',
  },
  {
    code: 'CHK18',
    name: 'Graduated Enforcement Response & Section 36 Sanctions',
    citation: 'Section 36, Legal Metrology Act 2009',
    defaultObserved: 'Pending statutory review',
    required: 'Statutory penalty classification',
    severity: 'critical',
  },
  {
    code: 'CHK19',
    name: 'Unit Sale Price (USP) Declaration',
    citation: 'Rule 6(1)(g)',
    defaultObserved: 'Pending USP extraction',
    required: 'Unit sale price declared in Rs per g/ml/piece',
    severity: 'major',
  },
  {
    code: 'CHK20',
    name: 'Dimensions Declaration Where Size is Relevant',
    citation: 'Rule 6(1)(m)',
    defaultObserved: 'Pending dimension extraction',
    required: 'Dimensions declared in metric units',
    severity: 'minor',
  },
  {
    code: 'CHK21',
    name: 'Special Standards for Garments and Hosiery',
    citation: 'Rule 6(1)(b) Proviso & Second Schedule Exemption',
    defaultObserved: 'Non-apparel commodity',
    required: 'Garments and hosiery size standards',
    severity: 'major',
  },
  {
    code: 'CHK23_hist',
    name: 'Origin Marking on Cosmetics (Former Rule 6(8))',
    citation: 'Rule 6(8) (omitted w.e.f 21.09.2026 by GSR 826(E))',
    defaultObserved: 'Historical rule check',
    required: 'Historical cosmetics origin indicator',
    severity: 'major',
  },
  {
    code: 'CHK23',
    name: 'Origin Marking on Soap, Cosmetics, Toiletries',
    citation: 'Rule 6(4A)(d)',
    defaultObserved: 'Pending visual inspection',
    required: 'Vegetarian / non-vegetarian dot on specified items',
    severity: 'advisory',
  },
];

export function formatFinding(f, idx = 0) {
  if (!f) return null;
  const srvId = typeof f.id === 'number' && f.id > 0 ? f.id : (typeof f.server_id === 'number' && f.server_id > 0 ? f.server_id : null);
  const checkId = f.check_id || f.code || f.rule_id || `CHK${String(idx + 1).padStart(2, '0')}`;
  const effective = f.effective_verdict || f.human_verdict || f.engine_verdict || f.verdict || f.finding_status || 'not_assessed';
  const engine = f.engine_verdict || f.verdict || 'not_assessed';
  const isFail = effective === 'fail' || effective === 'violation';

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
    observed: f.observed || 'No declaration observed on packaging',
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

export function buildInitialFindings(scan) {
  if (Array.isArray(scan?.findings) && scan.findings.length > 0) {
    return formatFindings(scan.findings);
  }
  const hasSticker = scan?.has_sticker;
  return STATUTORY_CHECKS_MASTER.map((m, idx) => ({
    id: idx + 1,
    server_id: null,
    check_id: m.code,
    rule_id: m.code,
    title: m.name,
    citation: m.citation,
    engine_verdict: 'not_assessed',
    effective_verdict: 'not_assessed',
    human_verdict: null,
    observed: m.code === 'CHK11' && hasSticker ? 'Sticker found affixed over package' : m.defaultObserved,
    required: m.required,
    severity: m.severity,
    reason: m.code === 'CHK11' && hasSticker ? 'Sticker declared on package — pending server assessment.' : 'Pending server assessment.',
    remediation: null,
    evidence_references: [m.code],
  }));
}

export default function FindingsScreen({ scan, onBack, onSaveFindings }) {
  const insets = useSafeAreaInsets();
  const safeBottom = Math.max(insets.bottom || 0, 24);

  const [currentScan, setCurrentScan] = useState(scan);
  const [findings, setFindings] = useState(() => buildInitialFindings(scan));

  // Override modal state
  const [selectedFinding, setSelectedFinding] = useState(null);
  const [overrideVerdict, setOverrideVerdict] = useState('pass');
  const [overrideReason, setOverrideReason] = useState('');
  const [submittingOverride, setSubmittingOverride] = useState(false);
  const [reassessing, setReassessing] = useState(false);
  const [verifyingEvidence, setVerifyingEvidence] = useState(false);
  const [evidenceIntegrity, setEvidenceIntegrity] = useState(null);
  const [selectedImage, setSelectedImage] = useState(null);
  const reassessBusyRef = useRef(false);

  // Sync state when scan prop changes
  useEffect(() => {
    setCurrentScan(scan);
    setFindings(buildInitialFindings(scan));
    setEvidenceIntegrity(null);
    setSelectedFinding(null);
    setSelectedImage(null);
  }, [scan?.id, scan?.server_id]);

  const scanId = currentScan?.server_id || (typeof currentScan?.id === 'number' && currentScan.id > 0 ? currentScan.id : null) || scan?.server_id || (typeof scan?.id === 'number' && scan.id > 0 ? scan.id : null);

  const evidenceImages = useMemo(() => {
    const list = [];
    const sourceImages = currentScan?.images || scan?.images;
    const token = getAccessToken();

    const buildImageUrl = (u) => {
      if (!u) return null;
      return (u.startsWith('http://') || u.startsWith('https://') || u.startsWith('file://'))
        ? u
        : `${API_BASE_URL.replace(/\/+$/, '')}/${u.replace(/^\/+/, '')}`;
    };

    if (Array.isArray(sourceImages) && sourceImages.length > 0) {
      // Canonical panel sorting: front, back, mrp, batch, side, other
      const panelOrder = { front: 1, back: 2, mrp: 3, batch: 4, side: 5, other: 6 };
      const sorted = [...sourceImages].sort((a, b) => {
        const orderA = panelOrder[a.panel?.toLowerCase()] || 99;
        const orderB = panelOrder[b.panel?.toLowerCase()] || 99;
        return orderA - orderB || (a.sequence || 0) - (b.sequence || 0);
      });

      sorted.forEach((img, i) => {
        let rawUrl = img.url || (img.id && scanId ? `/scans/${scanId}/images/${img.id}` : null);
        let rawThumbUrl = img.thumbnail_url || (img.id && scanId ? `/scans/${scanId}/images/${img.id}/thumbnail` : rawUrl);

        list.push({
          id: img.id ?? i,
          panel: img.panel || `panel_${i + 1}`,
          url: buildImageUrl(rawUrl),
          thumbnail_url: buildImageUrl(rawThumbUrl),
          sha256: img.sha256 || null,
        });
      });
    } else if (currentScan?.panelPhotos || scan?.panelPhotos) {
      const photos = currentScan?.panelPhotos || scan?.panelPhotos;
      const canonicalPanels = ['front', 'back', 'mrp', 'batch'];
      canonicalPanels.forEach((p, i) => {
        const uri = photos[p];
        if (uri) {
          list.push({
            id: `local-${p}`,
            panel: p,
            url: uri,
            thumbnail_url: uri,
            sha256: null,
          });
        }
      });
      Object.entries(photos).forEach(([p, uri]) => {
        if (uri && !canonicalPanels.includes(p)) {
          list.push({
            id: `local-${p}`,
            panel: p,
            url: uri,
            thumbnail_url: uri,
            sha256: null,
          });
        }
      });
    } else if (evidenceIntegrity?.images && Array.isArray(evidenceIntegrity.images)) {
      evidenceIntegrity.images.forEach((img, i) => {
        list.push({
          id: img.image_id || i,
          panel: img.panel || `panel_${i + 1}`,
          url: buildImageUrl(img.thumbnail_url),
          thumbnail_url: buildImageUrl(img.thumbnail_url),
          sha256: img.sha256_recorded,
        });
      });
    }
    return list;
  }, [currentScan, scan, scanId, evidenceIntegrity]);

  const handleVerifyEvidence = async () => {
    if (!scanId) {
      Alert.alert(
        'Offline Evidence',
        'This package was recorded locally. SHA-256 evidence integrity will be verified against the server once synchronized.'
      );
      return;
    }
    setVerifyingEvidence(true);
    try {
      const res = await verifyEvidence(scanId);
      setEvidenceIntegrity(res);
      if (res?.all_intact) {
        Alert.alert(
          'Evidence Integrity Verified',
          `All ${res.images?.length || 0} panel photo(s) verified.\n\nCryptographic SHA-256 hashes match original stored evidence on disk.`
        );
      } else {
        Alert.alert(
          'Integrity Status',
          `Verification complete: ${res.images?.filter((i) => i.sha256_matches).length || 0} of ${res.images?.length || 0} files intact.`
        );
      }
    } catch (err) {
      Alert.alert('Verification Error', err?.message || 'Could not verify evidence integrity.');
    } finally {
      setVerifyingEvidence(false);
    }
  };

  // Auto-sync fresh findings and scan details from server on mount
  useEffect(() => {
    if (!scanId) return;
    let active = true;
    (async () => {
      try {
        const fresh = await fetchScanDetails(scanId);
        if (active && fresh) {
          setCurrentScan(fresh);
          if (Array.isArray(fresh.findings) && fresh.findings.length > 0) {
            const formatted = formatFindings(fresh.findings);
            setFindings(formatted);
            if (onSaveFindings) {
              onSaveFindings(formatted);
            }
          }
        }
      } catch (e) {
        // silent fallback
      }
    })();
    return () => { active = false; };
  }, [scanId]);

  const handleTriggerReassess = async () => {
    if (!scanId) {
      Alert.alert(
        'Offline Package',
        'This package was recorded in offline mode without a server scan ID. It will be assessed once synchronized with the server.'
      );
      return;
    }

    if (reassessBusyRef.current) return;
    reassessBusyRef.current = true;
    setReassessing(true);
    try {
      const assessed = await assessScan(scanId);
      if (assessed) {
        setCurrentScan(assessed);
        if (Array.isArray(assessed.findings) && assessed.findings.length > 0) {
          const formatted = formatFindings(assessed.findings);
          setFindings(formatted);
          if (onSaveFindings) {
            onSaveFindings(formatted);
          }
          const pCount = formatted.filter((f) => f.effective_verdict === 'pass').length;
          const fCount = formatted.filter((f) => f.effective_verdict === 'fail').length;
          const nCount = formatted.filter((f) => f.effective_verdict === 'not_assessed').length;
          const overallDisplay = assessed.overall_result ? assessed.overall_result.toUpperCase() : 'COMPLETED';
          Alert.alert(
            'Assessment Complete',
            `Server assessment finished.\nVerdict: ${overallDisplay}\n\n✓ ${pCount} Compliant • ⚠️ ${fCount} Violation • ⚪ ${nCount} Not Assessed`
          );
        }
      }
    } catch (err) {
      Alert.alert('Reassessment Error', err?.message || 'Server assessment could not be completed.');
    } finally {
      setReassessing(false);
      reassessBusyRef.current = false;
    }
  };

  const handleOpenOverride = (finding) => {
    setSelectedFinding(finding);
    setOverrideVerdict(finding.human_verdict || finding.effective_verdict || 'pass');
    setOverrideReason(finding.override_reason || '');
  };

  const handleSaveOverride = async () => {
    if (!selectedFinding) return;
    if (!overrideReason.trim()) {
      Alert.alert('Remark Required', 'Under Section 36 inspection rules, an officer remark or reason is required.');
      return;
    }

    setSubmittingOverride(true);
    try {
      const applyLocal = () => {
        setFindings((prev) => {
          const updated = prev.map((f) => {
            if (f.id === selectedFinding.id || f.check_id === selectedFinding.check_id) {
              return {
                ...f,
                human_verdict: overrideVerdict,
                effective_verdict: overrideVerdict,
                override_reason: overrideReason.trim(),
              };
            }
            return f;
          });
          if (onSaveFindings) onSaveFindings(updated);
          return updated;
        });
        setSelectedFinding(null);
      };

      if (selectedFinding.server_id && scanId) {
        try {
          await overrideFinding(selectedFinding.server_id, overrideVerdict, overrideReason.trim());
          Alert.alert('Remark Saved', 'Inspector remark and finding verdict successfully updated on the server.');
        } catch (e) {
          const status = e?.status ?? e?.response?.status;
          if (status === 409) {
            Alert.alert(
              'Inspection Submitted',
              'This inspection has been submitted and sealed. Remarks cannot be altered.',
            );
            return;
          }
          if (status) {
            Alert.alert(
              'Update Refused',
              String(e?.response?.data?.detail || e?.message || 'The server refused this update.'),
            );
            return;
          }
          Alert.alert(
            'Saved Locally',
            'The server could not be reached. Your remark is saved locally and will synchronize when connected.',
          );
        }
      } else {
        Alert.alert('Saved Locally', 'Your remark is recorded for this package and will be uploaded upon synchronization.');
      }
      applyLocal();
    } finally {
      setSubmittingOverride(false);
    }
  };

  const passCount = findings.filter((f) => f.effective_verdict === 'pass').length;
  const failCount = findings.filter((f) => f.effective_verdict === 'fail').length;
  const notAssessedCount = findings.filter((f) => f.effective_verdict === 'not_assessed').length;
  const notApplicableCount = findings.filter((f) => f.effective_verdict === 'not_applicable' || f.effective_verdict === 'out_of_scope').length;

  // Authoritative overall verdict derivation
  const rawOverall = currentScan?.overall_result;
  let overallResult = 'not_assessed';
  if (rawOverall === 'compliant' || rawOverall === 'pass') {
    overallResult = 'compliant';
  } else if (rawOverall === 'violation' || rawOverall === 'fail') {
    overallResult = 'violation';
  } else if (rawOverall === 'review_required' || rawOverall === 'manual_review') {
    overallResult = 'review_required';
  } else {
    if (failCount > 0) {
      overallResult = 'violation';
    } else if (findings.some((f) => f.human_verdict || f.effective_verdict === 'review_required')) {
      overallResult = 'review_required';
    } else if (passCount > 0 && notAssessedCount === 0) {
      overallResult = 'compliant';
    } else {
      overallResult = 'not_assessed';
    }
  }

  // Product identity binding — truthful fallbacks
  const brand = (currentScan?.brand_name && !currentScan.brand_name.toLowerCase().includes('unspecified')) ? currentScan.brand_name.trim() : null;
  const commodity = (currentScan?.commodity_generic && !currentScan.commodity_generic.toLowerCase().includes('unspecified')) ? currentScan.commodity_generic.trim() : null;
  const batch = (currentScan?.batch_number && currentScan.batch_number !== 'N/A' && !currentScan.batch_number.toLowerCase().includes('unspecified')) ? currentScan.batch_number.trim() : null;
  const mrp = currentScan?.mrp;
  const netQty = currentScan?.net_quantity_value ? `${currentScan.net_quantity_value} ${currentScan.net_quantity_unit || ''}`.trim() : null;

  let displaySubtitle;
  if (brand && commodity) {
    displaySubtitle = `${brand} — ${commodity}`;
  } else if (commodity) {
    displaySubtitle = commodity;
  } else if (brand) {
    displaySubtitle = `${brand} • Commodity not determined`;
  } else {
    displaySubtitle = 'Commodity not determined';
  }

  const displayBatch = `Batch: ${batch || 'Batch not observed'}${mrp ? ` • MRP: ₹${mrp}` : ''}${netQty ? ` • Net Qty: ${netQty}` : ''}`;
  const displayTitle = `Statutory Findings (${findings.length})`;
  const rulePackVersion = currentScan?.rule_pack_version || currentScan?.diagnostics?.rule_pack_version || '2026.09.v1';
  const catalogRuleCount = currentScan?.diagnostics?.catalog_rule_count || 26;
  const executableCheckCount = currentScan?.diagnostics?.executable_check_count || 19;

  // True server assessment completion status
  const hasServerAssessed = Boolean(
    currentScan?.status === 'assessed' ||
    (currentScan?.overall_result && currentScan?.overall_result !== 'not_assessed') ||
    (currentScan?.checks_assessed && currentScan?.checks_assessed > 0) ||
    (findings.some((f) => f.engine_verdict && f.engine_verdict !== 'not_assessed'))
  );

  const isAwaitingAssessment = Boolean(
    scanId &&
    !hasServerAssessed &&
    (currentScan?.status === 'captured' || currentScan?.status === 'pending' || !currentScan?.status)
  );

  return (
    <View style={styles.container}>
      <Header
        title={displayTitle}
        subtitle={displaySubtitle}
        onBack={() => {
          if (onSaveFindings) onSaveFindings(findings);
          if (onBack) onBack();
        }}
      />

      <ScrollView style={styles.scroll} contentContainerStyle={styles.content}>
        {/* Package Header Card (Main Result) */}
        <Card padding="md" style={styles.headerCard}>
          <View style={styles.rowBetween}>
            <View style={{ flex: 1 }}>
              <Text style={styles.packageTitle}>{displaySubtitle}</Text>
              <Text style={styles.packageSub}>{displayBatch}</Text>
              <View style={styles.packBadge}>
                <Text style={styles.packBadgeText}>
                  Rule Pack {rulePackVersion} • {catalogRuleCount} Catalog Rules ({executableCheckCount} Algorithmic Checks)
                </Text>
              </View>
            </View>
            <VerdictBadge result={overallResult} />
          </View>

          <View style={[styles.rowAlign, { marginTop: spacing.md, flexWrap: 'wrap', gap: 6 }]}>
            <View style={[styles.statBadge, { backgroundColor: colors.pass?.fill || '#ECFDF5', borderColor: colors.pass?.border || '#A7F3D0' }]}>
              <Text style={[styles.statBadgeText, { color: colors.pass?.text || '#047857' }]}>✓ {passCount} Compliant</Text>
            </View>
            {failCount > 0 && (
              <View
                style={[
                  styles.statBadge,
                  { backgroundColor: colors.violation?.fill || '#FEF2F2', borderColor: colors.violation?.border || '#FECACA' },
                ]}
              >
                <Text
                  style={[
                    styles.statBadgeText,
                    { color: colors.violation?.text || '#B91C1C' },
                  ]}
                >
                  ⚠️ {failCount} Violations
                </Text>
              </View>
            )}
            {notAssessedCount > 0 && (
              <View
                style={[
                  styles.statBadge,
                  { backgroundColor: colors.notAssessed?.fill || '#F1F5F9', borderColor: colors.notAssessed?.border || '#CBD5E1' },
                ]}
              >
                <Text
                  style={[
                    styles.statBadgeText,
                    { color: colors.notAssessed?.text || '#475569' },
                  ]}
                >
                  ⚪ {notAssessedCount} Not Assessed
                </Text>
              </View>
            )}
            {notApplicableCount > 0 && (
              <View
                style={[
                  styles.statBadge,
                  { backgroundColor: colors.info?.fill || '#EFF6FF', borderColor: colors.info?.border || '#BFDBFE' },
                ]}
              >
                <Text
                  style={[
                    styles.statBadgeText,
                    { color: colors.info?.text || '#1D4ED8' },
                  ]}
                >
                  ○ {notApplicableCount} Not Applicable
                </Text>
              </View>
            )}
          </View>
        </Card>

        {/* Server Assessment Action: prominent button when awaiting; completed banner when done */}
        {isAwaitingAssessment && (
          <View style={{ marginBottom: spacing.sm }}>
            <Pressable
              onPress={handleTriggerReassess}
              disabled={reassessing}
              style={[styles.reassessBtn, reassessing && { opacity: 0.65 }]}
              accessibilityRole="button"
              accessibilityLabel="Run live assessment"
            >
              {reassessing ? (
                <View style={styles.rowAlign}>
                  <ActivityIndicator color={colors.white} size="small" style={{ marginRight: 8 }} />
                  <Text style={styles.reassessBtnText}>Evaluating {findings.length || 26} Statutory Checks with OCR & LLM…</Text>
                </View>
              ) : (
                <Text style={styles.reassessBtnText}>
                  ⚡ Run Server Assessment ({findings.length || 26} Statutory Checks) →
                </Text>
              )}
            </Pressable>
          </View>
        )}

        {hasServerAssessed && (
          <View style={styles.assessmentCompletedBar}>
            <View style={{ flex: 1 }}>
              <Text style={styles.assessmentCompletedTitle}>
                ✓ Authoritative Server Assessment Completed
              </Text>
              <Text style={styles.assessmentCompletedSub}>
                {findings.length} findings evaluated against Rule Pack {rulePackVersion} ({executableCheckCount} Algorithmic Checks)
              </Text>
            </View>
            <Pressable
              onPress={handleTriggerReassess}
              disabled={reassessing}
              style={styles.reassessSecondaryBtn}
              accessibilityRole="button"
              accessibilityLabel="Re-run assessment"
            >
              {reassessing ? (
                <ActivityIndicator color={colors.netraTeal} size="small" />
              ) : (
                <Text style={styles.reassessSecondaryBtnText}>🔄 Re-run</Text>
              )}
            </Pressable>
          </View>
        )}

        {/* Confirm & Return Action */}
        <View style={{ marginBottom: spacing.lg }}>
          <PrimaryButton
            title="✓ Confirm & Return to Package List"
            onPress={() => {
              if (onSaveFindings) onSaveFindings(findings);
              if (onBack) onBack();
            }}
          />
        </View>

        {/* Evidence Panel Gallery */}
        <Card padding="md" style={styles.galleryCard}>
          <View style={styles.rowBetween}>
            <View style={styles.rowAlign}>
              <Text style={styles.galleryTitle}>📸 Evidence Panel Gallery</Text>
            </View>
            <Text style={styles.gallerySub}>
              {evidenceImages.length} Panel(s) Captured
            </Text>
          </View>
          {evidenceImages.length === 0 ? (
            <Text style={{ fontSize: 12, color: colors.textMuted, marginTop: 8 }}>
              No evidence images attached to this package session.
            </Text>
          ) : (
            <ScrollView horizontal showsHorizontalScrollIndicator={false} style={{ marginTop: 10 }}>
              {evidenceImages.map((img, idx) => {
                const rawUrl = img.thumbnail_url || img.url;
                const highResUrl = img.url || rawUrl;

                return (
                  <Pressable
                    key={`evidence-${scanId || 'scan'}-${img.panel}-${img.id}-${img.sha256 || idx}`}
                    onPress={() => setSelectedImage({ ...img, displayUrl: highResUrl })}
                    style={styles.thumbWrapper}
                    accessibilityRole="imagebutton"
                    accessibilityLabel={`View ${img.panel} panel evidence`}
                  >
                    {rawUrl ? (
                      <Image
                        source={{
                          uri: rawUrl,
                          headers: (getAccessToken() && !rawUrl.startsWith('file://'))
                            ? { Authorization: `Bearer ${getAccessToken()}` }
                            : undefined,
                        }}
                        style={styles.panelThumb}
                      />
                    ) : (
                      <View style={[styles.panelThumb, { backgroundColor: colors.borderLight, justifyContent: 'center', alignItems: 'center' }]}>
                        <Text style={{ fontSize: 10, color: colors.textMuted }}>No photo</Text>
                      </View>
                    )}
                    <View style={styles.panelBadge}>
                      <Text style={styles.panelBadgeText}>{(img.panel || 'panel').toUpperCase()}</Text>
                    </View>
                  </Pressable>
                );
              })}
            </ScrollView>
          )}
        </Card>

        {/* Evidence Integrity Card (SHA-256) */}
        <Card padding="md" style={styles.integrityCard}>
          <View style={styles.rowBetween}>
            <View style={{ flex: 1, marginRight: spacing.sm }}>
              <View style={styles.rowAlign}>
                <Text style={styles.integrityTitle}>🔒 Evidence Integrity (SHA-256)</Text>
              </View>
              <Text style={styles.integritySub}>
                {evidenceIntegrity
                  ? `${evidenceIntegrity.images?.filter((i) => i.sha256_matches).length || 0} of ${evidenceIntegrity.images?.length || 0} panel photo(s) match cryptographic hash`
                  : 'Verify cryptographic evidence chain for this package'}
              </Text>
            </View>
            <Pressable
              onPress={handleVerifyEvidence}
              disabled={verifyingEvidence}
              style={styles.verifyBtn}
              accessibilityRole="button"
              accessibilityLabel="Verify evidence integrity"
            >
              {verifyingEvidence ? (
                <ActivityIndicator size="small" color={colors.netraTeal} />
              ) : (
                <Text style={styles.verifyBtnText}>Verify Evidence</Text>
              )}
            </Pressable>
          </View>
          {evidenceIntegrity?.images && evidenceIntegrity.images.length > 0 && (
            <View style={styles.hashList}>
              {evidenceIntegrity.images.map((img, i) => {
                const panelName = img.panel || img.panel_type || `PANEL ${i + 1}`;
                const shaRecorded = img.sha256_recorded || img.sha256_hash || img.sha256;
                return (
                  <View key={`hash-${scanId || 'curr'}-${img.image_id || i}`} style={styles.hashItem}>
                    <Text style={styles.hashLabel}>{panelName.toUpperCase()}:</Text>
                    <Text style={styles.hashCode} numberOfLines={1} ellipsizeMode="middle">
                      {shaRecorded ? `${shaRecorded.slice(0, 16)}…` : 'Pending'}
                    </Text>
                    <Text style={{ fontSize: 11, color: img.sha256_matches ? colors.pass.text : colors.violation.text, fontWeight: '700' }}>
                      {img.sha256_matches ? '✓ Intact' : '⚠️ Mismatch'}
                    </Text>
                  </View>
                );
              })}
            </View>
          )}
        </Card>

        {/* List of Statutory Checks */}
        <Text style={styles.sectionTitle}>STATUTORY FINDINGS ({findings.length})</Text>
        {findings.map((f) => {
          return (
            <Card key={`finding-${f.check_id}-${f.id}`} padding="md" style={styles.findingCard}>
              <View style={styles.rowBetween}>
                <View style={{ flex: 1, marginRight: 8 }}>
                  <View style={styles.rowAlign}>
                    <Text style={styles.checkCode}>{f.check_id}</Text>
                    <Text style={styles.checkName}>{f.title}</Text>
                  </View>
                  <Text style={styles.citationText}>{f.citation}</Text>
                </View>
                <VerdictBadge checkVerdict={f.effective_verdict} />
              </View>

              {/* Observed & Required Details */}
              <View style={styles.detailsBox}>
                <View style={{ marginBottom: 4 }}>
                  <Text style={styles.detailLabel}>Observed on package:</Text>
                  <Text style={styles.detailValue}>{f.observed}</Text>
                </View>
                <View>
                  <Text style={styles.detailLabel}>Statutory requirement:</Text>
                  <Text style={styles.detailRequired}>{f.required}</Text>
                </View>
                {f.reason && (
                  <View style={{ marginTop: 4 }}>
                    <Text style={[styles.detailLabel, { color: colors.violation.text }]}>Non-compliance ground / Explanation:</Text>
                    <Text style={{ fontSize: 11, color: colors.violation.text }}>{f.reason}</Text>
                  </View>
                )}
                {f.remediation && (
                  <View style={{ marginTop: 4 }}>
                    <Text style={[styles.detailLabel, { color: colors.textSecondary }]}>Statutory remediation:</Text>
                    <Text style={{ fontSize: 11, color: colors.text }}>{f.remediation}</Text>
                  </View>
                )}
                {f.evidence_references && f.evidence_references.length > 0 && (
                  <View style={{ marginTop: 4, flexDirection: 'row', alignItems: 'center' }}>
                    <Text style={styles.detailLabel}>Evidence Ref: </Text>
                    <Text style={{ fontSize: 10, color: colors.textMuted }}>
                      {Array.isArray(f.evidence_references) ? f.evidence_references.join(', ') : String(f.evidence_references)}
                    </Text>
                  </View>
                )}
                {(f.human_verdict || f.override_reason) && (
                  <View style={styles.overrideNotice}>
                    <Text style={styles.overrideNoticeText}>
                      📝 Inspector Remark ({f.human_verdict ? f.human_verdict.replace('_', ' ').toUpperCase() : 'NOTE'}): {f.override_reason}
                    </Text>
                  </View>
                )}
              </View>

              {/* Remark / Override Button */}
              <View style={styles.cardActions}>
                <Pressable
                  onPress={() => handleOpenOverride(f)}
                  style={styles.overrideBtn}
                  hitSlop={6}
                >
                  <Text style={styles.overrideBtnText}>📝 Add Remark / Review Verdict →</Text>
                </Pressable>
              </View>
            </Card>
          );
        })}

        {/* Bottom spacing */}
        <View style={{ height: spacing.xl }} />
      </ScrollView>

      {/* Inspector Review & Remarks Modal */}
      <Modal visible={!!selectedFinding} animationType="slide" transparent onRequestClose={() => setSelectedFinding(null)}>
        <View style={styles.modalBackdrop}>
          <View style={[styles.modalSheet, { paddingBottom: safeBottom + spacing.lg }]}>
            <Text style={styles.modalTitle}>Inspector Review & Remarks</Text>
            <Text style={styles.modalSub}>
              {selectedFinding?.check_id}: {selectedFinding?.title}
            </Text>

            <Text style={[styles.sectionTitle, { marginTop: spacing.md }]}>RULE RESULT</Text>
            <View style={styles.verdictGrid}>
              <Pressable
                onPress={() => setOverrideVerdict('pass')}
                style={[
                  styles.verdictBtn,
                  (overrideVerdict === 'pass' || overrideVerdict === 'compliant') && {
                    backgroundColor: colors.pass?.fill || '#ECFDF5',
                    borderColor: colors.pass?.border || '#A7F3D0',
                  },
                ]}
              >
                <Text style={{ color: colors.pass?.text || '#047857', fontWeight: '700', fontSize: 12 }}>✓ Compliant</Text>
              </Pressable>
              <Pressable
                onPress={() => setOverrideVerdict('fail')}
                style={[
                  styles.verdictBtn,
                  (overrideVerdict === 'fail' || overrideVerdict === 'violation') && {
                    backgroundColor: colors.violation?.fill || '#FEF2F2',
                    borderColor: colors.violation?.border || '#FECACA',
                  },
                ]}
              >
                <Text style={{ color: colors.violation?.text || '#B91C1C', fontWeight: '700', fontSize: 12 }}>⚠️ Violation</Text>
              </Pressable>
              <Pressable
                onPress={() => setOverrideVerdict('not_assessed')}
                style={[
                  styles.verdictBtn,
                  overrideVerdict === 'not_assessed' && {
                    backgroundColor: colors.notAssessed?.fill || '#F1F5F9',
                    borderColor: colors.notAssessed?.border || '#CBD5E1',
                  },
                ]}
              >
                <Text style={{ color: colors.notAssessed?.text || '#475569', fontWeight: '700', fontSize: 12 }}>⚪ Not Assessed</Text>
              </Pressable>
              <Pressable
                onPress={() => setOverrideVerdict('out_of_scope')}
                style={[
                  styles.verdictBtn,
                  (overrideVerdict === 'out_of_scope' || overrideVerdict === 'not_applicable') && {
                    backgroundColor: colors.info?.fill || '#EFF6FF',
                    borderColor: colors.info?.border || '#BFDBFE',
                  },
                ]}
              >
                <Text style={{ color: colors.info?.text || '#1D4ED8', fontWeight: '700', fontSize: 12 }}>○ Not Applicable</Text>
              </Pressable>
            </View>

            <Text style={[styles.sectionTitle, { marginTop: spacing.md }]}>
              INSPECTOR REMARK / CLARIFICATION
            </Text>
            <TextInput
              placeholder="State inspector remarks, observations, or legal justification for this finding…"
              placeholderTextColor={colors.placeholder}
              value={overrideReason}
              onChangeText={setOverrideReason}
              multiline
              numberOfLines={3}
              style={styles.reasonInput}
            />

            <View style={{ flexDirection: 'row', gap: spacing.sm, marginTop: spacing.lg }}>
              <Pressable
                onPress={() => setSelectedFinding(null)}
                style={styles.modalCancelBtn}
              >
                <Text style={{ color: colors.textSecondary, fontWeight: '600' }}>Cancel</Text>
              </Pressable>
              <View style={{ flex: 1 }}>
                <PrimaryButton
                  title={submittingOverride ? 'Saving…' : 'Save Remark & Verdict'}
                  onPress={handleSaveOverride}
                  disabled={submittingOverride}
                />
              </View>
            </View>
          </View>
        </View>
      </Modal>

      {/* Fullscreen Evidence Image Viewer Modal */}
      <Modal visible={!!selectedImage} animationType="fade" transparent onRequestClose={() => setSelectedImage(null)}>
        <View style={styles.imageModalBackdrop}>
          <View style={styles.imageModalContainer}>
            <View style={styles.imageModalHeader}>
              <View style={{ flex: 1, marginRight: 8 }}>
                <Text style={styles.imageModalTitle}>
                  {(selectedImage?.panel || 'Evidence').toUpperCase()} PANEL
                </Text>
                {selectedImage?.sha256 && (
                  <Text style={styles.imageModalSub} numberOfLines={1} ellipsizeMode="middle">
                    SHA-256: {selectedImage.sha256}
                  </Text>
                )}
              </View>
              <Pressable
                onPress={() => setSelectedImage(null)}
                style={styles.closeBtn}
                accessibilityRole="button"
                accessibilityLabel="Close evidence image viewer"
              >
                <Text style={styles.closeBtnText}>✕</Text>
              </Pressable>
            </View>
            <View style={styles.imageModalBody}>
              {selectedImage?.displayUrl ? (
                <Image
                  source={{
                    uri: selectedImage.displayUrl,
                    headers: (getAccessToken() && !selectedImage.displayUrl.startsWith('file://'))
                      ? { Authorization: `Bearer ${getAccessToken()}` }
                      : undefined,
                  }}
                  style={styles.fullscreenImage}
                  resizeMode="contain"
                />
              ) : (
                <Text style={{ color: colors.white }}>Image unavailable</Text>
              )}
            </View>
          </View>
        </View>
      </Modal>
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
  headerCard: {
    marginBottom: spacing.md,
    backgroundColor: colors.surface,
  },
  packageTitle: {
    fontSize: 15,
    fontWeight: '700',
    color: colors.niyamBlue,
  },
  packageSub: {
    fontSize: 11,
    color: colors.textMuted,
    marginTop: 2,
  },
  packBadge: {
    alignSelf: 'flex-start',
    backgroundColor: colors.surfaceHighlight || '#F8FAFC',
    borderColor: colors.borderLight || '#E2E8F0',
    borderWidth: 1,
    paddingHorizontal: 6,
    paddingVertical: 2,
    borderRadius: radius.xs || 4,
    marginTop: 4,
  },
  packBadgeText: {
    fontSize: 10,
    fontWeight: '600',
    color: colors.textMuted,
  },
  assessmentCompletedBar: {
    backgroundColor: colors.surface,
    borderColor: colors.pass?.border || '#A7F3D0',
    borderWidth: 1,
    borderRadius: radius.md,
    padding: spacing.sm,
    marginBottom: spacing.sm,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
  },
  assessmentCompletedTitle: {
    fontSize: 12,
    fontWeight: '700',
    color: colors.pass?.text || '#047857',
  },
  assessmentCompletedSub: {
    fontSize: 10,
    color: colors.textMuted,
    marginTop: 1,
  },
  reassessSecondaryBtn: {
    paddingHorizontal: 8,
    paddingVertical: 4,
    borderRadius: radius.sm,
    borderWidth: 1,
    borderColor: colors.borderLight,
    backgroundColor: colors.background,
  },
  reassessSecondaryBtnText: {
    fontSize: 11,
    fontWeight: '700',
    color: colors.netraTeal,
  },
  statBadge: {
    paddingHorizontal: 10,
    paddingVertical: 3,
    borderRadius: radius.sm,
    borderWidth: 1,
    marginRight: spacing.sm,
  },
  statBadgeText: {
    fontSize: 11,
    fontWeight: '700',
  },
  sectionTitle: {
    fontSize: 11,
    fontWeight: '700',
    color: colors.textMuted,
    letterSpacing: 0.8,
    marginBottom: spacing.xs,
    textTransform: 'uppercase',
  },
  findingCard: {
    marginBottom: spacing.sm,
  },
  checkCode: {
    fontSize: 11,
    fontWeight: '800',
    color: colors.netraTeal,
    backgroundColor: '#F0FDFA',
    paddingHorizontal: 6,
    paddingVertical: 1,
    borderRadius: radius.sm,
    marginRight: spacing.xs,
  },
  checkName: {
    fontSize: 13,
    fontWeight: '700',
    color: colors.text,
    flex: 1,
  },
  citationText: {
    fontSize: 10,
    color: colors.textMuted,
    marginTop: 2,
  },
  detailsBox: {
    marginTop: spacing.sm,
    paddingTop: spacing.sm,
    borderTopWidth: 1,
    borderTopColor: colors.borderLight,
  },
  detailLabel: {
    fontSize: 10,
    color: colors.textMuted,
    fontWeight: '600',
  },
  detailValue: {
    fontSize: 12,
    color: colors.text,
    fontWeight: '600',
  },
  detailRequired: {
    fontSize: 11,
    color: colors.textSecondary,
  },
  overrideNotice: {
    marginTop: 4,
    padding: 6,
    backgroundColor: colors.info.fill,
    borderRadius: radius.sm,
  },
  overrideNoticeText: {
    fontSize: 10,
    color: colors.info.text,
    fontWeight: '600',
  },
  cardActions: {
    flexDirection: 'row',
    justifyContent: 'flex-end',
    marginTop: spacing.xs,
  },
  overrideBtn: {
    paddingHorizontal: 8,
    paddingVertical: 4,
  },
  overrideBtnText: {
    fontSize: 11,
    fontWeight: '600',
    color: colors.netraTeal,
  },
  modalBackdrop: {
    flex: 1,
    backgroundColor: 'rgba(0,0,0,0.5)',
    justifyContent: 'flex-end',
  },
  modalSheet: {
    backgroundColor: colors.surface,
    borderTopLeftRadius: radius.lg,
    borderTopRightRadius: radius.lg,
    padding: spacing.lg,
    paddingBottom: spacing.xxl,
  },
  modalTitle: {
    fontSize: 16,
    fontWeight: '700',
    color: colors.niyamBlue,
  },
  modalSub: {
    fontSize: 12,
    color: colors.textMuted,
    marginTop: 2,
  },
  verdictGrid: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: spacing.xs,
    marginTop: spacing.xs,
  },
  verdictBtn: {
    flexBasis: '48%',
    flexGrow: 1,
    paddingVertical: 10,
    alignItems: 'center',
    borderRadius: radius.md,
    borderWidth: 1,
    borderColor: colors.border,
    backgroundColor: colors.white,
  },
  integrityCard: {
    marginBottom: spacing.md,
    backgroundColor: '#F8FAFC',
    borderColor: colors.borderLight,
    borderWidth: 1,
  },
  integrityTitle: {
    fontSize: 13,
    fontWeight: '700',
    color: colors.niyamBlue,
  },
  integritySub: {
    fontSize: 11,
    color: colors.textMuted,
    marginTop: 2,
  },
  verifyBtn: {
    paddingHorizontal: 12,
    paddingVertical: 6,
    borderRadius: radius.sm,
    backgroundColor: '#CCFBF1',
    borderWidth: 1,
    borderColor: colors.netraTeal,
  },
  verifyBtnText: {
    fontSize: 11,
    fontWeight: '700',
    color: colors.netraTeal,
  },
  hashList: {
    marginTop: spacing.sm,
    paddingTop: spacing.xs,
    borderTopWidth: 1,
    borderTopColor: colors.borderLight,
    gap: 4,
  },
  hashItem: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingVertical: 2,
  },
  hashLabel: {
    fontSize: 10,
    fontWeight: '700',
    color: colors.textMuted,
    width: 75,
  },
  hashCode: {
    fontSize: 10,
    fontFamily: 'monospace',
    color: colors.textSecondary,
    flex: 1,
    marginHorizontal: 4,
  },
  reasonInput: {
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: radius.md,
    padding: spacing.sm,
    fontSize: 12,
    color: colors.text,
    backgroundColor: colors.white,
    height: 70,
    textAlignVertical: 'top',
    marginTop: spacing.xs,
  },
  modalCancelBtn: {
    paddingHorizontal: spacing.lg,
    paddingVertical: spacing.md,
    alignItems: 'center',
    justifyContent: 'center',
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
  reassessBtn: {
    backgroundColor: colors.niyamBlue,
    borderRadius: radius.md,
    paddingVertical: 12,
    paddingHorizontal: spacing.lg,
    alignItems: 'center',
    justifyContent: 'center',
    ...shadows.sm,
  },
  reassessBtnText: {
    fontSize: 13,
    fontWeight: '700',
    color: colors.white,
    letterSpacing: 0.3,
  },
  galleryCard: {
    marginBottom: spacing.md,
    backgroundColor: '#F8FAFC',
    borderColor: colors.borderLight,
    borderWidth: 1,
  },
  galleryTitle: {
    fontSize: 13,
    fontWeight: '700',
    color: colors.niyamBlue,
  },
  gallerySub: {
    fontSize: 11,
    color: colors.textMuted,
  },
  thumbWrapper: {
    marginRight: 10,
    alignItems: 'center',
    position: 'relative',
  },
  panelThumb: {
    width: 72,
    height: 72,
    borderRadius: radius.sm,
    backgroundColor: colors.borderLight,
    borderWidth: 1,
    borderColor: colors.border,
  },
  panelBadge: {
    position: 'absolute',
    bottom: 2,
    left: 2,
    right: 2,
    backgroundColor: 'rgba(15, 23, 42, 0.75)',
    borderRadius: 3,
    paddingVertical: 1,
    alignItems: 'center',
  },
  panelBadgeText: {
    fontSize: 8,
    fontWeight: '800',
    color: colors.white,
    letterSpacing: 0.5,
  },
  imageModalBackdrop: {
    flex: 1,
    backgroundColor: 'rgba(0, 0, 0, 0.92)',
    justifyContent: 'center',
    alignItems: 'center',
  },
  imageModalContainer: {
    width: '94%',
    height: '82%',
    backgroundColor: '#0F172A',
    borderRadius: radius.md,
    overflow: 'hidden',
  },
  imageModalHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    padding: spacing.md,
    borderBottomWidth: 1,
    borderBottomColor: '#334155',
  },
  imageModalTitle: {
    fontSize: 14,
    fontWeight: '700',
    color: colors.white,
  },
  imageModalSub: {
    fontSize: 10,
    color: '#94A3B8',
    fontFamily: 'monospace',
    marginTop: 2,
  },
  closeBtn: {
    width: 32,
    height: 32,
    borderRadius: 16,
    backgroundColor: '#334155',
    justifyContent: 'center',
    alignItems: 'center',
  },
  closeBtnText: {
    fontSize: 14,
    fontWeight: '700',
    color: colors.white,
  },
  imageModalBody: {
    flex: 1,
    justifyContent: 'center',
    alignItems: 'center',
    padding: spacing.sm,
  },
  fullscreenImage: {
    width: '100%',
    height: '100%',
  },
});
