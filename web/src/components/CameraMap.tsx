/**
 * Interactive GIS map of the camera estate.
 *
 * A named Model 1 requirement (challenge FAQ Q15: "interactive GIS map with
 * layered filters"), and the first thing a judge sees.
 *
 * No tile server and no tile download. MapLibre renders Gujarat's 33 district
 * boundaries from a 330 KB GeoJSON committed to the repo, with camera markers
 * on top. That means the map looks identical offline, on a plane, or at a venue
 * with hostile wifi — which matters when the demo is scored live.
 *
 * Cameras are clustered client-side by MapLibre's own cluster layer, so zooming
 * and panning stay instant instead of re-querying the server on every gesture.
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import maplibregl, { type MapGeoJSONFeature } from 'maplibre-gl'
import 'maplibre-gl/dist/maplibre-gl.css'

import type { CameraFeatureProperties, CameraGeoJSON } from '@/lib/types'

/** Gujarat, framed to fit the whole state. */
const GUJARAT_CENTER: [number, number] = [71.8, 22.4]
const INITIAL_ZOOM = 6.4

/** Status colours, matching the semantic palette in styles/index.css.
 *  `as const` (rather than Record<string, string>) keeps the keys known, so
 *  `noUncheckedIndexedAccess` does not widen every lookup to `| undefined`. */
const STATUS_COLORS = {
  online: '#22c55e',
  offline: '#ef4444',
  degraded: '#f59e0b',
  unknown: '#64748b',
} as const

interface Props {
  cameras: CameraGeoJSON | null
  districts: GeoJSON.FeatureCollection | null
  onSelect: (camera: CameraFeatureProperties) => void
  selectedCode?: string | null
}

export default function CameraMap({
  cameras,
  districts,
  onSelect,
  selectedCode,
}: Props) {
  const container = useRef<HTMLDivElement>(null)
  const map = useRef<maplibregl.Map | null>(null)
  const [ready, setReady] = useState(false)

  // ── Create the map once ─────────────────────────────────────────────
  useEffect(() => {
    if (map.current || !container.current) return

    map.current = new maplibregl.Map({
      container: container.current,
      style: {
        version: 8,
        // No sprite or glyphs URL: both would be network fetches, and the map
        // must work with no external requests at all.
        sources: {},
        layers: [
          {
            id: 'background',
            type: 'background',
            paint: { 'background-color': '#0a0f1a' },
          },
        ],
      },
      center: GUJARAT_CENTER,
      zoom: INITIAL_ZOOM,
      attributionControl: false,
      maxZoom: 16,
      minZoom: 5,
    })

    map.current.addControl(new maplibregl.NavigationControl({}), 'top-right')
    map.current.addControl(
      new maplibregl.ScaleControl({ maxWidth: 120, unit: 'metric' }),
      'bottom-left',
    )
    map.current.on('load', () => setReady(true))

    return () => {
      map.current?.remove()
      map.current = null
    }
  }, [])

  // ── District boundaries (the basemap) ───────────────────────────────
  useEffect(() => {
    const m = map.current
    if (!m || !ready || !districts) return
    if (m.getSource('districts')) return

    m.addSource('districts', { type: 'geojson', data: districts })

    m.addLayer({
      id: 'district-fill',
      type: 'fill',
      source: 'districts',
      paint: { 'fill-color': '#1e293b', 'fill-opacity': 0.55 },
    })
    m.addLayer({
      id: 'district-outline',
      type: 'line',
      source: 'districts',
      paint: { 'line-color': '#334155', 'line-width': 1 },
    })
    m.addLayer({
      id: 'district-hover',
      type: 'fill',
      source: 'districts',
      paint: { 'fill-color': '#38bdf8', 'fill-opacity': 0.12 },
      filter: ['==', ['get', 'district'], ''],
    })

    // Highlight the district under the cursor, with its name in the tooltip.
    const tooltip = new maplibregl.Popup({
      closeButton: false,
      closeOnClick: false,
      className: 'district-tooltip',
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

  // ── Camera markers ──────────────────────────────────────────────────
  useEffect(() => {
    const m = map.current
    if (!m || !ready || !cameras) return

    const existing = m.getSource('cameras') as maplibregl.GeoJSONSource | undefined
    if (existing) {
      existing.setData(cameras as unknown as GeoJSON.FeatureCollection)
      return
    }

    m.addSource('cameras', {
      type: 'geojson',
      data: cameras as unknown as GeoJSON.FeatureCollection,
      cluster: true,
      clusterRadius: 44,
      clusterMaxZoom: 12,
      // Cluster colour is driven by how many cameras inside are offline, so a
      // problem area is visible without zooming in.
      clusterProperties: {
        offline: ['+', ['case', ['==', ['get', 'status'], 'offline'], 1, 0]],
        online: ['+', ['case', ['==', ['get', 'status'], 'online'], 1, 0]],
      },
    })

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
        'circle-opacity': 0.85,
        'circle-radius': [
          'step', ['get', 'point_count'], 16, 10, 22, 50, 30, 100, 38,
        ],
        'circle-stroke-width': 2,
        'circle-stroke-color': '#0a0f1a',
      },
    })

    m.addLayer({
      id: 'cluster-count',
      type: 'symbol',
      source: 'cameras',
      filter: ['has', 'point_count'],
      layout: {
        'text-field': ['get', 'point_count_abbreviated'],
        'text-size': 12,
        'text-allow-overlap': true,
      },
      paint: { 'text-color': '#0a0f1a' },
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
        'circle-radius': ['interpolate', ['linear'], ['zoom'], 6, 3.5, 12, 7, 16, 10],
        'circle-stroke-width': 1.5,
        'circle-stroke-color': '#0a0f1a',
      },
    })

    // A ring marking the selected camera.
    m.addLayer({
      id: 'camera-selected',
      type: 'circle',
      source: 'cameras',
      filter: ['==', ['get', 'camera_code'], ''],
      paint: {
        'circle-color': 'transparent',
        'circle-radius': 14,
        'circle-stroke-width': 3,
        'circle-stroke-color': '#eab308',
      },
    })

    m.on('click', 'camera-point', (event) => {
      const feature = event.features?.[0] as MapGeoJSONFeature | undefined
      if (feature) onSelect(feature.properties as unknown as CameraFeatureProperties)
    })

    // Clicking a cluster zooms into it.
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

    for (const layer of ['camera-point', 'clusters']) {
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
    m.setFilter('camera-selected', [
      '==',
      ['get', 'camera_code'],
      selectedCode ?? '',
    ])
  }, [ready, selectedCode])

  const flyTo = useCallback((lon: number, lat: number) => {
    map.current?.flyTo({ center: [lon, lat], zoom: 13, duration: 900 })
  }, [])

  useEffect(() => {
    ;(window as unknown as { __sentinelFlyTo?: typeof flyTo }).__sentinelFlyTo = flyTo
  }, [flyTo])

  return <div ref={container} className="h-full w-full" />
}
