/**
 * Interactive GIS map of the camera estate.
 *
 * A named Model 1 requirement (challenge FAQ Q15: "interactive GIS map with
 * layered filters"), and the first thing a judge sees.
 *
 * ── Basemaps ────────────────────────────────────────────────────────────
 * Two, switchable, because they serve different needs:
 *
 *   satellite  Esri World Imagery + place labels. Real aerial imagery, so a
 *              junction on screen is recognisably the junction it claims to
 *              be. Requires network access.
 *   offline    Gujarat's 33 district boundaries from a 330 KB local GeoJSON.
 *              No tile server, no external request, renders identically on a
 *              plane or at a venue with hostile wifi.
 *
 * Satellite is preferred when reachable and falls back to offline
 * automatically, with a visible badge — the demo must never depend on a
 * network we do not control, but it should look its best when one exists.
 *
 * ── No GL text ──────────────────────────────────────────────────────────
 * MapLibre symbol layers need a `glyphs` font endpoint, which would be
 * another network dependency. Every label here is HTML instead.
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import maplibregl, { type MapGeoJSONFeature } from 'maplibre-gl'
import 'maplibre-gl/dist/maplibre-gl.css'

import type { CameraFeatureProperties, CameraGeoJSON } from '@/lib/types'

/** Gujarat, framed to fit the whole state. */
const GUJARAT_CENTER: [number, number] = [71.6, 22.6]
const INITIAL_ZOOM = 6.6

/** Status colours, matching the semantic palette in styles/index.css. */
const STATUS_COLORS = {
  online: '#22c55e',
  offline: '#ef4444',
  degraded: '#f59e0b',
  unknown: '#94a3b8',
} as const

export type Basemap = 'satellite' | 'offline'

/**
 * Above this many cameras, switch to clustering. At demo scale (250) every
 * camera is drawn individually — clustering 250 points collapses the whole
 * state into a handful of blobs and hides exactly what the map is for.
 * The threshold exists for the 80,000-camera case.
 */
const CLUSTER_THRESHOLD = 2000

const SATELLITE_TILES =
  'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}'
const SATELLITE_LABEL_TILES =
  'https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}'

interface Props {
  cameras: CameraGeoJSON | null
  districts: GeoJSON.FeatureCollection | null
  onSelect: (camera: CameraFeatureProperties) => void
  selectedCode?: string | null
  basemap: Basemap
  onSatelliteUnavailable?: () => void
}

