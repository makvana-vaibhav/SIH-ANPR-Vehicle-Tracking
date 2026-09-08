# Infrastructure

Bandwidth, compute, storage and recovery for a statewide deployment.

Every number is labelled **measured**, **arithmetic** or **estimated**. The
distinction matters more than the numbers: this project has measured what it
could on one laptop and has never run a GPU or a load test, and a document that
blurred those into one confident table would be misleading precisely where it
mattered most.

---

## 1. Bandwidth

### The central argument

| | Video centralised | Events federated |
|---|---|---|
| Per camera | 4 Mbps (H.264, 1080p, 15 fps) | ~2 KB per event, ~1 event / 30 s |
| 80,000 cameras | **320 Gbps** | 2,667 events/s ≈ **43 Mbps** |

*Arithmetic.* Video bitrate is a typical municipal encoder setting; event size
is measured from the actual `vehicle.completed` payload (1.8–2.4 KB with
evidence).

The ratio is ~7,000×. It is the reason for federating rather than centralising,
and it is the number the architecture page should lead with.

### On-demand viewing

Streams are pulled only while somebody watches. MediaMTX is configured with
on-demand publishing, so an unwatched camera costs nothing.

| Concurrent viewers | Bandwidth to the centre |
|---|---|
| 10 | 40 Mbps |
| 50 (the challenge's live test case) | 200 Mbps |
| 250 | 1 Gbps |

*Arithmetic.* A district link that would need 320 Gbps to centralise its video
needs a couple of hundred Mbps to serve every operator who is actually looking.

### Per-district sizing

Assuming an even spread of 80,000 cameras across 33 districts (~2,400 each):

| Traffic | Per district |
|---|---|
| Events to the centre | 2,400 / 30 s × 2 KB ≈ **1.3 Mbps** |
| Video, if centralised | 2,400 × 4 Mbps = **9.6 Gbps** |
| Video, on-demand (5 viewers) | **20 Mbps** |

*Arithmetic.* A 10 Mbps district uplink carries the event stream with room to
spare. Nothing about this needs new fibre, which is the practical argument for
Model 5 over Model 4.

### Store and forward

District links fail. The worker holds events in memory and the bus retains them
for its consumer group, so a link outage delays events rather than losing them.

**Measured under load:** a backlog of 10,237 events formed while the offered
rate exceeded what three workers could absorb, and cleared completely once the
rate dropped. That is the store-and-forward property working as intended — the
events were delayed, not lost.

**Not implemented:** on-disk spooling for outages longer than memory allows.
Today a long outage on a busy edge node would drop the oldest events. A bounded
local queue with a documented depth is the fix.

---

## 2. Compute

### Measured, on the development machine

Apple Silicon, 10 cores, 16 GB, **CPU only** — no CUDA is available to Linux
containers on this hardware.

| Workload | Throughput |
|---|---|
| 720p stream, full pipeline | **4.39 fps** |
| 4K, diagnostics off | **2.78 fps** |
| Capture-to-event latency | **385 ms** median, 559 ms p99 |

### Sizing, extrapolated

At one analysed frame every 2 s per camera (enough to catch a vehicle crossing
a junction), one core sustains roughly **8 cameras**.

| Fleet | Cores | Nodes at 40 cores |
|---|---|---|
| 2,400 (one district) | ~300 | ~8 |
| 80,000 (statewide) | ~10,000 | **~250** |

*Estimated, from measured CPU throughput.* A single mid-range GPU running the
same ONNX graphs would change this by roughly an order of magnitude, which
would put a district on one or two boxes. **This repository has never executed
a GPU path**, so that improvement is stated as an expectation and not as a
number.

### Rotation, and what it costs

A worker runs a bounded number of concurrent streams and rotates the rest
through those slots. Coverage becomes partial in time, and the platform states
the gap rather than hiding it:

> 31 cameras across 3 slots, each watched 45 s every 11.2 min

*Measured, from a running worker.* This is what makes under-provisioning
**visible**. The alternative — taking the first N cameras and ignoring the rest
— under-reports silently, and a vehicle passing camera 20 is never seen.

---

## 3. Storage

### Tiering

| Tier | Contents | Retention | Medium |
|---|---|---|---|
| Hot | Detections, alerts, current health | 7 days | NVMe |
| Warm | Detections, crops | 90 days | HDD / network storage |
| Cold | Detections, audit | 1–5 years | Object storage |

### Capacity, at 80,000 cameras

| Item | Per unit | Per day | Per year |
|---|---|---|---|
| Detection row | ~2 KB | 2,667/s × 86,400 × 2 KB ≈ **460 GB** | ~168 TB |
| Plate crop (JPEG, ~15 KB) | 15 KB | ~3.5 TB | — (90-day cap ≈ 310 TB) |
| Audit row | ~1 KB | tens of MB | ~10 GB |

*Arithmetic.* The detection volume dominates and is why `detections` is a
TimescaleDB hypertable: expiry is `drop_chunks`, which unlinks whole partitions
rather than scanning 168 TB of rows.

**Crops are the expensive tier.** If 310 TB is not affordable, the lever is
storing crops only for watchlist hits and low-confidence reads — the ones a
human might need to check — rather than for every vehicle.

### Retention enforcement

Implemented and running daily (`app/services/retention.py`); see
[`SECURITY.md`](SECURITY.md) §4. Media retention is configured but **not yet
wired**, because evidence crops are not yet uploaded to MinIO at all.

---

## 4. Availability and recovery

### What exists

* Health probes on a staggered cycle, writing to a `camera_health` hypertable
  and flipping camera status. **Measured:** a killed stream is marked offline in
  **19 s**.
* Container healthchecks on every service, with dependency ordering.
* Graceful degradation: OpenSearch → `pg_trgm`, WebRTC → HLS, satellite →
  offline basemap, Redis down → rate limiter fails open.
* Stateless API and workers — replaceable without coordination.

### What does not exist

**No backups. No tested restore. Therefore no RPO or RTO can be claimed.**

Stating a target here would be fiction. What a deployment would need:

| | Target | Mechanism |
|---|---|---|
| RPO | 15 min | Postgres WAL archiving to object storage |
| RTO | 1 h | Restore drill, rehearsed and timed |
| Audit log | zero loss | Separate stream, append-only storage |

Each of those is a straightforward piece of work and none of it has been done.
A restore that has never been rehearsed is not a recovery plan.

---

## 5. The one-laptop deployment

The demonstration runs the same topology on one machine.

| | |
|---|---|
| Requirement | Docker Desktop, **8 GB** allocated (OpenSearch + Postgres together OOM at the 2 GB default) |
| Cold start | ~20 s to all-healthy |
| First build | several GB of images — a one-time cost, separate from the 5-minute demo claim |
| Offline | Yes. Map, models and seed data are local; satellite imagery is the only external request and falls back automatically |

Memory is managed by capping OpenSearch's JVM heap at 512 MB and tuning
Postgres `shared_buffers`; the scale profile with Redpanda and multiple workers
is opt-in precisely because it does not fit alongside everything else in 16 GB.

---

## 6. Summary of what is unproven

| Claim | Status |
|---|---|
| 43 Mbps of metadata vs 320 Gbps of video | **Arithmetic.** Event size measured; camera count assumed |
| ~250 nodes for 80,000 cameras | **Estimated** from measured CPU throughput |
| GPU improves this by an order of magnitude | **Expectation.** No GPU has run this code |
| 2,667 events/s sustained | **Measured.** 2,774 events/s across 80,000 camera identities, 0 failures ([HLD §7](HLD.md#7-scaling-to-80000-cameras)) |
| RPO 15 min / RTO 1 h | **Aspiration.** No backup exists to restore from |
| Storage tiering | **Design.** Only the hot tier and hypertable expiry exist today |
