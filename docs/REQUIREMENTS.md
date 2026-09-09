> # ⚠️ MAPS THE WRONG BRIEF
>
> This document maps the requirements of the **old statewide Gujarat CCTV challenge** —
> its reference models, its FAQ items, its live test case. The project now targets
> **SIH26127 — City-Wide AI Engine for Multi-Camera ANPR Trajectory Tracking and Urban Traffic
> Analytics**, whose requirements are different.
>
> Do **not** use this as the compliance map. The current requirements and their status are the
> 8 demo steps in **[docs/ROADMAP.md](ROADMAP.md)**. Rewriting this file for SIH26127 is P12.

---

# Challenge requirements → where we implement them

Source: **sentinel.gujarat.gov.in** (home, /resource, /faqs), retrieved 25 Aug 2026.
This is the compliance map. Every mandatory item must trace to working code.

---

## 1. The mandatory architecture constraint

> **Q12 — "Model 1 (Centralised CCTV Registry & GIS Foundation) is compulsory for all
> submissions and must be combined with one or more of the other models."**

We build **Model 5 = Model 1 (mandatory) + Model 3 (federation middleware) +
selective Model 2 (direct VMS/RTSP/ONVIF integration)**. Compliant, and Q23
explicitly permits "a fully innovative/customised architecture".

| Model | Definition (per FAQ) | Our position |
|---|---|---|
| 1 | Centralised CCTV registry & GIS foundation | **Implemented — mandatory** |
| 2 | Direct connection to each departmental VMS via RTSP/ONVIF/vendor SDK/API | **Implemented, selectively** — adapters pull on demand |
| 3 | Middleware integrating multiple departmental VMS via API/SDK/metadata exchange | **Implemented** — the federation core |
| 4 | Single consolidated central VMS owning recording + storage | **Deliberately rejected** — see below |
| 5 | Hybrid / custom | **This is us** |

Why not Model 4: Gujarat already runs departmental VMS from multiple vendors.
Replacing them is politically and financially unrealistic, and centralising
video costs ~320 Gbps at 80,000 cameras. We federate instead, so departments
keep their own recordings and **the central tier carries events, not video**
(~43 Mbps of metadata — a ~7,000× reduction).

---

## 2. Model 1 mandatory features (Q15)

> "Bulk/manual/API-based camera onboarding, interactive GIS map with layered
> filters, camera health monitoring, gap-analysis reports, and role-based
> search/audit trails."

| Feature | Where | Phase |
|---|---|---|
| Bulk onboarding (CSV) | `POST /api/v1/cameras/bulk` — row-level errors, dry-run | 2 ✅ |
| Manual onboarding | `POST /api/v1/cameras` + Onboarding screen | 2 ✅ |
| API-based onboarding | Same endpoint, `api_client` role with an API key | 2 ✅ |
| Camera metadata (location, department, type, ownership, connectivity, storage) | `cameras` + `vms_instances` tables | 2 ✅ |
| **Interactive GIS map with layered filters** | MapLibre map screen, filter by department/status/district/vendor/type | 3–4 |
| **Camera health monitoring** | Health monitor + `camera_health` hypertable + `/health/fleet` | 3 |
| **Gap-analysis reports** | `/api/v1/health/gaps` — coverage and blind-spot analysis | 3 |
| Role-based search + audit trails | RBAC matrix + `audit_log` | 1 ✅ |

---

## 3. The live test case (Q26–28) — this is the scored demo

> "~50 geographically distributed simulated camera feeds from different
> departments"; teams must "onboard these cameras and enable centralized
> monitoring and analytics"; "a designated vehicle number must be tracked across
> different camera locations"; expected output is the "complete route traversed,
> timestamped and location-wise movement history, and evidence of interoperability".

| Requirement | Our implementation | Phase |
|---|---|---|
| Onboard ~50 distributed feeds from different departments | Adapters + bulk onboarding; fleet already spans 5 departments and 5 VMS vendors | 2 ✅ / 3 |
| Centralised monitoring | Live dashboard, GIS map, video wall | 3–4, 9 |
| Analytics on those feeds | ANPR pipeline: detect → track → OCR → consensus | 5 |
| Track a designated vehicle across locations | Correlator, cross-camera route reconstruction | 7 |
| **Complete route + timestamped, location-wise history** | `GET /api/v1/vehicles/{plate}/route` → GeoJSON + ordered hop table | 7 |
| Evidence of interoperability | Multi-vendor adapters, per-camera provenance on every detection | 3 |

Note the sandbox uses "Python-based middleware" to replay ~12 h of footage from
30+ cameras across 5 departments as simulated live streams (Q39–40). Our own
simulator does the same thing, so the two are interchangeable.

