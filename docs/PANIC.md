# PANIC

Something broke and there are judges watching. Work down this page.

**First rule: keep talking.** Narrate what you are showing while you fix. Silence
reads as failure; a running commentary reads as expertise.

**Second rule: there is always a fallback.** §7 is a real rendered video of this
pipeline reading plates. It has never failed.

---

## 0. Thirty-second triage

```bash
docker compose ps                  # anything not "healthy"?
curl -s localhost:8000/health      # API alive?
curl -s localhost:8080 >/dev/null  # web alive?
```

| What you see | Go to |
|---|---|
| A container is unhealthy or missing | §1 |
| Screens load but no plates appear | §2 |
| A camera shows "Cannot play this stream" | §3 |
| The alert never fires | §4 |
| The map is blank | §5 |
| A screen is blank or 401s | §6 |
| Nothing works and time is up | §7 |

---

## 1. A container is down

```bash
docker compose up -d                       # brings back anything stopped
docker compose logs --tail 30 <service>    # if it keeps dying
```

**Postgres unhealthy** — everything depends on it:
```bash
docker compose restart postgres api ingest
```
The API waits for the DB, so restart both.

**Out of memory** (Docker Desktop under 8 GB) — drop the heavy optional
services; nothing in the demo path needs them:
```bash
docker compose stop opensearch minio
```

**Total reset — two minutes, and it works:**
```bash
make down && make demo
```

---

## 2. No plates are appearing

This is the most likely failure and it is almost always the worker.

```bash
docker logs nagarnetra-ai-worker --tail 20
```

| Log says | Do |
|---|---|
| `watching N/31 …` | It is working. Give it 60 s — the clip loops |
| `not started — the far end rejected our credentials` | Grid credentials expired. §2a |
| `no cameras assigned` | The roster is empty. §2b |
| Nothing / container missing | `docker compose --profile ai up -d ai-worker` |

**Restart it:**
```bash
docker compose --profile ai up -d --force-recreate ai-worker
```
Then wait **60 seconds** — it probes transports and connects before reading.

### 2a. Grid credentials rejected

Only affects the 30 grid cameras. **CAM-DEMO still works** — demo on that and
say the federated grid's session has lapsed, which is a true and unremarkable
thing to happen to somebody else's gateway.

To fix: check `SANDBOX_RTSP_USERNAME` / `SANDBOX_RTSP_PASSWORD` in `.env`, then
```bash
docker compose up -d --force-recreate api ingest ai-worker
```
`restart` does **not** reload `.env` — it must be `up -d --force-recreate`.

### 2b. The roster is empty

The API publishes it every 30 s. If the API restarted recently, wait 30 s.
```bash
docker compose exec redis redis-cli GET nagarnetra:fleet:anpr | head -c 200
```
Empty? Restart the API and wait: `docker compose restart api`

---

## 3. A camera will not play

**Check which camera.** `CAM-DEMO` failing and a grid camera failing are
different problems.

### CAM-DEMO black or erroring
```bash
docker compose restart simulator && sleep 15
curl -s localhost:9100/streams | grep CAM-DEMO
```
If the clip is missing entirely: `ls -la data/videos/anpr_demo.mp4` → if absent,
`make videos` (needs a network).

### A grid camera (SBX-*) will not play
**This is expected sometimes and is not your bug.** Their gateway goes down.

Say: *"That's a federated camera — the department's own gateway is not
responding right now. The registry still lists it, which is the correct
behaviour: unreachable is a problem, not an absence."*

Then click **CAM-DEMO** and carry on. **Do not debug this on stage.**

---

## 4. The alert never fires

The vehicle has to pass the camera *again* — measured at **33–99 s**. Fill the
time with the Vehicle Search screen; do not stand and wait.

If it still has not fired after two minutes:

```bash
# Is the plate actually being read?
docker compose exec postgres psql -U nagarnetra -d nagarnetra -c \
  "SELECT plate_normalised, ts FROM detections WHERE ts > now() - interval '3 min' \
   AND plate_normalised <> '' ORDER BY ts DESC LIMIT 5;"
```

**Add a plate you can see in that list**, not one you remember. The reading has
to match exactly.

**The terminal fallback — this always works and is genuinely impressive:**
```bash
docker compose exec -e NAGARNETRA_API_URL=http://api:8000 api \
    python /app/scripts/demo_anpr.py --plate NA13NRU
```
It prints every step: what the AI read, the watchlist entry, the wait, then the
alert with its evidence.

---

## 5. The map is blank

Click **Offline** on the basemap toggle. District boundaries are committed
GeoJSON and need no network — this is the "works on a plane" claim, so
demonstrating it deliberately turns a failure into a feature.

If there are no markers at all, the seed did not run:
```bash
docker compose exec api python -m scripts.seed
```

---

## 6. A screen is blank, or everything 401s

Your token expired (15 minutes). **Sign out and back in.**

If a screen is blank with no error, hard-refresh: `Cmd+Shift+R`.

If a role is missing tabs — that is **correct**. `analyst` and `auditor` do not
see Live ANPR. Sign in as `admin`.

---

## 7. Nothing works — the fallback

**Play the rendered video.** It is real output from this pipeline: plate crops
magnified, transcriptions overlaid, vehicle boxes tracked.

```bash
open ai-lab/runs/*__demo/annotated.mp4
```

Narrate over it: *"This is our pipeline reading a real traffic clip. Each box is
a tracked vehicle, the inset is the magnified plate crop, and the text above it
is the consensus reading across every frame that vehicle was visible."*

Then show the **documents** — `docs/HLD.md` §6 has every measured number, and
`docs/BRIEFING.md` has the architecture. A judge who cannot see the system
running can still assess the engineering.

---

## 8. Answers to give while you fix

| Situation | What to say |
|---|---|
| Grid camera down | *"Federated camera, their gateway. The registry still lists it — unreachable is a problem, not an absence."* |
| No plates on a grid camera | *"Night junction overview with headlight glare. Detection and tracking work; ANPR needs a camera down a lane. Same pipeline, different angle."* |
| Something is slow | *"CPU-only on a laptop, 4.4 fps per stream measured. Production runs this at the edge with a GPU."* |
| A number looks small | *"That's the measured figure, not a projected one. `docs/HLD.md` labels every number measured, arithmetic or extrapolated."* |

---

## 9. Before you start — the two-minute check

Run this **before** the judges arrive, not while they watch:

```bash
make demo        # brings everything up and verifies it
```

It reports every step and tells you what is wrong. If it ends with
**"Demo ready"** and no warnings, you are fine.

Then open the browser, sign in, and confirm:

- [ ] Dashboard says **event feed live** (green, top right)
- [ ] "Reading now" is producing plates
- [ ] The plate you intend to watchlist is **not already on it**
