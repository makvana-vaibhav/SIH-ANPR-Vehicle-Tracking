# Screenshots — what to capture and why

The form asks for a screenshots folder. Judges scroll it quickly, so it should
read as a **narrative in order**, not a gallery. Number the files; the numbers
are the story.

Capture at **1920×1080**, browser chrome hidden (F11 / fullscreen), dark theme.
`scripts/capture_screenshots.py` takes 1–8 automatically and consistently;
9–12 need a human because they involve interaction.

Before capturing anything: `make demo` — it verifies the platform is in a state
worth photographing rather than assuming it.

---

## The set

| # | File | What must be visible | Why it is in the folder |
|---|---|---|---|
| 1 | `01-gis-map.png` (`/map`) | Gujarat map, ~280 cameras, status colours, filter panel open | The compulsory Model 1 deliverable, in one image |
| 2 | `02-fleet-health.png` (`/health`) | Online / offline / degraded / unknown counts, per-department breakdown | Camera health monitoring — and that `unknown ≠ offline` |
| 3 | `03-camera-detail.png` | Live video playing, ANPR overlay boxes, recent reads beside it | Judge moment 1 completed: click a camera, watch video |
| 4 | `04-live-anpr.png` (`/anpr`) | Plate feed updating, confidence per read, multiple cameras contributing | Judge moment 2 |
| 5 | `05-alert-fired.png` | Red critical alert: plate crop, camera, timestamp, confidence, case ref | Judge moment 3 — the moment that sells the product |
| 6 | `06-alerts-list.png` | Priority-sorted list, lifecycle states visible | It is a workflow, not a notification |
| 7 | `07-vehicle-route.png` | Route drawn across ≥3 cameras, hop table with timestamps and implied speeds | Judge moment 4 — the scored live test case |
| 8 | `08-search.png` (`/vehicles`) | Plate search with results, filters, faceted counts | Search across the fleet |
| 9 | `09-watchlist.png` | Entries with category, priority, **case reference** | Purpose limitation, visible |
| 10 | `10-onboarding.png` (`/integration`) | CSV upload with a **row-level error report shown** | Bulk onboarding that validates — show it rejecting a bad row, not a clean success |
| 11 | `11-audit-log.png` (`/audit`) | Audit rows including a **denied** attempt | The governance story |
| 12 | `12-load-test.png` | The load-test result table from `tests/load/results/latest.md` | Judge moment 5 — the measured number |

---

## Two deliberate choices

**Screenshot 10 shows a failure.** A clean CSV upload proves nothing — every
system can accept a good file. Upload one with a bad coordinate and a duplicate
code, and photograph the per-row error report. That is the feature.

**Screenshot 11 shows a denied attempt.** Sign in as `analyst`, try to open a
stream, get refused, then photograph the audit row. A trail that only records
successes is not a trail.

---

## Capturing

```bash
make demo                                   # platform in a known-good state
python3 scripts/capture_screenshots.py      # writes docs/submission/screenshots/
```

The script signs in as `admin`, waits for each screen's real content to appear
rather than sleeping a fixed interval, and fails loudly if a screen is empty —
a screenshot of a spinner is worse than no screenshot.

For 9–12, drive the UI yourself and use the OS capture. Keep the same window
size so the folder looks like one system rather than four.
