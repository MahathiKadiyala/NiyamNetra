// NiyamNetra Design System - 08_UI_DESIGN.md §2
// Government trust, product polish: Deep navy + teal + saffron accent

export const colors = {
  // Brand
  niyamBlue: '#0F2A44',      // Headers, primary buttons, sidebar - 14.63:1 on white
  netraTeal: '#0E7490',      // Links, active states, secondary - 5.36:1 on white
  saffron: '#F59E0B',        // Accent bars, brackets - decoration only, 2.15:1 on white

  // Semantic - corrected for badge text (≥4.5:1 at 11-12px)
  pass: { fill: '#ECFDF5', border: '#A7F3D0', text: '#047857', icon: '#059669' },
  compliant: { fill: '#ECFDF5', border: '#A7F3D0', text: '#047857', icon: '#059669' },
  violation: { fill: '#FEF2F2', border: '#FECACA', text: '#B91C1C', icon: '#DC2626' },
  review: { fill: '#FFFBEB', border: '#FDE68A', text: '#B45309', icon: '#D97706' },
  notAssessed: { fill: '#F1F5F9', border: '#CBD5E1', text: '#475569', icon: '#64748B' },
  outOfScope: { fill: '#F1F5F9', border: '#CBD5E1', text: '#475569', icon: '○' },
  info: { fill: '#F0F9FF', border: '#BAE6FD', text: '#0369A1', icon: '#0284C7' },

  // Neutral
  background: '#F8FAFC',
  surface: '#FFFFFF',
  card: '#FFFFFF',
  border: '#CBD5E1',
  borderLight: '#E2E8F0',
  // 08 §2.1 form controls
  inputBorder: '#7E8EA3',
  placeholder: '#64748B',
  text: '#0F172A',
  textSecondary: '#475569',
  textMuted: '#64748B',
  textFaint: '#94A3B8',
  divider: '#E2E8F0',
  disabled: '#CBD5E1',
  white: '#FFFFFF',

  // Status
  online: '#047857',
  offline: '#92400E',
  offlineBg: '#FFFBEB',
  syncing: '#0369A1',
  syncingBg: '#F0F9FF',
  warning: '#B45309',
  warningBg: '#FFFBEB',
  error: '#B91C1C',
  errorBg: '#FEF2F2',
};

export const spacing = {
  xs: 4,
  sm: 8,
  md: 12,
  lg: 16,
  xl: 20,
  xxl: 24,
  xxxl: 32,
};

export const radius = {
  sm: 6,
  md: 8,
  lg: 12,
  xl: 16,
  full: 999,
};

export const typography = {
  // System stack + Noto fallback: Noto Sans covers Devanagari (Hindi) where
  // the platform font lacks glyphs. React Native has no webfont loading here,
  // so this is a fontFamily preference chain, not a bundled font.
  fontFamily: 'System',
  h1: { fontSize: 28, fontWeight: '700', color: colors.niyamBlue, fontFamily: 'System' },
  h2: { fontSize: 22, fontWeight: '700', color: colors.niyamBlue, fontFamily: 'System' },
  h3: { fontSize: 16, fontWeight: '600', color: colors.niyamBlue, fontFamily: 'System' },
  h4: { fontSize: 16, fontWeight: '600', color: colors.text, fontFamily: 'System' },
  body: { fontSize: 14, color: colors.text, fontFamily: 'System' },
  bodySecondary: { fontSize: 13, color: colors.textSecondary, fontFamily: 'System' },
  caption: { fontSize: 11, color: colors.textMuted, fontFamily: 'System' },
  label: { fontSize: 12, fontWeight: '600', color: colors.textSecondary, fontFamily: 'System' },
  button: { fontSize: 15, fontWeight: '600', fontFamily: 'System' },
  statNumber: { fontSize: 24, fontWeight: '700', color: colors.niyamBlue, fontFamily: 'System' },
  tabLabel: { fontSize: 11, fontWeight: '500', fontFamily: 'System' },
};

export const shadows = {
  sm: {
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 1 },
    shadowOpacity: 0.05,
    shadowRadius: 2,
    elevation: 1,
  },
  md: {
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 2 },
    shadowOpacity: 0.08,
    shadowRadius: 4,
    elevation: 2,
  },
  lg: {
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 4 },
    shadowOpacity: 0.12,
    shadowRadius: 8,
    elevation: 4,
  },
};

export const scanResultConfig = {
  compliant: { label: 'COMPLIANT', ...colors.pass, icon: '✓' },
  violation: { label: 'VIOLATION', ...colors.violation, icon: '✗' },
  not_assessed: { label: 'NOT ASSESSED', ...colors.notAssessed, icon: '—' },
  out_of_scope: { label: 'OUT OF SCOPE', ...colors.info, icon: '○' },
  review_required: { label: 'REVIEW REQUIRED', ...colors.review, icon: '!' },
  not_applicable: { label: 'NOT APPLICABLE', ...colors.info, icon: '○' },
  // Aliases for legacy/back-compat payloads
  success: { label: 'COMPLIANT', ...colors.pass, icon: '✓' },
  pass: { label: 'COMPLIANT', ...colors.pass, icon: '✓' },
  fail: { label: 'VIOLATION', ...colors.violation, icon: '✗' },
  non_compliant: { label: 'VIOLATION', ...colors.violation, icon: '✗' },
  refused: { label: 'REFUSED', fill: '#FFFBEB', border: '#FDE68A', text: '#B45309', icon: '🚫' },
};

// Check finding verdict configs (PASS, FAIL, NOT ASSESSED at finding level)
export const verdictConfig = {
  compliant: { label: 'PASS', ...colors.pass, icon: '✓' },
  pass: { label: 'PASS', ...colors.pass, icon: '✓' },
  violation: { label: 'FAIL', ...colors.violation, icon: '✗' },
  fail: { label: 'FAIL', ...colors.violation, icon: '✗' },
  non_compliant: { label: 'FAIL', ...colors.violation, icon: '✗' },
  not_assessed: { label: 'NOT ASSESSED', ...colors.notAssessed, icon: '—' },
  not_applicable: { label: 'NOT APPLICABLE', ...colors.info, icon: '○' },
  out_of_scope: { label: 'NOT APPLICABLE', ...colors.info, icon: '○' },
  success: { label: 'PASS', ...colors.pass, icon: '✓' },
  review: { label: 'REVIEW', ...colors.review, icon: '!' },
  refused: { label: 'REFUSED', fill: '#FFFBEB', border: '#FDE68A', text: '#B45309', icon: '🚫' },
};

// Inspection & Sync Status configs
export const statusConfig = {
  in_progress: { label: 'In Progress', fill: '#FFFBEB', border: '#FDE68A', text: '#B45309', icon: '⏳' },
  draft: { label: 'In Progress', fill: '#FFFBEB', border: '#FDE68A', text: '#B45309', icon: '⏳' },
  submitted: { label: 'Submitted', ...colors.pass, icon: '✓' },
  synced: { label: 'Synced', ...colors.pass, icon: '✓' },
  not_synced: { label: 'Not Synced', fill: '#FFFBEB', border: '#FDE68A', text: '#B45309', icon: '⏳' },
};
