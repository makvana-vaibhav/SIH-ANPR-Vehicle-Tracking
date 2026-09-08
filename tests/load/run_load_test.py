#!/usr/bin/env python3
"""Measures what the platform actually absorbs, and reports it unflattered.

Judge moment 5 claims the architecture scales to 80,000 cameras. The
arithmetic behind it — 2,667 events/s of metadata against 320 Gbps of video —
was sound but unmeasured, and an unmeasured number in an architecture talk is
the one a judge asks about.

This runs the real thing: a generator emits statewide event volume into the
real bus, the real API consumes it into the real Postgres, and the sustained
rate and end-to-end latency come from the API's own counters rather than from
the generator's opinion of how fast it was going.

Three things it deliberately does not do:

* **It does not run inference.** The AI tier's output is replayed; no laptop
  can produce 2,667 events/s of real ANPR. The report says so.
* **It does not tune the target until it passes.** If the platform caps below
  the target, the measured number is reported with the bottleneck named.
* **It does not leave anything behind.** The fleet and its detections are
  tagged and removed, because a demo database full of load fixtures is how
  the watchlist ended up 97% test litter once already.

Stdlib only, so it runs on the host with no install step.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent


def compose(*args: str, capture: bool = True, check: bool = True) -> str:
    result = subprocess.run(
        ["docker", "compose", *args], cwd=ROOT, capture_output=capture, text=True
    )
    if check and result.returncode != 0:
        raise RuntimeError(f"docker compose {' '.join(args)} failed:\n{result.stderr}")
    return result.stdout if capture else ""


def psql(sql: str) -> str:
    return compose("exec", "-T", "postgres", "psql", "-U", "nagarnetra", "-d", "nagarnetra",
                   "-v", "ON_ERROR_STOP=1", "-tAc", sql).strip()


def metrics(base_url: str, timeout: float = 20.0, attempts: int = 3) -> dict:
    """Scrape the API, retrying — a busy API is the normal case here.

    A scrape that times out under load is information, not a failure of the
    run: it means the API is spending its time ingesting. Retrying rather than
    raising is the difference between a measurement and a stack trace, and an
    unretried timeout in the drain loop threw away a completed 150-second run.
    """
    last: Exception | None = None
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(f"{base_url}/metrics", timeout=timeout) as response:
                return json.loads(response.read())
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last = exc
            time.sleep(0.5 * (attempt + 1))
    raise TimeoutError(f"metrics unreachable after {attempts} attempts: {last}")


def ingest_stats(base_url: str) -> dict:
    """Counters for the whole ingest tier, however it happens to be deployed.

    On the base profile the API persists events itself. On the scale profile
    it persists nothing and dedicated workers do, so reading the API's own
    counters would report zero on a platform absorbing thousands of events a
    second. `ingest_fleet` aggregates whichever workers are alive — including
    the API's own consumer when it is one of them.
    """
    payload = metrics(base_url)
    fleet = payload.get("ingest_fleet") or {}
    if fleet.get("workers"):
        return fleet
    return payload["ingest"]


def seed_fleet(count: int) -> int:
    """Create the load fleet, and report how many cameras exist afterwards."""
    sql = (HERE / "fixture.sql").read_text().replace(":count", str(count))
    compose("exec", "-T", "postgres", "psql", "-U", "nagarnetra", "-d", "nagarnetra",
            "-v", "ON_ERROR_STOP=1", "-c", sql)
    return int(psql("SELECT count(*) FROM cameras WHERE 'loadtest' = ANY(tags);"))


def teardown() -> dict[str, int]:
    before = {
        "cameras": int(psql("SELECT count(*) FROM cameras WHERE 'loadtest' = ANY(tags);")),
        "detections": int(psql("SELECT count(*) FROM detections WHERE track_id LIKE 'CAM-LOAD-%';")),
    }
    sql = (HERE / "teardown.sql").read_text()
    compose("exec", "-T", "postgres", "psql", "-U", "nagarnetra", "-d", "nagarnetra",
            "-v", "ON_ERROR_STOP=1", "-c", sql)
    # Reclaim what the run churned. Inserting and deleting 80,000 rows leaves
    # the table and its indexes bloated — three runs took `cameras` to 64 MB
    # for 281 surviving rows — and the next run then measures scans over dead
    # space. VACUUM FULL rather than plain VACUUM because the space should go
    # back to the disk, not just onto the free list.
    compose("exec", "-T", "postgres", "psql", "-U", "nagarnetra", "-d", "nagarnetra",
            "-c", "VACUUM FULL cameras")
    compose("exec", "-T", "postgres", "psql", "-U", "nagarnetra", "-d", "nagarnetra",
            "-c", "ANALYZE cameras")

    remaining = int(psql("SELECT count(*) FROM cameras WHERE 'loadtest' = ANY(tags);"))
    return {**before, "cameras_remaining": remaining}


def stream_lag(stream_key: str, group: str = "nagarnetra-api") -> int:
    """Entries added to the stream that the consumer group has not read.

    The single most useful number in the run. A backlog that grows steadily
    means the generator is outrunning the consumer, and the throughput the
    consumer reports is then its *capacity*, not the offered rate.

    This is the group's `lag`, not `XLEN`. The stream is length-capped, so
    `XLEN` sits at the cap regardless of whether anyone is behind — the first
    version of this used it and reported a permanent 100,000-event backlog on
    a consumer that was entirely caught up.
    """
    out = compose("exec", "-T", "redis", "redis-cli", "XINFO", "GROUPS", stream_key)
    fields = out.split()
    for i, token in enumerate(fields):
        if token == group:
            tail = fields[i:]
            if "lag" in tail:
                value = tail[tail.index("lag") + 1]
                return int(value) if value.isdigit() else 0
    return 0


def abandon_backlog(stream_key: str, group: str = "nagarnetra-api") -> None:
    """Fast-forward the consumer group past anything still queued.

    The measurement is over; the remaining backlog is load-test events with no
    value. Advancing the group's id to `$` tells the consumer to resume from
    live rather than work through tens of thousands of events whose cameras
    are about to be deleted.
    """
    remaining = stream_lag(stream_key)
    if remaining:
        compose("exec", "-T", "redis", "redis-cli", "XGROUP", "SETID", stream_key, group, "$")
        print(f"  abandon        skipped {remaining:,} queued events (measurement complete)")


def run(args: argparse.Namespace) -> dict:
    base = args.api_url

    print(f"  fleet          seeding {args.cameras:,} load cameras")
    fleet = seed_fleet(args.cameras)
    print(f"                 {fleet:,} present")

    # Baseline before the generator starts. Everything measured is a delta
    # against this, so events the live worker produced earlier are excluded.
    baseline = ingest_stats(base)
    depth_before = stream_lag(args.stream)

    print(f"  generator      {args.rate:,.0f} events/s for {args.duration:.0f}s "
          f"across {args.cameras:,} cameras")
    generator = subprocess.Popen(
        ["docker", "compose", "exec", "-T", "simulator",
         "python", "-m", "simulator.load_mode",
         "--cameras", str(args.cameras), "--rate", str(args.rate),
         "--duration", str(args.duration), "--stream", args.stream,
         "--watchlist", args.watchlist],
        cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        env={**__import__("os").environ, "PYTHONPATH": "/app/services/simulator"},
    )

    samples: list[dict] = []
    started = time.monotonic()
    last_consumed = baseline["consumed"]
    # Rates are computed against the time actually elapsed since the last
    # *successful* scrape, not against the nominal interval. Under load the
    # API sometimes cannot answer a scrape in time; dividing a two-interval
    # delta by one interval reported 8,064 events/s on a run doing 3,000.
    last_sampled_at = started
    deadline = started + args.duration

    while time.monotonic() < deadline:
        time.sleep(args.sample_interval)
        try:
            now = ingest_stats(base)
        except (urllib.error.URLError, TimeoutError) as exc:
            # A failed scrape is a finding, not a crash: it means the API is
            # too busy ingesting to answer, which is exactly the kind of thing
            # this test exists to surface.
            print(f"                 metrics scrape failed: {exc}")
            samples.append({"t": round(time.monotonic() - started, 1), "scrape_failed": True})
            continue

        sampled_at = time.monotonic()
        elapsed = sampled_at - started
        window = sampled_at - last_sampled_at
        delta = now["consumed"] - last_consumed
        last_consumed = now["consumed"]
        last_sampled_at = sampled_at
        rate = delta / window if window > 0 else 0.0
        depth = stream_lag(args.stream)
        samples.append({
            "t": round(elapsed, 1),
            "rate": round(rate, 1),
            "window_s": round(window, 1),
            "backlog": depth,
            "persisted": now["persisted"] - baseline["persisted"],
            "failed": now["failed"] - baseline["failed"],
        })
        print(f"    t={elapsed:5.1f}s  ingest {rate:7.0f} ev/s   "
              f"backlog {depth:>8,}   written {now['persisted'] - baseline['persisted']:>9,}")

    stdout, stderr = generator.communicate(timeout=120)
    try:
        generated = json.loads(stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        # A generator that died mid-run is a fact about the run, not a reason
        # to throw away the measurement — the samples up to that point are
        # real. Raising here lost a completed 180-second run and left 80,000
        # fixture cameras behind. Report it and let the reader judge.
        print(f"                 generator exited early (rc={generator.returncode})")
        if stderr.strip():
            print(f"                 {stderr.strip().splitlines()[-1]}")
        emitted = samples[-1]["persisted"] if samples else 0
        generated = {
            "emitted": emitted,
            "elapsed_s": args.duration,
            "target_rate": args.rate,
            "achieved_rate": round(emitted / args.duration, 1) if args.duration else 0.0,
            "generator_kept_up": False,
            "generator_died": True,
            "watchlist_emitted": 0,
        }

    return {"fleet": fleet, "baseline": baseline, "generated": generated,
            "samples": samples, "depth_before": depth_before}


def drain(base: str, stream_key: str, baseline: dict, timeout_s: float) -> dict:
    """Watch the backlog clear after the generator stops.

    This is where consumer *capacity* is measured rather than offered rate.
    While the generator runs, ingest throughput is capped by whichever side is
    slower; once it stops, the consumer is working flat out against a full
    queue and the rate it clears at is its ceiling.
    """
    print("  drain          generator stopped, clearing backlog")
    started = time.monotonic()
    last = ingest_stats(base)["consumed"]
    last_at = started
    rates: list[float] = []

    while time.monotonic() - started < timeout_s:
        time.sleep(2.0)
        depth = stream_lag(stream_key)
        try:
            now = ingest_stats(base)["consumed"]
        except TimeoutError:
            print("    metrics scrape failed, retrying")
            continue
        sampled_at = time.monotonic()
        rate = (now - last) / max(sampled_at - last_at, 0.001)
        last, last_at = now, sampled_at
        if depth > 0:
            rates.append(rate)
        print(f"    backlog {depth:>8,}   clearing at {rate:7.0f} ev/s")
        if depth == 0:
            break

    try:
        final = ingest_stats(base)
    except TimeoutError:
        final = {"consumed": last, "persisted": 0, "failed": 0, "alerts_raised": 0,
                 "latency": {}, "workers": 0}
    return {
        "drain_rates": rates,
        # The peak clearing rate against a real backlog. None when no backlog
        # ever formed — the consumer kept up, so its ceiling was never found,
        # and reporting the idle rate as "capacity" is how the first run of
        # this claimed a capacity of 6 events/s on a consumer doing 212.
        "capacity_ev_s": round(max(rates), 1) if rates else None,
        "cleared": stream_lag(stream_key) == 0,
        "drain_seconds": round(time.monotonic() - started, 1),
        "total_consumed": final["consumed"] - baseline["consumed"],
        "total_persisted": final["persisted"] - baseline["persisted"],
        "total_failed": final["failed"] - baseline["failed"],
        "alerts_raised": final["alerts_raised"] - baseline["alerts_raised"],
        "mean_batch": final.get("mean_batch", 0),
        "workers": final.get("workers", 1),
        "latency": final["latency"],
    }


def summarise(result: dict, drained: dict, args: argparse.Namespace) -> dict:
    live = [s for s in result["samples"] if "rate" in s]
    # Skip the first two samples: the consumer is still filling its camera
    # cache and Postgres its buffers, and a cold start is not a sustained rate.
    steady = live[2:] if len(live) > 3 else live
    rates = sorted(s["rate"] for s in steady)
    median = rates[len(rates) // 2] if rates else 0.0
    backlogs = [s["backlog"] for s in steady]

    return {
        "ran_at": datetime.now(UTC).isoformat(),
        "target_ev_s": args.rate,
        "cameras": result["fleet"],
        "duration_s": args.duration,
        "offered_ev_s": result["generated"]["achieved_rate"],
        "generator_kept_up": result["generated"]["generator_kept_up"],
        "generator_died": result["generated"].get("generator_died", False),
        "ingest_median_ev_s": median,
        "ingest_min_ev_s": min(rates) if rates else 0.0,
        "ingest_max_ev_s": max(rates) if rates else 0.0,
        "capacity_ev_s": drained["capacity_ev_s"],
        "backlog_peak": max(backlogs) if backlogs else 0,
        "backlog_final": backlogs[-1] if backlogs else 0,
        # Judged on throughput against what was offered, not on the backlog
        # alone. The first version of this compared the final backlog to twice
        # the first sample's, and the first sample was already 50,000 deep —
        # so it certified "sustained the target" on a run doing 180 events/s
        # against an offered 2,667. A load test that grades itself generously
        # is worse than none, because it is believed.
        "kept_up": bool(rates) and median >= result["generated"]["achieved_rate"] * 0.95,
        "backlog_grew": bool(backlogs) and backlogs[-1] > backlogs[0] * 1.5 + 1000,
        "cleared_after": drained["cleared"],
        "consumed": drained["total_consumed"],
        "persisted": drained["total_persisted"],
        "failed": drained["total_failed"],
        "alerts_raised": drained["alerts_raised"],
        "mean_batch": drained["mean_batch"],
        "workers": drained["workers"],
        "latency_ms": drained["latency"],
        "samples": result["samples"],
    }


def _capacity(summary: dict) -> str:
    """Capacity is only a measurement when the consumer was actually saturated."""
    value = summary["capacity_ev_s"]
    if value is None:
        return "not found — no backlog formed"
    return f"{value:,.0f} events/s"


def report(summary: dict) -> str:
    """The measured table, written so it can be pasted into the docs as-is."""
    lat = summary["latency_ms"]
    if summary["kept_up"]:
        verdict = "sustained the offered rate"
    else:
        shortfall = summary["offered_ev_s"] / max(summary["ingest_median_ev_s"], 1)
        verdict = (
            f"capped at {summary['ingest_median_ev_s']:,.0f} events/s — "
            f"**{shortfall:.0f}x below the offered rate**"
        )
    return f"""### Measured — {summary['ran_at'][:19].replace('T', ' ')} UTC

