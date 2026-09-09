# Demo video — script and shot list

The form asks for a solution video, and says you must submit one **using the
organisers' provided data**. That constraint shapes the whole video: it has to
show the platform running against *their* camera grid, not only against a clip
you chose.

**Target: 5–7 minutes.** Judges watch many of these. Everything that matters is
in the first 90 seconds.

**Record at 1920×1080, 30 fps.** Browser fullscreen, no bookmarks bar, no
notifications. Do a dry run first — a video where you fumble a login costs more
credibility than a missing feature.

---

## Before you record

```bash
make demo          # verifies each judge moment before saying ready
```

Then confirm by eye:
- the map has cameras on it
- the watchlist has `GJ03AB1234` on it
- plate reads are arriving

If `make demo` says the watchlist is empty, stop — two of the five moments will
have nothing to show.

Have `docs/PANIC.md` open on a second screen. Record in **two takes and cut**, rather
than one nervous continuous take.

---

## 0:00–0:35 · Open with the problem, not the product

**On screen:** the GIS map, ~280 cameras across Gujarat.

> "Gujarat already runs CCTV across police, traffic, municipal and highways
> departments — on Milestone, on Genetec, on Hikvision. Every one of those is
> an island. Tracking one vehicle across three districts today means three
> phone calls.
>
> NagarNetra does not replace any of them. It federates them. This is 281
> cameras from multiple departments and multiple vendors, on one map."

**Do:** filter by department. Filter by status. Let them see it respond.

*Do not* start with "Hello, we are team X". Start with the map.

---

## 0:35–1:15 · Judge moment 1 — the registry is real

> "Cameras get here three ways: bulk CSV, a manual form, or a self-registration
> API so another department onboards its own without us in the loop."

**Do:** open the onboarding screen. Upload a CSV **with a deliberately bad row**.
Show the per-row error report.

> "It validates per row and tells you which row failed and why. That matters
> more than accepting a clean file."

**Do:** click a camera → video plays.

> "Streams are pulled only while someone is watching. An unwatched camera costs
> zero bandwidth."

---

## 1:15–2:15 · Judge moment 2 — live ANPR on the organisers' grid

**This is the section that must use their data.** Say so out loud:

> "This is the Hosted Camera Grid you provided, live."

**Do:** show plates being read, bounding boxes drawn, the plate feed updating.

> "ANPR runs across the whole fleet in the background — not one camera at a
> time. Open any camera and you see what it has been reading."

**Then be honest, on camera:**

> "These are junction overview cameras, not lane-facing ANPR cameras. In
> daylight we read plates from them — here is `GJ11CO5913` at 0.92 confidence,
> and GJ11 is the Junagadh RTO, which is geographically consistent with where
> that camera is. At night, headlight glare means we read close to nothing.
> Detection, tracking, health and events work at any hour. Plate reading
> follows the light."

**Why say that:** the judges own those cameras. They know what they see at
night. Claiming perfect night ANPR is the fastest way to lose them.

---

## 2:15–3:00 · Judge moment 3 — the alert fires by itself

**Do:** add a plate you know is about to appear to the watchlist. Set category
`stolen`, priority `critical`, and **enter a case reference**.

**Do:** switch to the alerts screen. Wait. Let the alert arrive on camera.

> "Nobody triggered that. The plate was seen, matched, and the alert was
> pushed to every connected operator — measured at 24 milliseconds from
> detection to screen."

**Do:** open the alert. Show the crop, camera, timestamp, confidence, case ref.

> "Matching is order of the plate length, not order of the watchlist. At 80,000
> entries it costs the same as at five."

**Do:** acknowledge it. Mention that dispatching and closing are separate
permissions, because in a real control room they are separate authorities.

---

## 3:00–4:00 · Judge moment 4 — a plate becomes a route

**Do:** search `GJ03AB1234`. Show every sighting. Then the drawn route.

> "Every sighting, ordered, with timestamps, distances and implied speeds —
> and the route drawn across cameras."

**Do:** point at a flagged hop.

> "This leg is marked uncertain. We measure straight-line distance between
> cameras, so the implied speed is a *lower bound* — the real road is longer.
> We show what we can defend, and we flag what we cannot rather than dropping
> it. A system that hides its uncertainty invites decisions its evidence does
> not support."

---

## 4:00–5:00 · Judge moment 5 — the scale claim, with a measurement

**Do:** show the architecture, then the load-test result.

> "80,000 cameras centralised is 320 gigabits per second of video. Federated,
> it is 2,667 events per second at two kilobytes each — 43 megabits. About
> seven thousand times less. That is why existing district links are enough.
>
> We ran it. 80,000 camera identities, three ingest workers, on this laptop:
> 388,000 events written, zero failures, the backlog cleared, and 397
> watchlist alerts raised while it was running."

**Then, the strongest 20 seconds in the video:**

> "The load test found a real bug in our own code. A case-insensitive camera
> lookup was defeating a database index. At our 281 development cameras it was
> invisible. At 80,000 it was a 106-millisecond sequential scan on every batch,
> and it collapsed ingest sevenfold. It is fixed — migration 0003. We are
> telling you because a load test that finds nothing was not a load test."

---

## 5:00–5:40 · Governance

**Do:** open the audit log. Filter to show a **denied** attempt.

> "Every plate search, every stream opened, every watchlist change is recorded
> — including the ones that were refused. Reading the audit log is itself
> audited, including for administrators. And an auditor cannot run a vehicle
> search: reviewing who traced whom is a different job from tracing people."

---

## 5:40–6:10 · What is not built

Say it out loud. Do not bury it.

> "What we have not done: there is no TLS, no encryption at rest, and no tested
> restore — so we claim no recovery objective. The plate detector weights are
> AGPL and that obligation has to be resolved before deployment. All of it is
> in our security document, section 8. If you find something that is not on
> that list, that is a real finding and we would want to know."

---

## 6:10–6:30 · Close

> "One laptop. `docker compose up`. No cloud, no paid APIs, works offline.
> The central tier carries events, not video — and that is why it reaches
> 80,000 cameras on infrastructure Gujarat already has."

---

## Recording notes

- **Cut, don't wait.** If the alert takes 40 seconds, cut to it arriving.
- **Zoom in on small text.** Plate crops and confidence values are unreadable
  at 1080p full-screen. Crop in post.
- **No background music under speech.** This is an operations product.
- **Show real latency.** If something takes two seconds, let it take two
  seconds. Speeding up video is noticed and it costs trust.
- **Upload to Drive as unlisted, and check the sharing link in an incognito
  window** before pasting it into the form. A submission link that 404s for the
  reviewer is the same as no submission.
