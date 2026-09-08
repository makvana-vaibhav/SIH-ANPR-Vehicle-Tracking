# Seed data

What is here, and — more importantly — what is deliberately *not*.

| File | What it is |
|---|---|
| `watchlist.csv` | The demo watchlist, loaded by `seed.py`. Includes `GJ03AB1234`, the plate `docs/DEMO_SCRIPT.md` types. |
| `sample_cameras.csv` | **Four rows for demonstrating CSV onboarding live.** Not loaded by the seed. |
| `gujarat_districts.geojson` | District boundaries for the offline basemap. |
| `gujarat_places.csv` | Place names for the map, rendered as HTML labels. |

## Why there is no 250-camera fleet any more

There used to be a `cameras.csv` with 250 synthetic cameras, so the GIS map had
something to show at scale. It was a mistake, and removing it is the point of
this change.

**251 of 281 cameras had no stream URL at all.** They sat permanently at status
`unknown`, they could never be watched, they could never contribute a
detection, and they made every screen that counted cameras report a number that
was mostly fiction. Fleet health reported on cameras that could not be
unhealthy. The map looked impressive and meant nothing.

The registry now contains only cameras with a real video source: the
organisers' grid, plus one demonstration camera carrying a recorded clip that
says so in its name. Roughly 31 instead of 281 — and every one of them can be
opened, watched, and analysed.

## The sample CSV, and its deliberately bad row

`sample_cameras.csv` exists to *demonstrate* bulk onboarding rather than to
populate anything, so it is small enough to read on a projector.

Row 4 is invalid on purpose: its coordinates are in London. Uploading this file
shows three cameras created and one rejected, with the reason — which is the
feature. A CSV that imports cleanly demonstrates nothing that an empty file
would not.

These cameras have no `stream_url`, so they onboard as registry records with no
feed. That is honest, and it is what onboarding a camera the state has recorded
but not yet connected actually looks like. Delete them from the admin panel
when you are done demonstrating.
