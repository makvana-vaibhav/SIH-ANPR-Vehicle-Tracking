# Sentinel-GJ — progress & next steps

**Read this first.** One page: what works, what doesn't, what to do next.

*Updated 25 Aug 2026 · 5 of 14 phases done · **16 days to the event** (10–11 Sep,
registration closes 7 Sep)*

Detail lives in [BUILD_STATE.md](BUILD_STATE.md) (per-phase gates and every bug
fixed), [docs/STATUS.md](docs/STATUS.md) (real vs simulated, explained), and
[docs/REQUIREMENTS.md](docs/REQUIREMENTS.md) (challenge compliance map).

---

## Where we are

The **platform** is built: registry, integration, health, viewing, and a working
command centre. The **intelligence** is not: no plate reading, no alerts, no
route tracking yet. That is the next block of work and it is the half judges
actually score.

```
DONE      Phase 0 ──▶ 1 ──▶ 2 ──▶ 3 ──▶ 4          (Model 1 + Model 3 complete)
NEXT      Phase 5 ──▶ 6 ──▶ 7 ──▶ 8                (ANPR → alerts → routes → search)
THEN      Phase 9 ──▶ 10 ──▶ 11 ──▶ 12             (UI, scale proof, docs, hardening)
LATER     Phase 13 ──▶ 14                          (face/person, government DBs)
```

By the numbers: 9 containers · 30 API operations · 11,000 lines Python ·
2,500 lines TypeScript · 236 backend + 11 frontend tests · 9 commits.

---

## ✅ Done

### Phase 0 — Foundation
`docker compose up` brings 9 services healthy in ~20s from cold. Every image
pinned and arm64-native. Structured JSON logging with request correlation.
Liveness vs readiness properly separated.

### Phase 1 — Auth, RBAC, audit
Six roles with genuinely different access, enforced by one declarative matrix.
argon2id, JWT access+refresh, refresh-token rotation, revocation that fails
closed. Every mutating request and every search writes an `audit_log` row —
including failed logins and permission denials.

### Phase 2 — Registry + GIS
250 cameras across 5 departments and 5 VMS vendors, on real Gujarat junction and
highway coordinates. PostGIS radius search, district queries, GeoJSON for the
map. CSV bulk onboarding with row-level errors and dry-run. 33 district
boundaries committed (330 KB) — **no tile server, works offline**.

### Phase 3 — Integration + health monitoring
Five real adapters behind one interface: RTSP (ffprobe), ONVIF (SOAP,
defusedxml), vendor REST (covers Milestone/Genetec/CP Plus/Hikvision), the
challenge's own sandbox grid, and the simulator. Health monitor probes on a
staggered 15s cycle with bounded concurrency. **Gap-analysis reports** —
a mandatory Model 1 deliverable.

### Phase 4 — Stream gateway + portal
Live video in the browser over WebRTC/WHEP, playing real traffic footage.
Access is a short-lived camera-scoped token, and every stream open is audited.
Portal screens: Login · GIS Map (layered filters) · Camera detail with live
video and uptime history · Fleet Health · Integration.

**Verified:** killed stream detected offline in **19s** (target <30s) with a
high-priority alert; 250 GeoJSON features; nearby search ordered correctly.

---

## ⏳ Pending

| Phase | Delivers | Why it matters |
|---|---|---|
| **5 · AI pipeline** | Vehicle detect → ByteTrack → plate detect → OCR → **multi-frame consensus** | **Judge Moment 2.** The accuracy story. Nothing downstream exists without it |
| **6 · Events, watchlist, alerts** | Redis Streams consumer, bloom-filter watchlist match, WebSocket push, alert lifecycle | **Judge Moment 3** — the red alert firing by itself |
| **7 · Correlator** | Cross-camera route reconstruction, plausibility scoring, convoy detection | **Judge Moment 4** — and the organisers' scored live test case (FAQ Q26–28) |
| **8 · Search** | OpenSearch partial/fuzzy plate search, pg_trgm fallback | Partial plate → ranked results in <300ms |
| **9 · Remaining UI** | Dashboard, video wall, alerts triage, vehicle profile with animated route, watchlist manager, admin, architecture page | Where the five judge moments are actually performed |
| **10 · Scale proof** | Redpanda, 3 AI workers, 80k-camera load test, k6, Grafana | **Judge Moment 5** — measured numbers, not claims |
| **11 · Documentation** | HLD, INFRASTRUCTURE, SECURITY, API, DEMO_SCRIPT | A required submission artifact |
| **12 · Demo hardening** | `make demo` full path, 500k synthetic detections, e2e test, PANIC.md | Protects the live demo |
| **13 · Person & face** | Person detection, crowd counting, face detection + matching | Required (FAQ Q22). Ships with its own permission and audit path |
| **14 · Government DBs** | VAHAN / SARTHI / eGujCop adapters | Required (FAQ Q22). Real interface, labelled local dataset |

