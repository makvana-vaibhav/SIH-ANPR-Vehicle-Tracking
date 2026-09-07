# NagarNetra — where the build stands

*Written 25 August 2026, after Phase 4. Event is 10–11 September.*

This document answers three questions: what exists, what is real versus
simulated, and what comes next.

---

## 1. What the platform does today

Sign in at **http://localhost:8080** (`admin` / `NagarNetra@2026`) and you get:

| Screen | What it does |
|---|---|
| **Login** | Six roles, each with genuinely different access |
| **GIS Map** | 250 cameras on Gujarat's 33 real district boundaries, clustered, coloured by status, filterable by department / vendor / status / ANPR / free text |
| **Camera panel** | Metadata, 24-hour uptime strip, probe-on-demand, and **live video** |
| **Fleet Health** | Availability by department and vendor, grouped failure causes, gap analysis |
| **Integration** | The federated VMS instances and every protocol adapter |

Behind it: 45 API endpoints, 236 passing tests, 9 containers, one `make demo`.

---

## 2. Is the camera integration real or fake?

**The honest answer: the integration code is real. The cameras it currently
talks to are simulated — and that is the same thing the challenge organisers
do.**

Worth separating four distinct things, because "is it real" means different
things for each.

### 2.1 The adapter layer — real

`services/api/app/adapters/` contains five working implementations of one
interface. None are stubs; all make real network calls.

| Adapter | What it genuinely does |
|---|---|
| `RtspAdapter` | Spawns `ffprobe`, negotiates a real RTSP session, reads codec, resolution, frame rate and bitrate off the wire. Classifies failures into groupable codes (`unauthorized`, `stream_not_found`, `connection_refused`…). |
| `OnvifAdapter` | Real SOAP calls to ONVIF Device and Media services — `GetDeviceInformation`, `GetProfiles`, `GetStreamUri`. Parses with `defusedxml`, because camera-VLAN XML is untrusted input. |
| `VendorVmsAdapter` | Real REST federation: token auth with expiry-aware caching, camera-list sync, per-request stream resolution, playback lookup. Field-mapped so one class covers Milestone, Genetec, CP Plus and Hikvision. |
| `HostedGridAdapter` | Reads the **organisers' own** `GET /api/ingest` catalogue and their `rtsp://host:8554/stream/<id>` + `:8889/whep` + `/live/.../index.m3u8` endpoints. |
| `SimulatedVmsAdapter` | Queries MediaMTX's control API for which paths are genuinely publishing. |

Point any of the first four at a real endpoint and it works. That is not a
claim — it is what the code does, and swapping is a one-field change to
`vms_instances.adapter_type`.

### 2.2 The video — real RTSP, synthetic content

The 24 live cameras are **real RTSP streams**, published by `ffmpeg` into
MediaMTX and consumed over real WebRTC. The transport, the gateway, the
handshake and the player are all production paths. What is synthetic is the
*content*: generated test patterns, because `data/videos/` is empty until
`make videos` fetches footage.

This is precisely what the challenge does. Their FAQ (Q39–40) describes taking
~12 hours of footage from 30+ government cameras and replaying it as
"simulated live streams via Python-based middleware" so teams can integrate
without touching production infrastructure. Our simulator is the same
technique, and their sandbox is a drop-in source for it — **their RTSP and WHEP
ports are identical to ours** (8554, 8889), which is not a coincidence we
engineered so much as one we verified and then aligned with.

### 2.3 The camera registry — real structure, generated locations

The 250 cameras are generated, but not randomly. They sit on **real
coordinates**: 161 at named junctions across ten Gujarat cities, 89 along the
actual NH-27 and NH-48 alignments, with `heading_deg` computed from road
bearing.

That realism is load-bearing rather than decorative. The demo route
Rajkot → Gondal → Jetpur → Junagadh has legs of 38.1 / 29.5 / 31.0 km — about
25 minutes apart at highway speed. Random coordinates would produce a "route"
at impossible speeds, and the Phase 7 plausibility check would correctly reject
it. The seed had to be geographically honest for the intelligence layer to work
at all.

### 2.4 The demo VMS rows — labelled, not disguised

The four vendor VMS records keep their real `vendor` and `base_url`
(`https://vms.rajkot.gujarat.gov.in/api` and so on), because that is the
federation story and it is true. But their `adapter_type` is set to
`simulated`, because those hosts do not exist on a laptop.

We could have left them pointing at unreachable URLs and shown a map of 250
red markers. That would have been "more real" and completely useless. Instead
the demo says what it is, and production is one field per row.

### 2.5 What is *not* built yet

No ANPR. No plate reading, no tracking, no alerts on vehicles. That is Phase 5
and it is next. Today the platform is a registry, an integration layer, a
health monitor and a viewing gateway — Model 1 plus Model 3, complete and
tested. The intelligence tier is the next phase.

---

## 3. What "camera health" actually measures

This is the part most worth reading, because the first implementation was
wrong in an instructive way.

It initially marked **all 250 cameras `offline`** — technically defensible,
since their VMS endpoints are unreachable. But operationally it is a serious
error: "offline" sends an engineer to inspect a camera that is working
perfectly. The cost of that mistake is real money and lost trust in the alert
feed.

So four states now mean four different things:

| Status | Meaning | Response |
|---|---|---|
| `online` | Verified delivering video | None |
| `offline` | VMS reachable and reports no video | **Dispatch — genuine fault** |
| `degraded` | Delivering, but below fps or above latency budget | Investigate; ANPR results are unreliable |
| `unknown` | We cannot reach the VMS, **or** no feed is attached yet | Fix the integration, not the camera |

