import React, { useState } from 'react';
import { View, Text, Image, Pressable, ActivityIndicator, StyleSheet } from 'react-native';
import { colors, radius, spacing } from '../theme';
import { getAccessToken } from '../api/client';
import { API_BASE_URL } from '../api/config';
import {
  buildImageUrl as _buildImageUrl,
  getImageAuthHeaders as _getImageAuthHeaders,
  normalizeEvidenceList as _normalizeEvidenceList,
  createEvidenceKey,
  CANONICAL_PANEL_ORDER,
} from '../config/evidenceRendering';

export const buildImageUrl = (rawUri, baseUrl = API_BASE_URL) => _buildImageUrl(rawUri, baseUrl);
export const getImageAuthHeaders = (uri, token = getAccessToken()) => _getImageAuthHeaders(uri, token);
export const normalizeEvidenceList = (scan, scanId = null, evidenceIntegrity = null, baseUrl = API_BASE_URL) =>
  _normalizeEvidenceList(scan, scanId, evidenceIntegrity, baseUrl);
export { createEvidenceKey, CANONICAL_PANEL_ORDER };

export default function EvidenceThumbnail({
  uri,
  panel = 'panel',
  sha256 = null,
  width = 72,
  height = 72,
  onPress,
  style,
  imageStyle,
  accessibilityLabel,
  testID,
}) {
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState(false);
  const [reloadKey, setReloadKey] = useState(0);

  const cleanUri = buildImageUrl(uri);
  const authHeaders = getImageAuthHeaders(cleanUri);

  const handleRetry = () => {
    setLoadError(false);
    setLoading(true);
    setReloadKey((prev) => prev + 1);
  };

  const a11yLabel = accessibilityLabel || `View ${panel} panel evidence`;

  return (
    <Pressable
      testID={testID}
      onPress={onPress}
      style={[styles.container, { width, height }, style]}
      accessibilityRole="imagebutton"
      accessibilityLabel={a11yLabel}
    >
      {cleanUri ? (
        <>
          <Image
            key={`img-${reloadKey}-${cleanUri}`}
            source={{
              uri: cleanUri,
              headers: authHeaders,
            }}
            style={[styles.image, { width, height }, imageStyle]}
            resizeMode="cover"
            onLoadStart={() => {
              setLoading(true);
              setLoadError(false);
            }}
            onLoadEnd={() => setLoading(false)}
            onError={() => {
              setLoading(false);
              setLoadError(true);
            }}
          />

          {loading && !loadError && (
            <View style={[styles.overlay, styles.loadingOverlay]}>
              <ActivityIndicator size="small" color={colors.niyamBlue} />
            </View>
          )}

          {loadError && (
            <View style={[styles.overlay, styles.errorOverlay]}>
              <Text style={styles.errorIcon}>⚠️</Text>
              <Text style={styles.errorText}>Load failed</Text>
              <Pressable onPress={handleRetry} style={styles.retryBtn}>
                <Text style={styles.retryText}>Retry</Text>
              </Pressable>
            </View>
          )}
        </>
      ) : (
        <View style={[styles.emptyBox, { width, height }]}>
          <Text style={styles.emptyText}>No photo</Text>
        </View>
      )}

      {panel && (
        <View style={styles.panelBadge}>
          <Text style={styles.panelBadgeText} numberOfLines={1}>
            {panel.toUpperCase()}
          </Text>
        </View>
      )}
    </Pressable>
  );
}

const styles = StyleSheet.create({
  container: {
    borderRadius: radius.sm,
    backgroundColor: colors.borderLight,
    borderWidth: 1,
    borderColor: colors.border,
    overflow: 'hidden',
    position: 'relative',
    marginRight: 10,
    alignItems: 'center',
    justifyContent: 'center',
  },
  image: {
    borderRadius: radius.sm - 1,
  },
  overlay: {
    ...StyleSheet.absoluteFillObject,
    justifyContent: 'center',
    alignItems: 'center',
    borderRadius: radius.sm - 1,
  },
  loadingOverlay: {
    backgroundColor: 'rgba(248, 250, 252, 0.75)',
  },
  errorOverlay: {
    backgroundColor: '#FEF2F2',
    padding: 2,
  },
  errorIcon: {
    fontSize: 12,
  },
  errorText: {
    fontSize: 8,
    color: colors.error || '#B91C1C',
    fontWeight: '700',
    marginTop: 1,
  },
  retryBtn: {
    marginTop: 2,
    paddingHorizontal: 4,
    paddingVertical: 1,
    backgroundColor: '#FEE2E2',
    borderRadius: 2,
    borderWidth: 0.5,
    borderColor: '#FCA5A5',
  },
  retryText: {
    fontSize: 8,
    color: '#991B1B',
    fontWeight: '800',
  },
  emptyBox: {
    justifyContent: 'center',
    alignItems: 'center',
    backgroundColor: colors.borderLight,
  },
  emptyText: {
    fontSize: 10,
    color: colors.textMuted,
  },
  panelBadge: {
    position: 'absolute',
    bottom: 2,
    left: 2,
    right: 2,
    backgroundColor: 'rgba(15, 23, 42, 0.78)',
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
});