---

## 🔧 Known debt

Real, verified, and not to be mistaken for done.

| Issue | Severity | Note |
|---|---|---|
| **mypy fails — 17 errors, 8 files** | Medium | `make lint` never ran it. CLAUDE.md §5 claims "Python passes mypy" — **currently false**. Fix or amend the claim |
| Footage carries non-Indian plates | Medium | Fine for detection/tracking; OCR scoring needs `generate_synthetic_plates.py` for Gujarat ground truth |
| Vendor adapters never met a real VMS | Medium | Code is real and unit-tested; no Milestone/Genetec server has been on the other end |
| HLS fallback opens a raw `.m3u8` | Low | Non-Safari browsers download instead of playing. Needs hls.js or an embedded page |
| 24 of 250 cameras stream | Low | Laptop encoding limit. `SIM_STREAM_COUNT` → ~50 for the live test case |
| No API rate limiting | Low | Required before anything is exposed beyond localhost |
| Health debounce counters in-memory | Low | Monitor restart resets the failure count. Deliberate, but know it |
| Alembic `downgrade()` never run | Low | Written, untested |
| Portrait clips pillarboxed | Cosmetic | `fetch_videos.sh` pads rather than crops |

---

## ▶ Next steps, in order

### 1. Phase 5 — the ANPR pipeline ← **start here**
The single highest-value remaining item. Everything in Phases 6–8 is blocked on it.

- `scripts/fetch_models.sh` — YOLO vehicle + plate weights, **exported to ONNX in
  a throwaway tools image** so the runtime stays torch-free (~400 MB, and no
  AGPL in the shipped artifact)
- Frame reader: drop-to-latest, 8–12 analysed fps (streams are live RTP with
  monotonic PTS — no seeking)
- ByteTrack in pure NumPy → persistent `track_id`
- Plate rectification: 4-point warp + CLAHE + upscale
- ONNX CRNN OCR (**not** PaddleOCR — no aarch64 wheel exists)
- **Multi-frame consensus** — character-position voting weighted by OCR
  confidence × crop sharpness, position-aware `0↔O 1↔I 8↔B` correction, Indian
  plate grammar validation. One event per track, not per frame
- `--benchmark` mode so the HLD numbers are measured, not invented

*Gate:* real plates read off `data/videos/`, per-stage latency table printed.

### 2. Phase 6 — events, watchlist, alerts
Redis Streams consumer group, bloom-filter watchlist match plus Levenshtein ≤1
as a separate lower-priority "possible match", WebSocket push, alert lifecycle
with dedup. *Gate:* watchlisted plate → alert on a connected client in <2s.

### 3. Phase 7 — correlator
The scored live test case. Cluster sightings into hops, compute implied speeds,
score plausibility against `heading_deg`, **mark low-confidence hops rather than
dropping them**. *Gate:* `GJ03AB1234` → Rajkot → Gondal → Jetpur → Junagadh with
sane speeds as valid GeoJSON.

### 4. Then
Phase 8 search → Phase 9 remaining UI → Phase 10 load test.

### Do before submission (not urgent, but not optional)
- Clear the mypy debt and add it to `make lint`
- `generate_synthetic_plates.py` for measured OCR accuracy
- Raise `SIM_STREAM_COUNT` toward 50 and confirm the laptop holds
- `docs/DEMO_SCRIPT.md` — the 8-minute walkthrough with fallbacks

---

## Running it

```bash
make demo        # up + migrate + seed + open browser
make videos      # fetch real traffic footage (one time)
make status      # dependency readiness
make test        # 236 backend + 11 frontend tests
make logs S=api  # tail one service
```

| Surface | URL |
|---|---|
| Command centre | http://localhost:8080 — `admin` / `Sentinel@2026` |
| API docs | http://localhost:8000/docs |
| Simulator control | http://localhost:9100/streams |

Watch health monitoring react live:

```bash
curl -X POST http://localhost:9100/streams/CAM-00086/stop
# ~19s later the marker turns red and a high-priority alert is raised
curl -X POST http://localhost:9100/streams/CAM-00086/start
```

---

## The three things that must stay true

1. **Model 1 is compulsory** (FAQ Q12) and is complete — registry, GIS map with
   layered filters, health monitoring, gap analysis, audit trails.
2. **The central tier carries events, not video.** 320 Gbps centralised versus
   43 Mbps of metadata — a ~7,000× reduction. Every architecture decision
   follows from this.
3. **Never mock, never stub.** If a phase is marked done, it runs. Where
   something is simulated, it is labelled — see [docs/STATUS.md](docs/STATUS.md) §2.
