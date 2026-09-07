/**
 * Can a control room still work while the state's cameras are all reporting?
 *
 * The ingest load test measures whether events land. This measures the other
 * half, and it is the half an operator would notice: with the bus at
 * statewide volume, does the map still draw, does the alert list still open,
 * does a plate search still come back?
 *
 * Run it *concurrently* with run_load_test.py. Run alone it measures an idle
 * API, which is not the question.
 *
 * The queries are the ones the command centre actually issues on load — taken
 * from the network tab, not invented — so the mix reflects what a shift
 * change costs rather than a synthetic uniform spread.
 */

import http from 'k6/http'
import { check, sleep } from 'k6'
import { Trend, Rate } from 'k6/metrics'

const BASE = __ENV.API_URL || 'http://api:8000'
const USERNAME = __ENV.API_USER || 'admin'
const PASSWORD = __ENV.API_PASSWORD || 'NagarNetra@2026'

const searchLatency = new Trend('plate_search_ms')
const mapLatency = new Trend('map_load_ms')
const alertLatency = new Trend('alert_list_ms')
const failures = new Rate('operator_request_failed')

export const options = {
  scenarios: {
    // 20 operators is a district control room, not the whole state. The
    // ceiling being probed here is the API's, and 20 concurrent users is
    // already far past what a single district generates.
    control_room: {
      executor: 'ramping-vus',
      startVUs: 1,
      stages: [
        { duration: '15s', target: 20 },
        { duration: '60s', target: 20 },
        { duration: '15s', target: 0 },
      ],
      gracefulRampDown: '10s',
    },
  },
  thresholds: {
    // An operator waiting more than 2 s for the alert list has noticed.
    'alert_list_ms': ['p(95)<2000'],
    'map_load_ms': ['p(95)<3000'],
    'plate_search_ms': ['p(95)<3000'],
    'operator_request_failed': ['rate<0.01'],
  },
}

export function setup() {
  const res = http.post(
    `${BASE}/api/v1/auth/login`,
    JSON.stringify({ username: USERNAME, password: PASSWORD }),
    { headers: { 'Content-Type': 'application/json' } },
  )
  if (res.status !== 200) {
    throw new Error(`login failed: ${res.status} ${res.body}`)
  }
  return { token: res.json('access_token') }
}

export default function (data) {
  const headers = { Authorization: `Bearer ${data.token}` }

  // The map. Every operator opens this first, and it is the heaviest read.
  const map = http.get(`${BASE}/api/v1/cameras/geojson`, { headers })
  mapLatency.add(map.timings.duration)
  failures.add(map.status !== 200)
  check(map, { 'map loads': (r) => r.status === 200 })

  // The alert feed, polled continuously by every open command centre.
  const alerts = http.get(`${BASE}/api/v1/alerts?limit=50`, { headers })
  alertLatency.add(alerts.timings.duration)
  failures.add(alerts.status !== 200)
  check(alerts, { 'alerts load': (r) => r.status === 200 })

  // A plate search — the most expensive read an operator can issue, and the
  // one that writes an audit row, so this exercises the audit path too.
  const plate = `GJ${String(1 + Math.floor(Math.random() * 27)).padStart(2, '0')}`
  const search = http.get(`${BASE}/api/v1/detections?prefix=${plate}&limit=25`, { headers })
  searchLatency.add(search.timings.duration)
  failures.add(search.status !== 200)
  check(search, { 'search returns': (r) => r.status === 200 })

  sleep(1)
}