Generator replaying AI-tier output; **no inference in this measurement**.

| | |
|---|---|
| Camera identities | {summary['cameras']:,} |
| Target | {summary['target_ev_s']:,.0f} events/s for {summary['duration_s']:.0f}s |
| Offered by the generator | **{summary['offered_ev_s']:,.0f} events/s**{' — generator died mid-run, treat with suspicion' if summary['generator_died'] else ''} |
| Ingest sustained (median) | **{summary['ingest_median_ev_s']:,.0f} events/s** |
| Ingest range | {summary['ingest_min_ev_s']:,.0f} – {summary['ingest_max_ev_s']:,.0f} events/s |
| Consumer capacity, saturated | {_capacity(summary)} |
| Backlog peak / final | {summary['backlog_peak']:,} / {summary['backlog_final']:,}{' — still growing' if summary['backlog_grew'] else ''} |
| Events consumed | {summary['consumed']:,} |
| Detections written | {summary['persisted']:,} |
| Alerts raised under load | {summary['alerts_raised']:,} |
| Failed | {summary['failed']:,} |
| Ingest workers | {summary['workers']} |
| Mean persist batch | {summary['mean_batch']} events |
| Capture→persisted p50 / p95 / p99 | **{lat['p50_ms']} / {lat['p95_ms']} / {lat['p99_ms']} ms** |

