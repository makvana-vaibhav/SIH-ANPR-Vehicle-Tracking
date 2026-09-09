# Seed data

What is here, and — more importantly — what is deliberately *not*.

| File | What it is |
|---|---|
| `watchlist.csv` | The demo watchlist, loaded by `seed.py`. Includes `GJ03AB1234`, the plate `docs/DEMO_SCRIPT.md` types. |
| `sample_cameras.csv` | **Four rows for demonstrating CSV onboarding live.** Not loaded by the seed. |
| `gujarat_districts.geojson` | District boundaries for the offline basemap. |
| `gujarat_places.csv` | Place names for the map, rendered as HTML labels. |
| `ahmedabad_roads.geojson` | **P1.** Ten arterial corridor centrelines from OSM, ordered along the road. Input to the fleet generator. |
| `ahmedabad_localities.geojson` | **P1.** 115 named localities, so a camera is "SG Highway at Thaltej" rather than "SG Highway km 12.4". |
| `ahmedabad_cameras.csv` | **P1.** The 69-camera Ahmedabad fleet, loaded by `seed.py`. |

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

## The Ahmedabad fleet, and why it is not the same mistake

`ahmedabad_cameras.csv` puts 69 cameras back into the registry, which looks like
the thing that was just deleted. The difference is the only one that matters:
**every one of these cameras has video behind it.**

They sit on a `simulated` VMS. The simulator publishes each into MediaMTX under
its own code, `SimulatedVmsAdapter` derives the stream URL from that code, and
`probe_health` asks MediaMTX whether the path is actually publishing. A camera
here that is not streaming goes *offline*, visibly — which is exactly what the
250 synthetic rows could never do.

Note the **absent `stream_url` column**, which is deliberate twice over. These
cameras resolve through the adapter rather than a stored URL; and
`gateway.reconcile()` registers a MediaMTX pull path from `stream_url`, so
giving them one that points at the gateway would make MediaMTX pull a path from
itself, loop, and silently block publishing.

Regenerate either file with:

```bash
python scripts/fetch_ahmedabad_osm.py        # network; refreshes the OSM geometry
python scripts/generate_ahmedabad_fleet.py   # offline, deterministic
```

Camera positions are real OSM road vertices, never interpolated points, so no
camera sits in a building or in the Sabarmati.

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
