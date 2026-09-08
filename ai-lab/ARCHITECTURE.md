# Inference architecture

How the lab is shaped to become the AI tier of the platform, and what that
shape costs. This describes what is built and measured, not what is planned.

---

## The pipeline

```
frame source ──► object detection ──► tracking ──► plate scheduler
                                                        │
                                              (is this vehicle worth a look?)
                                                        │
                                                        ▼
                          crop gate ◄──────────── plate detection
                     (is this crop new evidence?)        │
                              │                          │
                              ▼                          ▼
                      preprocessing ──► OCR ──► multi-frame consensus
                                                        │
                                                        ▼
                                            track merge (one vehicle)
                                                        │
                                                        ▼
                                              structured event
```

Two gates sit between tracking and inference, and they are the difference
between a demo and something that could run continuously:

* **The plate scheduler** decides whether a *vehicle* is worth searching. New,
  moved, rescaled, not yet read, or overdue — otherwise skip.
* **The crop gate** decides whether a *plate crop* is worth reading. First look,
  sharper, larger, perceptually different, or the current answer is weak —
  otherwise skip.

Measured on 4K footage with ~20 vehicles per frame, the two together cut plate
detection from 13.3 calls per frame to 1.45 and OCR from 14.2 to 0.7, while
tracking and detection ran unchanged.

Every skip is counted by reason and reported in `summary.json`. That matters:
a pipeline that gets faster by quietly doing less is indistinguishable from one
that got faster by doing less *wastefully*, unless it shows its working.

---

## Batch and stream are the same components

```
ailab run     file ──► Pipeline    ──► results, report, annotated video
ailab stream  RTSP ──► StreamRunner ──► events, continuously
```

Both construct the same detector, tracker, plate detector and OCR engine from
the same config, and share `_find_plates` and `_read_plate`. A measurement taken
in batch mode therefore describes the streaming path too — otherwise the lab
would be optimising something the production worker does not run.

What differs is what a live source demands:

| | batch | stream |
|---|---|---|
| pace | as fast as possible | the camera's, and it does not wait |
| backlog | irrelevant | must not accumulate |
| results | at the end | as they happen |
| memory | grows with the file | bounded by vehicles in view |
| failure | file ends | camera drops; reconnect |

---

## Drop-to-latest, and why

A camera producing 30 fps into a pipeline that manages 3 fps leaves three
options:

* **Block the reader.** The decode buffer fills, the RTSP session stalls, the
  server drops us. Recovery is a reconnect and a gap.
* **Queue everything.** Memory grows, and so does latency. After a minute we are
  confidently reporting where a car was a minute ago — worse than useless for an
  alert.
* **Drop to latest.** Decode continuously, keep the newest frame, hand the
  consumer whatever is current.

The third is right for ANPR: a vehicle is in view for dozens of frames, so
missing some costs a little consensus evidence, while falling behind costs alert
latency — the thing the platform actually sells.

`StreamReader` runs the decode on its own thread and counts every dropped frame.
A worker discarding 90% of its input while reporting healthy throughput is
telling the operator a number nobody asked for.

---

## Events

Two kinds, and the distinction is operational:

| event | when | for |
|---|---|---|
| `vehicle.observed` | while still in view, when the reading changes materially | real-time alerting |
| `vehicle.completed` | after the vehicle leaves, consensus settled | history, cross-camera tracing |

Only `completed` events could not raise an alert in time; only `observed` events
would fill history with half-formed readings. Provisional events are emitted only
when the plate changes or confidence improves by a configured margin, and are
capped per vehicle, so a long-dwelling car cannot flood the stream.

The payload carries camera identity and optional location, vehicle type and
confidence, plate text with confidence and *alternatives*, the evidence trail
(every read, crop references), and capture-to-event latency. Alternatives travel
with the event so the platform can widen a watchlist match rather than being
handed one string to trust.

**What is deliberately absent:** database lookups, watchlist matching, GIS,
alert rules, retention. The AI tier's job ends at "here is what I saw, and how
sure I am". Keeping that line sharp is what lets this be benchmarked and scaled
on its own terms.

---

## One vehicle, one event

A tracker assigns identities to observations and gets it wrong in ways no
parameter tuning removes. Measured on a 240-frame clip, one car produced three
tracks whose lifetimes overlapped, and another produced two whose plates differed
by a single dropped character.

That matters more here than it would elsewhere: cross-camera route
reconstruction joins sightings by plate and time, so one vehicle arriving as
three tracks at one camera becomes three hops that never happened.

Two repairs, at different levels:

1. **Plate ownership resolution**, per frame. Overlapping vehicle boxes meant the
   same plate pixels were found inside several vehicles' crops — 76 cases in one
   clip, at IoU up to 0.91. Each plate region is now kept once and attributed to
   the smallest tracked vehicle containing it.
2. **Track merging**, after tracking. Fragments that resolved the same plate
   (within one character) and are compatible in time become one vehicle, their
   reads pooled and consensus recomputed over the union.

Raw tracks are still written to `tracks.csv`. "The tracker fragmented here" is a
finding worth seeing, not something to hide behind a merged total.

---

## Scaling toward many cameras

**One worker handles one camera.** That is the unit, and it is deliberate: a
worker's capacity and cost are directly measurable rather than entangled with
its neighbours, and a camera that needs more can be given its own without
disturbing the rest.

```
   camera ─┐
   camera ─┼─► worker ─┐
   camera ─┘           │
                       ├─► event stream ─► platform
   camera ─┐           │
   camera ─┼─► worker ─┘
   camera ─┘
```

The properties that make this scale horizontally are already in place:

* **Workers share nothing.** No cross-camera state, no shared tracker, no
  coordination. Adding a worker needs no change to existing ones.
* **The central tier carries events, not video.** An event is ~2 KB; a 4 Mbps
  stream is not. This is the same argument the platform's architecture rests on,
  and the AI tier is where it is enforced — video is consumed at the edge and
  never forwarded.
* **Backpressure is local and bounded.** A worker that falls behind drops frames
  and says so. It does not grow a queue, and it does not push load onto anything
  else.
* **Cameras are addressed by ID, not by position.** Every event carries
  `camera_id`, so which worker produced it is an operational detail rather than
  something the platform must model.

**What is not built, and should not be assumed:** a scheduler that assigns
cameras to workers, health/restart supervision, a real message bus in place of
JSONL, and GPU batching across cameras. Those belong to deployment, and the
platform already has the bus (`EventBus`) this would target.

**Capacity, measured rather than claimed.** One CPU worker on this machine
reaches ~2.8 fps on 4K and ~4.4 fps on 720p in `bench` mode. A camera analysed at
5 fps — ample for ANPR, since a vehicle is in view for dozens of frames — puts
roughly one 720p camera per worker on this hardware. **Reaching 80,000 cameras is
therefore a question about GPUs and node count, and this machine cannot answer
it.** No GPU figure appears here because none has been measured.

---

## Configuration ladder

| mode | for |
|---|---|
| `diagnostic` | study one clip: every observation, no early exits |
| `accurate` | find the ceiling: large inputs, near-continuous search |
| `default` | balanced batch processing |
| `fast` | sweep long footage |
| `bench` | measure inference alone, no diagnostic output |

Diagnostic output costs ~3.2x the inference it observes at 4K. That is a fair
price for a lab and an unacceptable one for a worker, which is why the two are
separable rather than compromised into one setting.
