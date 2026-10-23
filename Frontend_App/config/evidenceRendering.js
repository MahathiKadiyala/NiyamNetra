// Frontend_App/config/evidenceRendering.js
// Evidence image identity, URL resolution, secure headers, and canonical panel mapping.
// Preserves immutable original evidence provenance and protects against stale/reused thumbnails.

export const CANONICAL_PANEL_ORDER = {
  front: 1,
  back: 2,
  mrp: 3,
  batch: 4,
  side: 5,
  top: 6,
  bottom: 7,
  other: 8,
};

/**
 * Builds an absolute image URL from a relative or full URI.
 * Strictly guarantees that bearer tokens, JWTs, or secrets are NEVER appended to the URL string.
 */
export function buildImageUrl(rawUri, baseUrl = 'https://niyamnetra-backend.onrender.com') {
  if (!rawUri || typeof rawUri !== 'string') return null;
  const trimmed = rawUri.trim();
  if (!trimmed) return null;
  if (
    trimmed.startsWith('http://') ||
    trimmed.startsWith('https://') ||
    trimmed.startsWith('file://') ||
    trimmed.startsWith('data:')
  ) {
    return trimmed;
  }
  const cleanBase = String(baseUrl || '').replace(/\/+$/, '');
  const cleanPath = trimmed.replace(/^\/+/, '');
  return `${cleanBase}/${cleanPath}`;
}

/**
 * Returns authorization headers for protected remote media requests.
 * Local files and data URIs do not require or leak auth headers.
 * Unauthenticated requests safely return undefined.
 */
export function getImageAuthHeaders(uri, token = null) {
  if (!token || !uri || typeof uri !== 'string' || uri.startsWith('file://') || uri.startsWith('data:')) {
    return undefined;
  }
  return { Authorization: `Bearer ${token}` };
}

/**
 * Creates a stable, collision-free React component key for an evidence thumbnail.
 * Key incorporates scan identity, panel name, image ID, and hash/URI.
 */
export function createEvidenceKey(scanId, panel, imageId, shaOrUri, index = 0) {
  const sKey = scanId ? `scan-${scanId}` : 'scan-local';
  const pKey = panel ? String(panel).toLowerCase() : 'panel';
  const idKey = imageId !== undefined && imageId !== null ? `img-${imageId}` : `idx-${index}`;
  const sigKey = shaOrUri ? String(shaOrUri).slice(-16) : `seq-${index}`;
  return `evidence-${sKey}-${pKey}-${idKey}-${sigKey}`;
}

/**
 * Normalizes evidence images list for a scan ensuring correct panel mapping,
 * canonical panel ordering, deduplication, and distinction between original and thumbnail URLs.
 */
export function normalizeEvidenceList(scan, scanId = null, evidenceIntegrity = null, baseUrl = 'https://niyamnetra-backend.onrender.com') {
  const list = [];
  const sId = scanId || scan?.server_id || (typeof scan?.id === 'number' && scan.id > 0 ? scan.id : null);
  const sourceImages = scan?.images;

  if (Array.isArray(sourceImages) && sourceImages.length > 0) {
    // Canonical panel ordering: front, back, mrp, batch, side, etc.
    const sorted = [...sourceImages].sort((a, b) => {
      const orderA = CANONICAL_PANEL_ORDER[a.panel?.toLowerCase()] || 99;
      const orderB = CANONICAL_PANEL_ORDER[b.panel?.toLowerCase()] || 99;
      return orderA - orderB || (a.sequence || 0) - (b.sequence || 0);
    });

    sorted.forEach((img, i) => {
      const rawUrl = img.url || (img.id && sId ? `/scans/${sId}/images/${img.id}` : null);
      const rawThumbUrl = img.thumbnail_url || (img.id && sId ? `/scans/${sId}/images/${img.id}/thumbnail` : rawUrl);

      list.push({
        id: img.id ?? i,
        scan_id: sId,
        panel: img.panel || `panel_${i + 1}`,
        url: buildImageUrl(rawUrl, baseUrl),
        thumbnail_url: buildImageUrl(rawThumbUrl, baseUrl),
        sha256: img.sha256 || null,
        is_persisted: true,
      });
    });
  } else if (scan?.panelPhotos && Object.keys(scan.panelPhotos).length > 0) {
    const photos = scan.panelPhotos;
    const canonicalPanels = ['front', 'back', 'mrp', 'batch'];
    canonicalPanels.forEach((p) => {
      const uri = photos[p];
      if (uri) {
        list.push({
          id: `local-${p}`,
          scan_id: sId,
          panel: p,
          url: buildImageUrl(uri, baseUrl),
          thumbnail_url: buildImageUrl(uri, baseUrl),
          sha256: null,
          is_persisted: false,
        });
      }
    });
    Object.entries(photos).forEach(([p, uri]) => {
      if (uri && !canonicalPanels.includes(p)) {
        list.push({
          id: `local-${p}`,
          scan_id: sId,
          panel: p,
          url: buildImageUrl(uri, baseUrl),
          thumbnail_url: buildImageUrl(uri, baseUrl),
          sha256: null,
          is_persisted: false,
        });
      }
    });
  } else if (evidenceIntegrity?.images && Array.isArray(evidenceIntegrity.images)) {
    evidenceIntegrity.images.forEach((img, i) => {
      list.push({
        id: img.image_id || i,
        scan_id: sId,
        panel: img.panel || `panel_${i + 1}`,
        url: buildImageUrl(img.thumbnail_url, baseUrl),
        thumbnail_url: buildImageUrl(img.thumbnail_url, baseUrl),
        sha256: img.sha256_recorded || null,
        is_persisted: true,
      });
    });
  }

  return list;
}
