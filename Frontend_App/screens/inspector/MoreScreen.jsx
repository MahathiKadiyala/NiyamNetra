import React, { useEffect, useState } from 'react';
import { View, Text, ScrollView, Pressable, ActivityIndicator, Alert } from 'react-native';
import { colors, spacing, typography, radius } from '../../theme';
import Header from '../../components/Header';
import Card from '../../components/Card';
import { useAuth } from '../../auth/AuthContext';
import { useSync } from '../../offline/SyncProvider';
import { fetchMe } from '../../api/admin';
import { fetchRuleInfo } from '../../api/inspections';

// Full en/hi strings for this screen (was header-only preview).
const STR = {
  en: {
    more: 'More', signOut: 'Sign Out', officer: 'Officer',
    fieldDivision: 'Field Division', syncNow: 'Sync now', syncing: 'Syncing...',
    pending: 'pending', uploading: 'Uploading to server', tapToSync: 'Tap to sync now',
    help: 'Help & Guidance', about: 'About NiyamNetra & Statutory Rules',
    helpText: 'Capture front, back, MRP and batch panels. The server assesses 26 checks (2026.09.v1) after sync.',
    aboutText: 'NiyamNetra • Legal Metrology (Packaged Commodities) Rules, 2011. 26 statutory rule checks (2026.09.v1).',
    language: 'Language', english: 'English', hindi: 'हिन्दी',
    footer: 'NiyamNetra • Legal Metrology (Packaged Commodities) Rules, 2011',
    langTitle: 'Language', langMsg: 'Choose display language for this screen.',
  },
  hi: {
    more: 'अधिक', signOut: 'साइन आउट', officer: 'अधिकारी',
    fieldDivision: 'क्षेत्रीय प्रभाग', syncNow: 'अभी सिंक करें', syncing: 'सिंक हो रहा है...',
    pending: 'लंबित', uploading: 'सर्वर पर अपलोड हो रहा है', tapToSync: 'सिंक के लिए टैप करें',
    help: 'सहायता और मार्गदर्शन', about: 'नियमनेत्रा और वैधानिक नियमों के बारे में',
    helpText: 'फ्रंट, बैक, MRP और बैच पैनल कैप्चर करें। सिंक के बाद सर्वर 26 जांचों (2026.09.v1) का मूल्यांकन करता है।',
    aboutText: 'नियमनेत्रा • विधिक मापविज्ञान (पैकेज्ड वस्तुएं) नियम, 2011. 26 वैधानिक नियम जांचें (2026.09.v1)।',
    language: 'भाषा', english: 'English', hindi: 'हिन्दी',
    footer: 'नियमनेत्रा • विधिक मापविज्ञान (पैकेज्ड वस्तुएं) नियम, 2011',
    langTitle: 'भाषा', langMsg: 'इस स्क्रीन के लिए भाषा चुनें।',
  },
};