/** Probe one tile so we can fall back before the user sees a blank map. */
async function satelliteReachable(): Promise<boolean> {
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

export default function CameraMap({
  cameras,
  districts,
  onSelect,
  selectedCode,
  basemap,
  onSatelliteUnavailable,
}: Props) {
  const container = useRef<HTMLDivElement>(null)
  const map = useRef<maplibregl.Map | null>(null)
  const clustered = useRef<boolean | null>(null)
  const [ready, setReady] = useState(false)

  // ── Create the map once ─────────────────────────────────────────────
  useEffect(() => {
    if (map.current || !container.current) return

    map.current = new maplibregl.Map({
      container: container.current,
      style: {
        version: 8,
        // No sprite/glyphs: both are network fetches, and every label in this
        // component is HTML precisely so the map needs neither.
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
          {
            id: 'background',
            type: 'background',
            paint: { 'background-color': '#080d16' },
          },
          {
            id: 'satellite',
            type: 'raster',
            source: 'satellite',
            layout: { visibility: 'none' },
            // Slightly dimmed so status dots stay the brightest thing on
            // screen — this is an operations display, not a mapping tool.
            paint: { 'raster-brightness-max': 0.82, 'raster-saturation': -0.12 },
          },
          {
            id: 'satellite-labels',
            type: 'raster',
            source: 'satellite-labels',
            layout: { visibility: 'none' },
            paint: { 'raster-opacity': 0.75 },
          },
        ],
      },
      center: GUJARAT_CENTER,
      zoom: INITIAL_ZOOM,
      maxZoom: 18,
      minZoom: 5,
      attributionControl: false,
    })

    map.current.addControl(
      new maplibregl.NavigationControl({ showCompass: false }),
      'top-right',
    )
    map.current.addControl(
      new maplibregl.ScaleControl({ maxWidth: 110, unit: 'metric' }),
      'bottom-left',
    )
    map.current.addControl(
      new maplibregl.AttributionControl({ compact: true }),
      'bottom-right',
    )
    map.current.on('load', () => setReady(true))

    return () => {
      map.current?.remove()
      map.current = null
    }
  }, [])

  // ── District boundaries (the offline basemap) ───────────────────────
  useEffect(() => {
    const m = map.current
    if (!m || !ready || !districts || m.getSource('districts')) return

    m.addSource('districts', { type: 'geojson', data: districts })

    // Inserted beneath the camera layers by adding them before cameras load.
    m.addLayer({
      id: 'district-fill',
      type: 'fill',
      source: 'districts',
      paint: { 'fill-color': '#16233a', 'fill-opacity': 0.75 },
    })
    m.addLayer({
      id: 'district-outline',
      type: 'line',
      source: 'districts',
      paint: {
        'line-color': '#3b5578',
        'line-width': ['interpolate', ['linear'], ['zoom'], 6, 0.7, 12, 1.6],
      },
    })
    m.addLayer({
      id: 'district-hover',
      type: 'fill',
      source: 'districts',
      paint: { 'fill-color': '#38bdf8', 'fill-opacity': 0.16 },
      filter: ['==', ['get', 'district'], ''],
    })

    const tooltip = new maplibregl.Popup({
      closeButton: false,
      closeOnClick: false,
      className: 'district-tooltip',
      offset: 10,
    })

    m.on('mousemove', 'district-fill', (event) => {
      const feature = event.features?.[0]
      if (!feature) return
      const name = (feature.properties as { district?: string }).district ?? ''
      m.setFilter('district-hover', ['==', ['get', 'district'], name])
      tooltip.setLngLat(event.lngLat).setHTML(`<strong>${name}</strong>`).addTo(m)
    })
    m.on('mouseleave', 'district-fill', () => {
      m.setFilter('district-hover', ['==', ['get', 'district'], ''])
      tooltip.remove()
    })
  }, [ready, districts])

  // ── Basemap switching ───────────────────────────────────────────────
  useEffect(() => {
    const m = map.current
    if (!m || !ready) return

    const showSatellite = basemap === 'satellite'
    const set = (id: string, visible: boolean) => {
      if (m.getLayer(id)) {
        m.setLayoutProperty(id, 'visibility', visible ? 'visible' : 'none')
      }
    }

    set('satellite', showSatellite)
    set('satellite-labels', showSatellite)
    // District fill would obscure the imagery; the outlines stay in both
    // modes because district context is the point of the map.
    set('district-fill', !showSatellite)

    if (m.getLayer('district-outline')) {
      m.setPaintProperty(
        'district-outline',
        'line-color',
        showSatellite ? '#7dd3fc' : '#3b5578',
      )
      m.setPaintProperty(
        'district-outline',
        'line-opacity',
        showSatellite ? 0.55 : 1,
      )
    }
  }, [ready, basemap])

  // Fall back before the user is left staring at a blank map.
  useEffect(() => {
    if (basemap !== 'satellite') return
    let cancelled = false
    void satelliteReachable().then((ok) => {
      if (!ok && !cancelled) onSatelliteUnavailable?.()
    })
    return () => {
      cancelled = true
    }
  }, [basemap, onSatelliteUnavailable])

  // ── Camera markers ──────────────────────────────────────────────────
  useEffect(() => {
    const m = map.current
    if (!m || !ready || !cameras) return

    const shouldCluster = cameras.features.length > CLUSTER_THRESHOLD

    // Clustering is a source-creation option, so a change of mode means
    // rebuilding the source and its layers.
    if (clustered.current !== null && clustered.current !== shouldCluster) {
      for (const id of ['camera-halo', 'camera-point', 'camera-selected', 'clusters']) {
        if (m.getLayer(id)) m.removeLayer(id)
      }
      if (m.getSource('cameras')) m.removeSource('cameras')
      clustered.current = null
    }

    const existing = m.getSource('cameras') as maplibregl.GeoJSONSource | undefined
    if (existing) {
      existing.setData(cameras as unknown as GeoJSON.FeatureCollection)
      return
    }

    m.addSource('cameras', {
      type: 'geojson',
      data: cameras as unknown as GeoJSON.FeatureCollection,
      cluster: shouldCluster,
      clusterRadius: 50,
      clusterMaxZoom: 11,
      clusterProperties: shouldCluster
        ? {
            offline: ['+', ['case', ['==', ['get', 'status'], 'offline'], 1, 0]],
            online: ['+', ['case', ['==', ['get', 'status'], 'online'], 1, 0]],
          }
        : undefined,
    })
    clustered.current = shouldCluster

    if (shouldCluster) {
      m.addLayer({
        id: 'clusters',
        type: 'circle',
        source: 'cameras',
        filter: ['has', 'point_count'],
        paint: {
          'circle-color': [
            'case',
            ['>', ['get', 'offline'], 0], STATUS_COLORS.offline,
            ['>', ['get', 'online'], 0], STATUS_COLORS.online,
            STATUS_COLORS.unknown,
          ],
          'circle-opacity': 0.8,
          'circle-radius': ['step', ['get', 'point_count'], 14, 50, 20, 500, 28],
          'circle-stroke-width': 1.5,
          'circle-stroke-color': '#ffffff',
          'circle-stroke-opacity': 0.5,
        },
      })

      m.on('click', 'clusters', async (event) => {
        const feature = event.features?.[0]
        if (!feature) return
        const source = m.getSource('cameras') as maplibregl.GeoJSONSource
        const zoom = await source.getClusterExpansionZoom(
          feature.properties.cluster_id as number,
        )
        m.easeTo({
          center: (feature.geometry as GeoJSON.Point).coordinates as [number, number],
          zoom,
        })
      })
    }

    // Soft halo behind each dot: on satellite imagery a flat 4px circle
    // disappears against a bright rooftop or a road marking.
    m.addLayer({
      id: 'camera-halo',
      type: 'circle',
      source: 'cameras',
      filter: ['!', ['has', 'point_count']],
      paint: {
        'circle-color': [
          'match', ['get', 'status'],
          'online', STATUS_COLORS.online,
          'offline', STATUS_COLORS.offline,
          'degraded', STATUS_COLORS.degraded,
          STATUS_COLORS.unknown,
        ],
        'circle-opacity': 0.22,
        'circle-radius': ['interpolate', ['linear'], ['zoom'], 6, 5, 10, 9, 14, 16, 18, 24],
        'circle-blur': 0.45,
      },
    })

    m.addLayer({
      id: 'camera-point',
      type: 'circle',
      source: 'cameras',
      filter: ['!', ['has', 'point_count']],
      paint: {
        'circle-color': [
          'match', ['get', 'status'],
          'online', STATUS_COLORS.online,
          'offline', STATUS_COLORS.offline,
          'degraded', STATUS_COLORS.degraded,
          STATUS_COLORS.unknown,
        ],
        // Small but present across the whole state, growing with zoom.
        'circle-radius': ['interpolate', ['linear'], ['zoom'], 6, 3, 9, 4.5, 12, 7, 16, 11],
        'circle-stroke-width': ['interpolate', ['linear'], ['zoom'], 6, 0.8, 12, 1.6],
        'circle-stroke-color': '#0a0f1a',
        'circle-stroke-opacity': 0.85,
        // Cameras that are up should read first; un-integrated ones recede.
        'circle-opacity': [
          'match', ['get', 'status'], 'unknown', 0.65, 1,
        ],
      },
    })

    m.addLayer({
      id: 'camera-selected',
      type: 'circle',
      source: 'cameras',
      filter: ['==', ['get', 'camera_code'], ''],
      paint: {
        'circle-color': 'transparent',
        'circle-radius': ['interpolate', ['linear'], ['zoom'], 6, 8, 12, 14, 16, 20],
        'circle-stroke-width': 2.5,
        'circle-stroke-color': '#eab308',
      },
    })

    m.on('click', 'camera-point', (event) => {
      const feature = event.features?.[0] as MapGeoJSONFeature | undefined
      if (feature) onSelect(feature.properties as unknown as CameraFeatureProperties)
    })

    // Hover label, as HTML rather than a GL symbol layer.
    const hover = new maplibregl.Popup({
      closeButton: false,
      closeOnClick: false,
      offset: 12,
      className: 'camera-tooltip',
    })

    m.on('mouseenter', 'camera-point', (event) => {
      m.getCanvas().style.cursor = 'pointer'
      const feature = event.features?.[0]
      if (!feature) return
      const p = feature.properties as unknown as CameraFeatureProperties
      hover
        .setLngLat((feature.geometry as GeoJSON.Point).coordinates as [number, number])
        .setHTML(
          `<div class="tt">
             <div class="tt-code">${p.camera_code}</div>
             <div class="tt-name">${p.name ?? ''}</div>
             <div class="tt-meta">${[p.district, p.department_code].filter(Boolean).join(' · ')}</div>
             <div class="tt-status tt-${p.status}">${p.status}</div>
           </div>`,
        )
        .addTo(m)
    })
    m.on('mouseleave', 'camera-point', () => {
      m.getCanvas().style.cursor = ''
      hover.remove()
    })

    for (const layer of ['clusters']) {
      if (!m.getLayer(layer)) continue
      m.on('mouseenter', layer, () => {
        m.getCanvas().style.cursor = 'pointer'
      })
      m.on('mouseleave', layer, () => {
        m.getCanvas().style.cursor = ''
      })
    }
  }, [ready, cameras, onSelect])

  // ── Selection ring follows the selected camera ──────────────────────
  useEffect(() => {
    const m = map.current
    if (!m || !ready || !m.getLayer('camera-selected')) return
    m.setFilter('camera-selected', ['==', ['get', 'camera_code'], selectedCode ?? ''])
  }, [ready, selectedCode])

  const flyTo = useCallback((lon: number, lat: number) => {
    map.current?.flyTo({ center: [lon, lat], zoom: 14, duration: 900 })
  }, [])

  useEffect(() => {
    ;(window as unknown as { __sentinelFlyTo?: typeof flyTo }).__sentinelFlyTo = flyTo
  }, [flyTo])

  return <div ref={container} className="h-full w-full" />
}
