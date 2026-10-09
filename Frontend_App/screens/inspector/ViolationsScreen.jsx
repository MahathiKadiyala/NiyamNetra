import React, { useCallback, useEffect, useState } from 'react';
import { View, Text, ScrollView, Pressable, RefreshControl, ActivityIndicator, Image, Alert } from 'react-native';
import { colors, spacing, typography, radius } from '../../theme';
import Header from '../../components/Header';
import Card from '../../components/Card';
import VerdictBadge from '../../components/VerdictBadge';
import EmptyState from '../../components/EmptyState';
import FindingRow from '../../components/FindingRow';
import SegmentControl from '../../components/SegmentControl';
import { fetchInspections } from '../../api/admin';
import { API_BASE_URL } from '../../api/config';
import { getAccessToken } from '../../api/client';

// §5.4 Violations list — live data from GET /inspections, with detail view.
function todayLocal() {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

function itemDate(it) {
  const raw = it?.date || it?.inspection_date || it?.local_created_at || it?.created_at || '';
  return String(raw).slice(0, 10);
}

function isViolation(it) {
  // Primary check: backend rollup fields (result/overall_result/verdict)
  const r = String(it?.result || it?.overall_result || it?.verdict || it?.status || '').toLowerCase();
  if (['violation', 'fail', 'non_compliant', 'non-compliant'].includes(r)) return true;
  // Secondary check: result_counts.violation > 0 (any scan in this inspection violated)
  if (Number(it?.result_counts?.violation || 0) > 0) return true;
  // Tertiary: explicit violation_count / violated field
  if (Number(it?.violation_count || 0) > 0) return true;
  if (Number(it?.violated || 0) > 0) return true;
  return false;
}

function findingsOf(it) {
  if (Array.isArray(it?.findings) && it.findings.length > 0) return it.findings;
  if (Array.isArray(it?.scans)) {
    const fromScans = [];
    for (const s of it.scans) {
      if (Array.isArray(s?.findings)) {
        fromScans.push(...s.findings);
      }
    }
    if (fromScans.length > 0) return fromScans;
  }
  if (Array.isArray(it?.checks)) {
    return it.checks
      .filter((c) => String(c?.verdict || c?.result || '').toLowerCase() !== 'pass')
      .map((c) => ({
        code: c.code || c.check_id || '',
        name: c.title || c.name || '',
        checkVerdict: c.verdict || c.result || 'fail',
        reason: c.reason || c.note || '',
        value: c.value != null ? String(c.value) : '',
      }));
  }
  return [];
}

export default function ViolationsScreen({ navigation }) {
  const [view, setView] = useState('list'); // list | detail
  const [selectedInspection, setSelectedInspection] = useState(null);
  const [violations, setViolations] = useState([]);
  const [filter, setFilter] = useState('all');
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState(null); // null | 'forbidden' | 'network'

  const load = useCallback(async (isRefresh = false) => {
    if (isRefresh) setRefreshing(true);
    else setLoading(true);
    setError(null);
    try {
      // Backend GET /inspections supports store_id/status/date range/q only —
      // there is no result= filter, so fetch and filter client-side.
      const data = await fetchInspections();
      const list = Array.isArray(data) ? data : data?.items || data?.results || [];
      setViolations(list.filter(isViolation));
    } catch (e) {
      const status = e?.status ?? e?.response?.status;
      setError(status === 403 || e?.code === 'FORBIDDEN' ? 'forbidden' : 'network');
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  // Date-based filter counts
  const today = todayLocal();
  const weekAgoD = new Date();
  weekAgoD.setDate(weekAgoD.getDate() - 6);
  const weekAgo = `${weekAgoD.getFullYear()}-${String(weekAgoD.getMonth() + 1).padStart(2, '0')}-${String(weekAgoD.getDate()).padStart(2, '0')}`;

  const filtered = violations.filter((v) => {
    const d = itemDate(v);
    if (filter === 'today') return d === today;
    if (filter === 'week') return d >= weekAgo;
    return true;
  });
  const todayCount = violations.filter((v) => itemDate(v) === today).length;
  const weekCount = violations.filter((v) => itemDate(v) >= weekAgo).length;

  if (view === 'detail' && selectedInspection) {
    const v = selectedInspection;
    const findings = findingsOf(v);
    // Use result_counts.violation for violated count if available (more accurate)
    const violated = v.result_counts?.violation ?? v.violated ?? v.violation_count ?? findings.length;
    const checks = v.checks ?? v.checks_total ?? v.total_checks ?? 0;
    return (
      <View style={{ flex: 1, backgroundColor: colors.background }}>
        <Header title="Violation Details" subtitle={v.store_name || v.store || ''} onBack={() => setView('list')} />
        <ScrollView contentContainerStyle={{ padding: spacing.lg, paddingBottom: 100 }}>
          {/* Violation summary */}
          <Card title={v.commodity || v.commodity_generic || 'Inspection'} subtitle={`${violated} of ${checks} checks violated`} padding="lg" style={{ marginBottom: spacing.md }}>
            <View style={{ flexDirection: 'row', justifyContent: 'space-between', marginBottom: spacing.sm }}>
              <View>
                <Text style={typography.label}>Store</Text>
                <Text style={typography.body}>{v.store_name || v.store || 'Unknown store'}</Text>
              </View>
              <VerdictBadge result="violation" />
            </View>
            <View style={{ flexDirection: 'row', justifyContent: 'space-between' }}>
              <View>
                <Text style={typography.label}>Date</Text>
                <Text style={typography.body}>{itemDate(v)}</Text>
              </View>
              <View>
                <Text style={typography.label}>Checks run</Text>
                <Text style={typography.body}>{checks ? `${checks} assessed` : 'See findings below'}</Text>
              </View>
            </View>
          </Card>

          {/* Findings */}
          <Text style={{ ...typography.h4, marginBottom: spacing.sm }}>Findings</Text>
          {findings.length === 0 ? (
            <View style={{ backgroundColor: colors.borderLight, borderRadius: radius.md, padding: spacing.md, marginBottom: spacing.md }}>
              <Text style={{ fontSize: 13, color: colors.textSecondary, fontWeight: '600', marginBottom: 4 }}>
                No finding details in this record
              </Text>
              <Text style={{ fontSize: 12, color: colors.textMuted, lineHeight: 18 }}>
                This inspection was marked as a violation based on the scan rollup ({violated} violated package{violated === 1 ? '' : 's'}).
                Detailed findings are recorded per-package during assessment. View the full inspection record for per-package breakdown.
              </Text>
            </View>
          ) : (
            findings.map((f, i) => <FindingRow key={i} finding={f} />)
          )}

          {/* Scan-level breakdown if findings are empty */}
          {findings.length === 0 && Array.isArray(v.scans) && v.scans.filter(s => s.overall_result === 'violation').length > 0 && (
            <View style={{ marginBottom: spacing.md }}>
              <Text style={{ ...typography.label, marginBottom: spacing.xs }}>Violation Packages</Text>
              {v.scans.filter(s => s.overall_result === 'violation').map((s, i) => (
                <View key={i} style={{ backgroundColor: colors.violation.fill, borderRadius: radius.sm, padding: spacing.sm, marginBottom: spacing.xs }}>
                  <Text style={{ color: colors.violation.text, fontSize: 12, fontWeight: '600' }}>
                    ✗ {s.commodity_generic || s.brand_name || `Package ${i + 1}`}
                  </Text>
                  {s.checks_assessed > 0 && (
                    <Text style={{ color: colors.violation.text, fontSize: 11, marginTop: 2 }}>
                      {s.checks_assessed} checks assessed
                    </Text>
                  )}
                </View>
              ))}
            </View>
          )}

          {/* Evidence link */}
          <Card title="Evidence" subtitle="Tap to view captured images" padding="md" style={{ marginTop: spacing.md }}>
            {(v.evidence_uris || v.photos || v.images || []).length === 0 ? (
              <Text style={{ fontSize: 12, color: colors.textMuted }}>No evidence images attached.</Text>
            ) : (
              <View style={{ flexDirection: 'row', flexWrap: 'wrap' }}>
                {(v.evidence_uris || v.photos || v.images || []).map((u, i) => {
                  const rawUri = typeof u === 'string' ? u : u?.thumbnail_url || u?.url || u?.uri || u?.file_path;
                  if (!rawUri) return null;
                  const uri = (rawUri.startsWith('http://') || rawUri.startsWith('https://') || rawUri.startsWith('file://'))
                    ? rawUri
                    : `${API_BASE_URL.replace(/\/+$/, '')}/${rawUri.replace(/^\/+/, '')}`;
                  return (
                    <Pressable
                      key={i}
                      onPress={() => Alert.alert('Evidence', `Image ${i + 1} — file retained securely on server.`)}
                      accessibilityRole="imagebutton"
                      accessibilityLabel={`Evidence image ${i + 1}`}
                    >
                      <Image
                        source={{
                          uri,
                          headers: (getAccessToken() && !uri.startsWith('file://'))
                            ? { Authorization: `Bearer ${getAccessToken()}` }
                            : undefined,
                        }}
                        style={{ width: 60, height: 60, borderRadius: radius.sm, backgroundColor: colors.border, margin: spacing.xs, borderWidth: 2, borderColor: colors.saffron }}
                      />
                    </Pressable>
                  );
                })}
              </View>
            )}
          </Card>
        </ScrollView>
      </View>
    );
  }

  return (
    <View style={{ flex: 1, backgroundColor: colors.background }}>
      <Header title="Violations" subtitle={`${violations.length} flagged`} />
      <ScrollView
        contentContainerStyle={{ padding: spacing.lg, paddingBottom: 100 }}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={() => load(true)} />}
      >
        {/* Filter — mirrors PassScreen */}
        <SegmentControl
          selected={filter}
          onSelect={setFilter}
          options={[
            { key: 'all', label: 'All', count: violations.length },
            { key: 'today', label: 'Today', count: todayCount },
            { key: 'week', label: 'This week', count: weekCount },
          ]}
          scrollable
        />

        {/* List */}
        <View style={{ marginTop: spacing.md }}>
          {loading ? (
            <View style={{ paddingVertical: spacing.xxl, alignItems: 'center' }}>
              <ActivityIndicator color={colors.netraTeal} />
            </View>
          ) : error && violations.length === 0 ? (
            <EmptyState
              icon="⚠️"
              title={error === 'forbidden' ? 'Not permitted' : 'Could not load violations'}
              subtitle={error === 'forbidden'
                ? 'Your account cannot view violations. Contact your administrator.'
                : 'Check your connection and pull to refresh.'}
              variant="neutral"
            />
          ) : filtered.length === 0 ? (
            <EmptyState
              icon="✓"
              title={filter === 'today' ? 'No violations today' : filter === 'week' ? 'No violations this week' : 'No violations recorded'}
              subtitle="Finding no violations is a fact about the shelf, not a performance."
              variant="neutral"
            />
          ) : (
            filtered.map((v) => {
              // Prefer result_counts.violation for the displayed count (most accurate)
              const violated = v.result_counts?.violation ?? v.violated ?? v.violation_count ?? findingsOf(v).length;
              return (
                <Pressable key={v.id} onPress={() => { setSelectedInspection(v); setView('detail'); }}>
                  <Card padding="md" style={{ marginBottom: spacing.sm }}>
                    <View style={{ flexDirection: 'row', justifyContent: 'space-between', alignItems: 'flex-start' }}>
                      <View style={{ flex: 1 }}>
                        <Text style={{ fontSize: 15, fontWeight: '600', color: colors.text, marginBottom: 4 }}>
                          {v.store_name || v.store || 'Unknown store'}
                        </Text>
                        <Text style={{ fontSize: 13, color: colors.textSecondary, marginBottom: 4 }}>{v.commodity || v.commodity_generic || ''}</Text>
                        <Text style={{ fontSize: 12, color: colors.textMuted }}>{itemDate(v)}</Text>
                        <View style={{ flexDirection: 'row', marginTop: spacing.sm }}>
                          <View style={{ backgroundColor: colors.violation.fill, borderRadius: radius.sm, paddingHorizontal: 8, paddingVertical: 2 }}>
                            <Text style={{ color: colors.violation.text, fontSize: 11, fontWeight: '600' }}>✗ {violated} violation{violated === 1 ? '' : 's'}</Text>
                          </View>
                        </View>
                      </View>
                      <VerdictBadge result="violation" />
                    </View>
                  </Card>
                </Pressable>
              );
            })
          )}
        </View>
      </ScrollView>
    </View>
  );
}