// §5.6 More - profile, queue, language, logout (live profile from /auth/me)
export default function MoreScreen({ navigation }) {
  const { logout, role } = useAuth();
  const { pending, isSyncing, syncNow } = useSync();
  const [lang, setLang] = useState('en');
  const [me, setMe] = useState(null);
  const [ruleInfo, setRuleInfo] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let mounted = true;
    (async () => {
      try {
        const [userData, ruleData] = await Promise.allSettled([fetchMe(), fetchRuleInfo()]);
        if (mounted) {
          if (userData.status === 'fulfilled' && userData.value) setMe(userData.value);
          if (ruleData.status === 'fulfilled' && ruleData.value) setRuleInfo(ruleData.value);
        }
      } catch { /* neutral fallback */ }
      finally { if (mounted) setLoading(false); }
    })();
    return () => { mounted = false; };
  }, []);

  const t = STR[lang] || STR.en;
  const name = me?.full_name || t.officer;
  const empId = me?.employee_id || '—';
  const area = me?.jurisdiction || t.fieldDivision;
  const initials = name.trim().split(' ').map((w) => w[0]).join('').slice(0, 2).toUpperCase() || 'O';

  const openLang = () => {
    Alert.alert(
      t.langTitle,
      t.langMsg,
      [
        { text: STR.en.english, onPress: () => setLang('en') },
        { text: STR.hi.hindi, onPress: () => setLang('hi') },
        { text: 'Cancel', style: 'cancel' },
      ],
    );
  };

  const menuAction = (key) => {
    if (key === 'help') {
      Alert.alert(t.help, t.helpText);
    } else {
      const count = ruleInfo?.total_checks || ruleInfo?.checks_registered || 26;
      const packVer = ruleInfo?.active_rule_pack_version || ruleInfo?.rule_pack_version || '2026.09.v1';
      const details = ruleInfo
        ? `${ruleInfo.name || 'Legal Metrology Rules Engine'}\n\n• Gazette Reference: ${ruleInfo.gazette_ref || 'G.S.R. 826(E)'}\n• ${count} Statutory Checks Enforced (${packVer})\n• Status: ${ruleInfo.status || 'Active'}\n\n${ruleInfo.summary || ruleInfo.description || ''}`
        : t.aboutText;
      Alert.alert(t.about, details);
    }
  };

  return (
    <View style={{ flex: 1, backgroundColor: colors.background }}>
      <Header title={t.more} />
      <ScrollView contentContainerStyle={{ padding: spacing.lg, paddingBottom: 100 }}>
        {/* Profile card */}
        <Card padding="lg" style={{ marginBottom: spacing.md }}>
          {loading ? (
            <ActivityIndicator color={colors.netraTeal} />
          ) : (
            <>
              <View style={{ flexDirection: 'row', alignItems: 'center' }}>
                <View style={{ width: 56, height: 56, borderRadius: 28, backgroundColor: colors.niyamBlue, justifyContent: 'center', alignItems: 'center', marginRight: spacing.md }}>
                  <Text style={{ color: colors.white, fontSize: 20, fontWeight: '700' }}>{initials}</Text>
                </View>
                <View style={{ flex: 1 }}>
                  <Text style={{ fontSize: 16, fontWeight: '700', color: colors.text }}>{name}</Text>
                  <Text style={{ fontSize: 12, color: colors.textMuted, marginTop: 2 }}>{empId}</Text>
                  <View style={{ backgroundColor: colors.info.fill, borderRadius: radius.sm, paddingHorizontal: 6, paddingVertical: 1, marginTop: 4, alignSelf: 'flex-start' }}>
                    <Text style={{ color: colors.info.text, fontSize: 10, fontWeight: '600' }}>{(me?.role || role || 'inspector').toUpperCase()}</Text>
                  </View>
                </View>
              </View>
              <Text style={{ fontSize: 12, color: colors.textSecondary, marginTop: spacing.md, paddingTop: spacing.md, borderTopWidth: 1, borderTopColor: colors.divider }}>
                {area}
              </Text>
            </>
          )}
        </Card>

        {/* Sync queue */}
        {pending > 0 && (
          <Pressable onPress={syncNow} accessibilityRole="button" accessibilityLabel={`Sync now, ${pending} pending`}>
            <Card padding="md" style={{ marginBottom: spacing.md, borderColor: colors.info.border, borderWidth: 1 }}>
              <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
                <View style={{ flexDirection: 'row', alignItems: 'center' }}>
                  <Text style={{ fontSize: 20, marginRight: spacing.md }}>↻</Text>
                  <View>
                    <Text style={{ fontSize: 14, fontWeight: '600', color: colors.text }}>{isSyncing ? t.syncing : `${pending} ${t.pending}`}</Text>
                    <Text style={{ fontSize: 12, color: colors.textMuted }}>{isSyncing ? t.uploading : t.tapToSync}</Text>
                  </View>
                </View>
                <View style={{ width: 32, height: 32, borderRadius: 16, backgroundColor: colors.info.fill, justifyContent: 'center', alignItems: 'center' }}>
                  <Text style={{ color: colors.info.text, fontWeight: '700', fontSize: 14 }}>{pending}</Text>
                </View>
              </View>
            </Card>
          </Pressable>
        )}

        {/* Menu items */}
        <Card padding="none" style={{ marginBottom: spacing.md }}>
          {[
            { key: 'help', label: t.help, icon: '?' },
            { key: 'about', label: t.about, icon: 'ⓘ' },
          ].map((item, idx, arr) => (
            <Pressable
              key={item.key}
              onPress={() => menuAction(item.key)}
              accessibilityRole="button"
              accessibilityLabel={item.label}
              style={{ flexDirection: 'row', alignItems: 'center', padding: spacing.md, borderBottomWidth: idx < arr.length - 1 ? 1 : 0, borderBottomColor: colors.divider }}
            >
              <Text style={{ fontSize: 18, marginRight: spacing.md, width: 24, textAlign: 'center' }}>{item.icon}</Text>
              <Text style={{ flex: 1, fontSize: 14, color: colors.text }}>{item.label}</Text>
              <Text style={{ color: '#64748B', fontSize: 16 }}>›</Text>
            </Pressable>
          ))}
        </Card>

        {/* Language — full en/hi for this screen */}
        <Card title={t.language} padding="md" style={{ marginBottom: spacing.md }}>
          <Pressable onPress={openLang} accessibilityRole="button" accessibilityLabel={t.language}>
            <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
              <View>
                <Text style={{ fontSize: 14, fontWeight: '600', color: colors.text }}>
                  {lang === 'hi' ? STR.hi.hindi : STR.en.english}
                </Text>
                <Text style={{ fontSize: 12, color: colors.textMuted }}>{t.langMsg}</Text>
              </View>
              <Text style={{ color: '#64748B', fontSize: 16 }}>›</Text>
            </View>
          </Pressable>
        </Card>

        {/* Logout */}
        <Pressable onPress={logout} accessibilityRole="button" accessibilityLabel={t.signOut} style={{ marginTop: spacing.lg }}>
          <View style={{ backgroundColor: colors.errorBg, borderRadius: radius.md, padding: spacing.md, alignItems: 'center', borderColor: colors.violation.border, borderWidth: 1 }}>
            <Text style={{ color: colors.error, fontWeight: '600', fontSize: 14 }}>{t.signOut}</Text>
          </View>
        </Pressable>
        <Text style={{ ...typography.caption, textAlign: 'center', marginTop: spacing.xxl }}>
          {ruleInfo?.name ? `${ruleInfo.name} • ${ruleInfo?.total_checks || ruleInfo?.checks_registered || 26} Statutory Checks` : t.footer}
        </Text>
      </ScrollView>
    </View>
  );
}