A camera that **was** online and stops is still `offline` with an alert. The raw
error is identical to a never-integrated camera (`not_publishing`) — only the
*transition* distinguishes them, which is why that logic lives in the status
machine rather than in the probe.

Two related decisions:

* **Integration failures aggregate per VMS.** One dead Milestone server is one
  incident with one fix, not 4,000 alerts burying the feed.
* **Availability is reported twice**: over the whole registry, and over
  *integrated* cameras only. Averaging un-integrated cameras into availability
  understates the health of the estate you actually run and overstates the size
  of an outage. Neither number alone is honest.

Measured: a deliberately killed stream is detected `offline` in **19 seconds**,
with a high-priority alert, against a 30-second target.

---

## 4. Compliance with the challenge

From the organisers' FAQ (full mapping in `docs/REQUIREMENTS.md`):

> **Q12 — "Model 1 (Centralised CCTV Registry & GIS Foundation) is compulsory
> for all submissions and must be combined with one or more of the other
> models."**

We build **Model 5 = Model 1 + Model 3 + selective Model 2**. Model 1's
mandatory features (Q15):

| Required | Status |
|---|---|
| Bulk / manual / API-based camera onboarding | ✅ all three |
| Interactive GIS map with layered filters | ✅ |
| Camera health monitoring | ✅ |
| Gap-analysis reports | ✅ |
| Role-based search and audit trails | ✅ |

The scored live test case (Q26–28) is ~50 distributed feeds from different
departments, onboarded, with a designated vehicle tracked across cameras and
its complete timestamped route reported. Onboarding and monitoring are done;
the tracking half is Phases 5–7.

Bonus credit is explicitly offered for cross-camera tracking, edge processing,
bandwidth optimisation, auditability, operational dashboards, automated alerts,
health monitoring and integration-ready APIs — all of which are in the
architecture rather than bolted on.

---

## 5. Why the central tier carries events, not video

The whole architecture rests on one piece of arithmetic:

| Approach | Central bandwidth at 80,000 cameras |
|---|---|
| Centralise video (Model 4) | 80,000 × 4 Mbps ≈ **320 Gbps** |
| Centralise metadata (this design) | ~2,667 events/s × ~2 KB ≈ **43 Mbps** |

A **~7,000× reduction**, plus on-demand pull for the handful of feeds an
operator is actually watching. That is also why departments keep their own
recordings: we never copy their video, we ask where it is.

---

## 6. Security decisions already made

Not deferred to a hardening phase — they are in the code and tested.

* **Secrets have no in-code defaults.** The API refuses to boot rather than
  sign tokens with a key readable in this repository.
* **`credentials_ref` is a pointer**, never a credential, and is never
  serialised — even to admins.
* **Stream URLs are never returned by list endpoints.** Viewing access is a
  short-lived, camera-scoped signed token, and every issuance writes a
  `camera.view` audit row. A token for camera A is rejected for camera B.
* **Credentials embedded in a stream URL are rejected at validation** —
  `rtsp://admin:pw@host` is how camera passwords leak into databases and logs.
* **ONVIF XML is parsed with `defusedxml`** — verified that an entity-expansion
  bomb raises `EntitiesForbidden` rather than exhausting the health monitor.
* **Refresh tokens rotate**, and revocation **fails closed**: if Redis is
  unreachable, a token is treated as revoked rather than honouring one an
  administrator believes they cancelled.
* **Login is timing-safe** against username enumeration.
* **Audit never stores credentials** and **never fails a request** — a failed
  audit write logs at ERROR instead, because auditing must not become an
  availability risk for the policing work it oversees.
* **Auditors cannot search plates.** The role that oversees plate search does
  not get to perform it.
* **A leaked onboarding API key exposes nothing** — `api_client` can create
  cameras and do nothing else.

---

## 7. Build progress

| Phase | State |
|---|---|
| 0 · Foundation, compose stack, self-documentation | ✅ |
| 1 · Auth, RBAC matrix, audit trail | ✅ |
| 2 · Camera registry, PostGIS, bulk onboarding | ✅ |
| 3 · Integration adapters + health monitoring + gap analysis | ✅ |
| 4 · Stream gateway + command centre portal | ✅ |
| **5 · AI pipeline — ANPR** | **next** |
| 6 · Event engine, watchlist, alerts | |
| 7 · Correlator — cross-camera routes | |
| 8 · Search | |
| 9 · Remaining UI screens | |
| 10 · Scale profile, 80k-camera load test | |
| 11 · Documentation | |
| 12 · Demo hardening | |
| 13 · Person & face detection, crowd counting | added |
| 14 · VAHAN / SARTHI / eGujCop integration | added |

Phases 13 and 14 are required by FAQ Q22 and sequenced after ANPR, because the
scored test case designates a *vehicle*, not a person. Face recognition against
a person database is the most privacy-sensitive capability here, so it ships
with its own permission and audit path rather than inheriting plate-search
access.

---

## 8. Running it

```bash
make demo        # up + migrate + seed + open the browser
make status      # which dependencies are up
make test        # 236 backend tests
make logs S=api  # tail one service
```

| Surface | URL |
|---|---|
| Command centre | http://localhost:8080 |
| API docs | http://localhost:8000/docs |
| Simulator control | http://localhost:9100/streams |
| MediaMTX | http://localhost:8889 |

To see health monitoring react live, kill a stream and watch the map:

```bash
curl -X POST http://localhost:9100/streams/CAM-00086/stop
# ~19s later: the marker turns red and a high-priority alert is raised
curl -X POST http://localhost:9100/streams/CAM-00086/start
```