---

## 4. Evaluation criteria (Q36)

| # | Criterion | How we serve it |
|---|---|---|
| 1 | Successful test case (onboarding + analytics on government feed) | `HostedGridAdapter` consumes their `/api/ingest` catalogue directly |
| 2 | Solution presentation (clarity, model justification) | `docs/HLD.md` — Model 5 rationale with the bandwidth arithmetic |
| 3 | Solution architecture (technical soundness, HLD quality) | C4 diagrams, failure modes, measured numbers |
| 4 | Working platform (software maturity) | `docker compose up`, tests, audit trail, graceful degradation |
| 5 | Video analytics output (ANPR/detection/report quality) | Multi-frame consensus + measured precision/recall/CER |
| 6 | Scalability and PoC readiness (~80,000 cameras) | Load test at ~2,667 events/s with real measured numbers |
| 7 | Submission completeness | Presentation, HLD, 2 demo videos, output report |

**Bonus credit** (explicitly listed): "innovative hybrid architectures, advanced
cross-camera tracking, additional reliable analytics, edge processing,
bandwidth optimisation, enhanced cybersecurity/auditability, operational
dashboards, automated alerts, health monitoring, and integration-ready APIs."

Every one of those is in the architecture. Bonuses cannot compensate for missing
mandatory requirements, so Model 1 completeness comes first.

---

## 5. Analytics scope (Q22)

> "ANPR, face recognition, crowd/vehicle counting, anomaly detection, statewide
> vehicle tracking" plus integration with **VAHAN, SARTHI, eGujCop, AFIS, NAFIS**.

| Capability | Phase | Notes |
|---|---|---|
| ANPR | 5 | Vehicle detect → track → plate detect → OCR → multi-frame consensus |
| Statewide vehicle tracking | 7 | Cross-camera correlation, route reconstruction, convoy detection |
| Vehicle counting | 5–6 | Falls out of tracking; per-camera and per-corridor counts |
| Anomaly detection | 6 | Speed, convoy, camera-down, unusual-hours sighting |
| Crowd counting | 13 | Person detection density |
| **Face recognition** | **13** | See the note below |
| Government DB cross-reference (VAHAN/SARTHI/eGujCop/AFIS/NAFIS) | 14 | Adapter interface + local reference dataset; the real APIs are not publicly reachable |

### Note on face recognition and government databases

These are genuinely required by the challenge, and are being built — but they
are sequenced **after** ANPR because ANPR is what the scored live test case
actually measures (Q26–28 designate a *vehicle*, not a person).

They also carry obligations the vehicle pipeline does not. Face recognition
against a person database is the most privacy-sensitive capability in this
platform, so it inherits the same controls as everything else and then some:
every match written to `audit_log`, RBAC-gated to specific roles, retention
limits enforced, and no silent enrolment. `docs/SECURITY.md` covers purpose
limitation and supervisor approval for bulk export.

The real VAHAN/SARTHI/AFIS/NAFIS endpoints are not publicly accessible, so we
build the integration **interface** plus a clearly-labelled local reference
dataset standing in for the live service. The adapter is real and swappable;
what it talks to in a demo is not the production registry, and the UI says so
rather than implying we have live access to national databases.

---

## 6. Dataset and stream access (from /resource)

The sandbox exposes a camera catalogue and three consumption paths:

| Path | URL form | Our use |
|---|---|---|
| Catalogue | `GET http://<host>/api/ingest` | `HostedGridAdapter.list_cameras()` |
| RTSP | `rtsp://<host>:8554/stream/<id>` | AI pipeline ingest |
| WebRTC (WHEP) | `http://<host>:8889/stream/<id>/whep` | Browser preview |
| HLS | `http://<host>/live/stream/<id>/index.m3u8` | Fallback / restricted networks |

**Our MediaMTX gateway already uses the identical ports (8554 RTSP, 8889 WHEP),**
so the sandbox is a drop-in source rather than a translation problem.

Their guide warns the catalogue schema is not fixed and "camera ids and the set
of available cameras can change", so the adapter parses defensively and
re-syncs rather than assuming a fixed id set or field names.

Codecs are H.264 and H.265, with varying resolution, frame rate and bitrate.
Streams are live RTP/RTSP with monotonic PTS — **no seeking, no byte-range
fetching** — which is why the frame reader is drop-to-latest.

---

## 7. Timeline

| Date | Milestone |
|---|---|
| 7 Sep 2026 | Registration closes |
| 7 Sep 2026 | Shortlisting |
| 10–11 Sep 2026 | Event |
| 11 Sep 2026 | Results — top 6 demonstrate live |

Prize pool ₹37,00,000.
