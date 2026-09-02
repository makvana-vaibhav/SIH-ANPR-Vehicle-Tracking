/**
 * The shared basemap definition.
 *
 * Extracted so the camera map and the route map cannot drift apart: two copies
 * of a style would eventually disagree about which tiles, which attribution,
 * and — the one that actually bites — whether the offline fallback still
 * works. CLAUDE.md §6 makes the offline path a hard requirement, and a second
 * hand-rolled style is precisely how that requirement gets quietly broken.
 */

import type { StyleSpecification } from 'maplibre-gl'

export type Basemap = 'satellite' | 'offline'

export const GUJARAT_CENTER: [number, number] = [71.6, 22.6]

/**
 * Esri World Imagery. No API key, and the only external request the product
 * makes — probed before use, with the offline style as the fallback.
 */
export const SATELLITE_TILES =
  'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}'
export const SATELLITE_LABEL_TILES =
  'https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}'

/** Probe one tile so we can fall back before the user sees a blank map. */
export async function satelliteReachable(): Promise<boolean> {
  const controller = new AbortController()
  const timer = window.setTimeout(() => controller.abort(), 4000)
  try {
    const response = await fetch(
      SATELLITE_TILES.replace('{z}', '6').replace('{y}', '28').replace('{x}', '45'),
      { signal: controller.signal, mode: 'no-cors' },
    )
    // `no-cors` yields an opaque response; reaching here at all means the
    // request completed rather than failing at the network layer.
    return response.type === 'opaque' || response.ok
  } catch {
    return false
  } finally {
    window.clearTimeout(timer)
  }
}

/**
 * A style with no `sprite` and no `glyphs`.
 *
 * Both are network fetches MapLibre makes on its own, so declaring either
 * would break the "works on a plane" guarantee. Every label in this
 * application is HTML for that reason — see CLAUDE.md §6.
 */
export interface StyleOptions {
  /** Background behind everything. */
  ground?: string
  /**
   * Dim the imagery. The operations map does this so status dots stay the
   * brightest thing on screen; the route map does not, because there the
   * imagery is what an operator is reading the junction from.
   */
  dim?: boolean
}

export function baseStyle({ ground = '#0b1220', dim = false }: StyleOptions = {}): StyleSpecification {
  return {
    version: 8,
    sources: {
      satellite: {
        type: 'raster',
        tiles: [SATELLITE_TILES],
        tileSize: 256,
        maxzoom: 19,
        attribution: 'Imagery © Esri, Maxar, Earthstar Geographics',
      },
      'satellite-labels': {
        type: 'raster',
        tiles: [SATELLITE_LABEL_TILES],
        tileSize: 256,
        maxzoom: 19,
      },
    },
    layers: [
      { id: 'background', type: 'background', paint: { 'background-color': ground } },
      {
        id: 'satellite',
        type: 'raster',
        source: 'satellite',
        layout: { visibility: 'none' },
        ...(dim
          ? { paint: { 'raster-brightness-max': 0.82, 'raster-saturation': -0.12 } }
          : {}),
      },
      {
        id: 'satellite-labels',
        type: 'raster',
        source: 'satellite-labels',
        layout: { visibility: 'none' },
        paint: { 'raster-opacity': dim ? 0.75 : 0.85 },
      },
    ],
  }
}

/** Show or hide the satellite layers to match the chosen basemap. */
export function applyBasemap(map: maplibregl.Map, basemap: Basemap): void {
  const visibility = basemap === 'satellite' ? 'visible' : 'none'
  for (const layer of ['satellite', 'satellite-labels']) {
    if (map.getLayer(layer)) map.setLayoutProperty(layer, 'visibility', visibility)
  }
}
