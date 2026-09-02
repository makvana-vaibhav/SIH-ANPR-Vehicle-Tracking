# Demo script

Eight minutes, five judge moments, one laptop. Every step below has been run;
where a step depends on something outside our control, the fallback is named.

**Before you start:** `make demo`, then confirm the checklist in §0. Do not
skip it — three of the five moments depend on a worker being up, and it takes
90 seconds to notice that on stage.

---

## 0. Pre-flight (2 minutes before)

```bash
docker compose ps            # every service healthy
curl -s localhost:8000/health
curl -s localhost:9100/streams | grep CAM-DEMO      # the demo feed is publishing
docker logs sentinel-ai-worker --tail 5 | grep watching
```

Then in the browser at **http://localhost:8080**, sign in as `admin` /
`Sentinel@2026` and check:

- [ ] Dashboard shows **event feed live** (green, top right)
- [ ] "Reading now" is producing plates
- [ ] Watchlist has **no** entry for the plate you are about to add

> If the event feed is amber, the worker is not publishing. Restart it:
> `docker compose --profile ai up -d --force-recreate ai-worker` and wait 30 s.

---

## 1. Fleet on a map (90 s) — *Judge Moment 1*

**Say:** Gujarat already runs CCTV across departments and vendors. We do not
replace those systems; we federate them.

1. **Dashboard** — read the tiles aloud. Point at *"280 of 281 never probed"*
   and say plainly: the registry is bigger than the fleet we analyse, and the
   tile says so rather than showing a flattering number.
2. **GIS Map** — 281 cameras across Gujarat. Filter by department, then by
   status. Click a marker → popup with department, VMS vendor, status.

**Fallback:** if satellite tiles are slow, switch the basemap to *Offline* —
district boundaries are committed GeoJSON and need no network.

---

## 2. Live video and live ANPR (2 min) — *Judge Moment 2*

**Say:** the AI runs beside the camera. Only events cross the network — that is
the whole scaling argument.

1. **Live ANPR** → pick **CAM-DEMO**.
2. Video plays inline. Plate boxes appear over vehicles with the reading and
   its confidence.
3. Point at the **feed on the right**: each card shows how many frames agreed
   and whether the grammar repaired the reading.
4. Now pick **SBX-00001 (Chiman bhai Bridge)** — a real government camera.
   Vehicles are detected and tracked; **no plates are read**, and say why: it
   is a night-time junction overview with headlight glare. The pipeline is the
   same; the camera angle is not.

**This is the honest moment of the demo and it is worth making deliberately.**
A system that claims to read plates off every camera is one nobody in the room
believes.

**Fallback:** if the grid is unreachable, stay on CAM-DEMO and say the grid is
federated and currently down — the registry still lists its cameras, which is
the correct behaviour.

---

## 3. Watchlist → automatic alert (2 min) — *Judge Moment 3*

**Say:** this is what the platform is for.

1. **Watchlist** → add a plate you just saw in the live feed. Category
   `stolen`, priority `critical`, case reference `FIR/2026/DEMO`.
2. Say: matching runs on every settled read across every camera.
3. **Dashboard** or **Alerts** — within a minute or two, as the clip loops and
   that vehicle passes again, a **red critical alert** appears by itself.
4. Open **Alerts**. Show the plate, camera, location, confidence and case
   reference. Acknowledge it with the keyboard: `a`.
5. Say: every transition is recorded against the operator who made it.

**Timing note:** the alert fires when the vehicle next passes — measured at
**33–99 s**. Fill the gap with §4; do not stand and wait.

**Fallback:** `docker compose exec -e SENTINEL_API_URL=http://api:8000 api
python /app/scripts/demo_anpr.py --plate NA13NRU` runs the same chain in a
terminal and prints each step.

---

## 4. Trace a vehicle (90 s) — *Judge Moment 4*

1. **Vehicle Search** → click a suggested plate (those are the ones seen on
   more than one camera).
2. The route draws on the map. Point out that the legs are **dashed and
   straight**: the platform knows where the vehicle was seen, not the roads it
   took, so distances and speeds are lower bounds.
3. Open the hop table — timestamps, dwell, implied speed, and any flags in
   plain English.
4. If a leg is flagged, read it out. **An impossible leg is what a cloned plate
   looks like**, and the system says so rather than hiding it.

**Caveat to state:** only one camera currently produces plates, so a full
four-camera route needs either the grid to provide an ANPR-class feed or a
second demonstration camera. Say it; the correlator is built and tested either
way.

---

## 5. Who did what (60 s) — *the question a police force asks*

1. **Audit** — every plate search, stream open, watchlist change and sign-in.
2. Filter to **Plate searches**. Show your own search from §4.
3. Say: **reading this page is itself recorded**. There is no role that reads
   the trail unseen, including admin.
4. Sign out, sign in as **auditor**. Live ANPR and Vehicle Search are **gone
   from the navigation** — the interface offers only what the role may use.
5. Type `/vehicles` in the address bar → a clean refusal naming the missing
   permission.

---

## 6. Architecture and scale (60 s) — *Judge Moment 5*

Open [`docs/HLD.md`](HLD.md) §1 and read the arithmetic:

> 80,000 × 4 Mbps ≈ **320 Gbps** centralised, versus 2,667 events/s × 2 KB ≈
> **43 Mbps** federated. About 7,000× less.

Then §6 — the measured numbers: 4.39 fps per 720p stream, 385 ms
capture-to-event, 24 ms detection-to-alert, 19 s to detect a dead camera.

**Say plainly:** the load test proving 2,667 events/s sustained has **not been
run**. Every number quoted is measured on this laptop; the 80,000-camera figure
is extrapolation and is labelled as such in the document.

---

## 7. If something breaks

| Symptom | Do this |
|---|---|
| Event feed amber | `docker compose --profile ai up -d --force-recreate ai-worker` |
| No video on CAM-DEMO | `docker compose restart simulator`, wait 15 s |
| Grid cameras unreachable | Expected if their gateway is down. Say so and use CAM-DEMO |
| A screen is blank | Hard refresh. The token may have expired — sign in again |
| Postgres unhealthy | `docker compose restart postgres api`; the API waits for it |
| Everything is wrong | `make down && make demo`. Two minutes |

**Last resort:** `ai-lab/runs/.../annotated.mp4` is a rendered video of the
pipeline reading plates with crops and transcriptions overlaid. Play it and
narrate. It is real output from this pipeline, not a mock-up.

---

## 8. What not to claim

Judges reward honesty and punish overreach. Do not say:

* *"It reads plates from all 30 government cameras."* It reads plates from
  none of them, for camera-angle reasons that are not the pipeline's fault.
* *"99% accuracy."* The 100% figure is on generated plates and is optimistic by
  construction.
* *"It scales to 80,000 cameras."* Say **the architecture is designed for it and
  here is the arithmetic**, and that the load test has not been run.
* *"It's production ready."* It has no TLS, no backups and an AGPL model
  weight. `docs/SECURITY.md` §8 lists all of it.

Every one of these is already written down in this repository. Being the person
who says it first is worth more than hoping nobody asks.