The platform **{verdict}**.
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cameras", type=int, default=80_000)
    parser.add_argument("--rate", type=float, default=2667.0)
    parser.add_argument("--duration", type=float, default=120.0)
    parser.add_argument("--api-url", default="http://localhost:8000")
    parser.add_argument("--stream", default="nagarnetra:events:detections")
    parser.add_argument("--watchlist", default="GJ03AB1234")
    parser.add_argument("--sample-interval", type=float, default=5.0)
    parser.add_argument("--drain-timeout", type=float, default=300.0)
    parser.add_argument("--keep", action="store_true",
                        help="leave the load fleet in place (for inspecting afterwards)")
    parser.add_argument("--out", default=str(HERE / "results"))
    args = parser.parse_args()

    print(f"\n\033[1m80,000-camera load test\033[0m — target {args.rate:,.0f} events/s\n")
    try:
        metrics(args.api_url)
    except (urllib.error.URLError, TimeoutError) as exc:
        print(f"API not reachable at {args.api_url}: {exc}\nRun `make up` first.")
        return 2

    try:
        result = run(args)
        drained = drain(args.api_url, args.stream, result["baseline"], args.drain_timeout)
        summary = summarise(result, drained, args)
    finally:
        # Abandon whatever backlog is left before touching the fleet. The
        # consumer keeps draining after the measurement is taken, and deleting
        # its cameras underneath it makes every remaining event fail a foreign
        # key — 1,022 of them, on the first run of this, all of them reported
        # as platform failures when they were the harness tidying up too soon.
        abandon_backlog(args.stream)

        # Always, even on failure. A half-finished run that leaves 80,000
        # fixture cameras on the map is worse than no run at all.
        if not args.keep:
            print("  teardown       removing the load fleet")
            removed = teardown()
            print(f"                 {removed['cameras']:,} cameras, "
                  f"{removed['detections']:,} detections removed "
                  f"({removed['cameras_remaining']} left)")

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = summary["ran_at"][:19].replace(":", "").replace("-", "")
    (out_dir / f"load-{stamp}.json").write_text(json.dumps(summary, indent=2))
    markdown = report(summary)
    (out_dir / "latest.md").write_text(markdown)

    print("\n" + markdown)
    print(f"written to {out_dir}/latest.md")
    return 0 if summary["kept_up"] else 1


if __name__ == "__main__":
    sys.exit(main())
