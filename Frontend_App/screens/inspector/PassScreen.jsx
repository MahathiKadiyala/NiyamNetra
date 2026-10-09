import React, { useCallback, useEffect, useState } from 'react';
import { View, Text, ScrollView, Pressable, RefreshControl, ActivityIndicator, Modal, Image } from 'react-native';
import { colors, spacing, typography, radius } from '../../theme';
import Header from '../../components/Header';
import Card from '../../components/Card';
import VerdictBadge from '../../components/VerdictBadge';
import EmptyState from '../../components/EmptyState';
import SegmentControl from '../../components/SegmentControl';
import { fetchInspections } from '../../api/admin';
import { API_BASE_URL } from '../../api/config';
import { getAccessToken } from '../../api/client';

// §5.3 Pass/Compliant inspections list — live data from GET /inspections.
function todayLocal() {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

function itemDate(it) {
  const raw = it?.date || it?.inspection_date || it?.local_created_at || it?.created_at || '';
  return String(raw).slice(0, 10);
}

function isSuccess(it) {
  const r = String(it?.result || it?.overall_result || it?.verdict || it?.status || '').toLowerCase();
  if (['success', 'pass', 'compliant'].includes(r)) return true;
  const violations = it?.result_counts?.violation ?? it?.violation_count ?? 0;
  const compliant = it?.result_counts?.compliant ?? it?.pass_count ?? 0;
  if (violations === 0 && compliant > 0) return true;
  return false;
}

export default function PassScreen({ navigation }) {
  const [filter, setFilter] = useState('all');
  const [inspections, setInspections] = useState([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState(null); // null | 'forbidden' | 'network'
  const [detail, setDetail] = useState(null); // selected inspection or null

  const load = useCallback(async (isRefresh = false) => {
    if (isRefresh) setRefreshing(true);
    else setLoading(true);
    setError(null);
    try {
      // Backend GET /inspections has no result= filter (store_id/status/date
      // range/q only) — fetch and filter client-side so the filter is real.
      const data = await fetchInspections();
      const list = Array.isArray(data) ? data : data?.items || data?.results || [];
      setInspections(list.filter(isSuccess));
    } catch (e) {
      const status = e?.status ?? e?.response?.status;
      setError(status === 403 || e?.code === 'FORBIDDEN' ? 'forbidden' : 'network');
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const today = todayLocal();
  const weekAgoD = new Date();
  weekAgoD.setDate(weekAgoD.getDate() - 6);
  const weekAgo = `${weekAgoD.getFullYear()}-${String(weekAgoD.getMonth() + 1).padStart(2, '0')}-${String(weekAgoD.getDate()).padStart(2, '0')}`;
  const filtered = inspections.filter((i) => {
    const d = itemDate(i);
    if (filter === 'today') return d === today;
    if (filter === 'week') return d >= weekAgo;
    return true;
  });
  const todayCount = inspections.filter((i) => itemDate(i) === today).length;
  const weekCount = inspections.filter((i) => itemDate(i) >= weekAgo).length;

  return (
    <View style={{ flex: 1, backgroundColor: colors.background }}>
      <Header title="Successful Inspections" subtitle={`${inspections.length} total`} />
      <ScrollView
        contentContainerStyle={{ padding: spacing.lg, paddingBottom: 100 }}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={() => load(true)} />}
      >
        {/* Filter */}
        <SegmentControl
          selected={filter}
          onSelect={setFilter}
          options={[
            { key: 'all', label: 'All', count: inspections.length },
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
          ) : error && inspections.length === 0 ? (
            <EmptyState
              icon="⚠️"
              title={error === 'forbidden' ? 'Not permitted' : 'Could not load inspections'}
              subtitle={error === 'forbidden'
                ? 'Your account cannot view these inspections. Contact your administrator.'
                : 'Check your connection and pull to refresh.'}
              variant="neutral"
            />
          ) : filtered.length === 0 ? (
            <EmptyState
              icon="✓"
              title="No successful inspections"
              subtitle="Inspections that pass all checks will appear here."
              variant="neutral"
            />
          ) : (
            filtered.map((item) => {
              const passed = item.passed ?? item.pass_count ?? item.checks_passed ?? 0;
              const notAssessed = item.not_assessed ?? item.not_assessed_count ?? 0;
              const checks = item.checks ?? item.checks_total ?? item.checks_assessed ?? item.total_checks ?? 0;
              return (
                <Pressable
                  key={item.id}
                  onPress={() => setDetail(item)}
                  accessibilityRole="button"
                  accessibilityLabel={`Inspection at ${item.store_name || item.store || 'unknown store'}, ${itemDate(item)}. View details.`}
                >
                  <Card padding="md" style={{ marginBottom: spacing.sm }}>
                    <View style={{ flexDirection: 'row', justifyContent: 'space-between', alignItems: 'flex-start' }}>
                      <View style={{ flex: 1 }}>
                        <Text style={{ fontSize: 15, fontWeight: '600', color: colors.text, marginBottom: 4 }}>
                          {item.store_name || item.store || 'Unknown store'}
                        </Text>
                        <Text style={{ fontSize: 12, color: colors.textMuted }}>{itemDate(item)} • {checks ? `${checks} assessed` : 'Statutory checks assessed'}</Text>
                        <View style={{ flexDirection: 'row', marginTop: spacing.sm }}>
                          <View style={{ backgroundColor: colors.pass.fill, borderRadius: radius.sm, paddingHorizontal: 8, paddingVertical: 2, marginRight: spacing.xs }}>
                            <Text style={{ color: colors.pass.text, fontSize: 11, fontWeight: '600' }}>✓ {passed} compliant</Text>
                          </View>
                          {notAssessed > 0 && (
                            <View style={{ backgroundColor: colors.notAssessed.fill, borderRadius: radius.sm, paddingHorizontal: 8, paddingVertical: 2 }}>
                              <Text style={{ color: colors.notAssessed.text, fontSize: 11, fontWeight: '600' }}>⏳ {notAssessed} not assessed</Text>
                            </View>
                          )}
                        </View>
                      </View>
                      <VerdictBadge result="compliant" />
                    </View>
                  </Card>
                </Pressable>
              );
            })
          )}
        </View>
      </ScrollView>

      {/* Detail modal — server data with a carousel note (no fake gallery) */}
      <Modal visible={!!detail} animationType="slide" transparent onRequestClose={() => setDetail(null)}>
        <View style={{ flex: 1, backgroundColor: 'rgba(0,0,0,0.4)', justifyContent: 'flex-end' }}>
          <View style={{ backgroundColor: colors.background, borderTopLeftRadius: radius.lg, borderTopRightRadius: radius.lg, maxHeight: '85%', padding: spacing.lg }}>
            <View style={{ flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', marginBottom: spacing.md }}>
              <Text style={{ fontSize: 17, fontWeight: '700', color: colors.text }}>
                {detail?.store_name || detail?.store || 'Inspection'}
              </Text>
              <Pressable onPress={() => setDetail(null)} accessibilityRole="button" accessibilityLabel="Close details" hitSlop={10} style={{ minWidth: 44, minHeight: 44, justifyContent: 'center', alignItems: 'center' }}>
                <Text style={{ fontSize: 22, color: colors.textMuted }}>✕</Text>
              </Pressable>
            </View>
            {!!detail && (
              <ScrollView>
                <View style={{ flexDirection: 'row', justifyContent: 'space-between', marginBottom: spacing.sm }}>
                  <View>
                    <Text style={typography.label}>Date</Text>
                    <Text style={typography.body}>{itemDate(detail)}</Text>
                  </View>
                  <VerdictBadge result="compliant" />
                </View>
                <Text style={{ fontSize: 13, color: colors.textSecondary, marginBottom: spacing.md }}>
                  Assessed by the server against 26 statutory rule checks (2026.09.v1).
                  {(detail?.checks_assessed || detail?.checks) ? ` ${detail.checks_assessed ?? detail.checks} evaluated on this package.` : ''}
                </Text>
                {(detail?.evidence_uris || detail?.photos || detail?.images || []).length > 0 ? (
                  <>
                    <Text style={{ ...typography.label, marginBottom: spacing.sm }}>Evidence</Text>
                    <ScrollView horizontal showsHorizontalScrollIndicator={false} style={{ marginBottom: spacing.sm }}>
                      {(detail.evidence_uris || detail.photos || detail.images || []).map((u, i) => {
                        const rawUri = typeof u === 'string' ? u : u?.thumbnail_url || u?.url || u?.uri || u?.file_path;
                        if (!rawUri) return null;
                        const uri = (rawUri.startsWith('http://') || rawUri.startsWith('https://') || rawUri.startsWith('file://'))
                          ? rawUri
                          : `${API_BASE_URL.replace(/\/+$/, '')}/${rawUri.replace(/^\/+/, '')}`;
                        return (
                          <Image
                            key={i}
                            source={{
                              uri,
                              headers: (getAccessToken() && !uri.startsWith('file://'))
                                ? { Authorization: `Bearer ${getAccessToken()}` }
                                : undefined,
                            }}
                            style={{ width: 160, height: 160, borderRadius: radius.md, marginRight: spacing.sm, backgroundColor: colors.borderLight }}
                          />
                        );
                      })}
                    </ScrollView>
                    <Text style={{ fontSize: 11, color: colors.textMuted }}>
                      Swipe horizontally. Full evidence carousel with zoom is roadmap.
                    </Text>
                  </>
                ) : (
                  <Text style={{ fontSize: 12, color: colors.textMuted }}>No evidence images returned for this inspection.</Text>
                )}
              </ScrollView>
            )}
          </View>
        </View>
      </Modal>
    </View>
  );
}
