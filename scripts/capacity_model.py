#!/usr/bin/env python3
"""Size and cost a NagarNetra deployment for an arbitrary camera fleet.

    python3 scripts/capacity_model.py --cameras 100000

## Why this is a script and not a table in a document

`docs/INFRASTRUCTURE.md` and `docs/BRIEFING.md` both carry sizing tables, and
both were written for one fleet size (80,000). A judge asks "what about a
lakh?", or "what about just Mumbai?", and a static table cannot answer. Worse,
the two documents can drift apart, and did.

Every figure here is derived from a declared constant, and every constant
carries its provenance: **MEASURED** ones name the run that produced them,
**ASSUMED** ones name the reasoning. `--provenance` prints them all. That is
the CLAUDE.md §5 rule — computed, never invented — applied to the one part of
the project that is inherently a projection.

## What is genuinely measured

Only two things, and they are worth separating from everything else:

* **Inference throughput.** 4.39 fps, 720p, full pipeline, 4 ONNX threads,
  CPU-only (ai-lab/PERFORMANCE.md §"Accuracy — synthetic footage").
* **Central ingest ceiling.** 2,774 events/s sustained, 0 failures, across
  80,000 camera identities into the real Postgres (docs/HLD.md §7). p95
  capture-to-persisted was 5.7 s against a 3 s target — the throughput passed,
  the latency did not, and that is reported rather than tuned away.

Everything else — bitrates, event sizes, crop sizes, retention, unit prices —
is an assumption you should challenge and can override.

## The correction this script encodes

`docs/INFRASTRUCTURE.md` §2 states "one core sustains roughly 8 cameras" and
sizes 80,000 cameras at ~10,000 cores. That divides the fleet by 8.78 cameras
but forgets those 8.78 cameras were served by a **4-thread** worker. The real
figure is 8.78 cameras per 4 cores — about **2.2 cameras per core**, so the
published sizing is optimistic by roughly 4x. This script uses the per-worker
number and multiplies by the worker's core count, which is why its core counts
are ~4x that document's. The document is wrong; see the note in `--provenance`.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from typing import Any

# ── Constants, with provenance ────────────────────────────────────────
# kind: "measured" carries a source; "assumed" carries a rationale.


@dataclass(frozen=True)
class Constant:
    value: float
    unit: str
    kind: str
    source: str


C: dict[str, Constant] = {
    # ── Measured ──
    "pipeline_fps": Constant(
        4.39,
        "analysed frames/s per worker",
        "measured",
        "ai-lab/PERFORMANCE.md — 720p, full pipeline, 4 ONNX threads, CPU-only",
    ),
    "worker_threads": Constant(
        4,
        "cores per worker",
        "measured",
        "AILAB_ORT_THREADS=4 in docker-compose.yml; 126 ms/call at 4 threads "
        "vs 452 ms at the library default, so 4 is the measured optimum",
    ),
    "ingest_ceiling_eps": Constant(
        2774,
        "events/s per central stack",
        "measured",
        "docs/HLD.md §7 load test — sustained median, 0 failures, 388,713 rows "
        "written; p95 latency 5.7 s missed its 3 s target",
    ),
    # ── Assumed: workload ──
    "analysed_fps_per_camera": Constant(
        0.5,
        "analysed frames/s per camera",
        "assumed",
        "one frame every 2 s — enough to catch a vehicle crossing a junction. "
        "The single most load-bearing assumption here: halve it and compute "
        "halves. Should be validated against measured junction transit times",
    ),
    "events_per_camera_per_s": Constant(
        1 / 30,
        "events/s per camera",
        "assumed",
        "one vehicle event per camera per 30 s, averaged over a day. Peak-hour "
        "arterial cameras will exceed this by several times",
    ),
    "event_bytes": Constant(
        2048,
        "bytes",
        "assumed",
        "a detection row with plate, bbox, OCR evidence and ids",
    ),
    "video_bitrate_mbps": Constant(
        4.0,
        "Mbps per camera",
        "assumed",
        "H.264, 1080p, 15 fps — typical municipal ANPR encoder",
    ),
    "plate_crop_bytes": Constant(
        15360, "bytes", "assumed", "~15 KB JPEG plate crop, one per detection"
    ),
    # ── Assumed: retention ──
    "hot_days": Constant(7, "days", "assumed", "operational window on NVMe"),
    "warm_days": Constant(
        90, "days", "assumed", "investigation window incl. plate crops"
    ),
    "cold_years": Constant(
        5, "years", "assumed", "detections + audit, statutory retention"
    ),
    # ── Assumed: platform shape ──
    "cores_per_node": Constant(
        40, "cores", "assumed", "a commodity 2-socket inference node"
    ),
    "cameras_per_edge_site": Constant(
        2400, "cameras", "assumed", "one municipal zone / district aggregation point"
    ),
    # ── Assumed: unit costs, INR. Indicative, not quotes ──
    # Two node prices, because paying for a GPU and then sizing on CPU
    # throughput charges for hardware the model does not use. The script picks
    # one based on --gpu-speedup.
    "cost_edge_node_cpu": Constant(
        400_000, "INR", "assumed", "40-core, 128 GB, no accelerator"
    ),
    "cost_edge_node_gpu": Constant(
        750_000,
        "INR",
        "assumed",
        "40-core, 128 GB, 1x A2/L4 GPU — docs/BRIEFING.md §14 midpoint",
    ),
    "cost_nvme_per_tb": Constant(
        75_000, "INR/TB", "assumed", "enterprise NVMe, ₹60k for 8 TB"
    ),
    "cost_bulk_storage_per_tb": Constant(
        10_000, "INR/TB", "assumed", "tiered warm/cold, ₹40–60 lakh per 500 TB"
    ),
    "cost_db_node": Constant(
        1_000_000, "INR", "assumed", "32-core, 256 GB, NVMe — ₹25–35 lakh for three"
    ),
    "cost_service_node": Constant(400_000, "INR", "assumed", "API / bus / search node"),
    "cost_uplink_per_site_year": Constant(
        150_000, "INR/site/year", "assumed", "100 Mbps business fibre"
    ),
    "cost_power_per_node_year": Constant(
        60_000, "INR/node/year", "assumed", "~600 W average, plus cooling, at ~₹8/kWh"
    ),
    "cost_ops_engineer_year": Constant(
        1_200_000, "INR/person/year", "assumed", "loaded cost"
    ),
    "dr_fraction": Constant(
        0.6,
        "fraction",
        "assumed",
        "DR site at ~60% of the CENTRAL tier only. Edge inference is already "
        "geographically distributed across sites and degrades per-site rather "
        "than failing together, so duplicating it for DR buys nothing",
    ),
}


def v(name: str) -> float:
    return C[name].value


# ── Model ─────────────────────────────────────────────────────────────
def compute(cameras: int, gpu_speedup: float, ops_ratio: int) -> dict[str, Any]:
    """Derive resources and cost for a fleet of `cameras`."""
    # ── Inference compute ──
    # Cameras one worker sustains, then cores from the worker's thread count.
    # Going via the worker rather than via a per-core figure is the whole
    # point: the measurement was taken on a 4-thread worker.
    cameras_per_worker = v("pipeline_fps") / v("analysed_fps_per_camera") * gpu_speedup
    workers = cameras / cameras_per_worker
    analysis_cores = workers * v("worker_threads")
    analysis_nodes = analysis_cores / v("cores_per_node")

    # ── Event tier ──
    events_per_s = cameras * v("events_per_camera_per_s")
    event_mbps = events_per_s * v("event_bytes") * 8 / 1e6
    ingest_stacks = events_per_s / v("ingest_ceiling_eps")

    # ── Bandwidth: the federation argument ──
    centralised_video_gbps = cameras * v("video_bitrate_mbps") / 1000
    reduction = (centralised_video_gbps * 1000) / event_mbps if event_mbps else 0.0

    # ── Storage ──
    detections_per_day = events_per_s * 86_400
    det_bytes_day = detections_per_day * v("event_bytes")
    crop_bytes_day = detections_per_day * v("plate_crop_bytes")

    hot_tb = det_bytes_day * v("hot_days") / 1e12
    warm_tb = (det_bytes_day + crop_bytes_day) * v("warm_days") / 1e12
    cold_tb = det_bytes_day * 365 * v("cold_years") / 1e12

    # ── Sites and central tier ──
    edge_sites = max(1, round(cameras / v("cameras_per_edge_site")))
    # Three DB nodes is the floor (quorum); grows with ingest stacks.
    db_nodes = max(3, round(3 * ingest_stacks))
    service_nodes = max(3, round(6 * ingest_stacks))

    # ── Capex ──
    node_price = (
        v("cost_edge_node_gpu") if gpu_speedup > 1.0 else v("cost_edge_node_cpu")
    )
    capex_edge = analysis_nodes * node_price
    capex_hot = hot_tb * v("cost_nvme_per_tb")
    capex_bulk = (warm_tb + cold_tb) * v("cost_bulk_storage_per_tb")
    capex_db = db_nodes * v("cost_db_node")
    capex_service = service_nodes * v("cost_service_node")
    # DR mirrors the central tier, not the edge: see dr_fraction's provenance.
    capex_central = capex_hot + capex_bulk + capex_db + capex_service
    capex_dr = capex_central * v("dr_fraction")
    capex_total = capex_edge + capex_central + capex_dr

    # ── Opex, per year ──
    total_nodes = analysis_nodes + db_nodes + service_nodes
    opex_uplinks = edge_sites * v("cost_uplink_per_site_year")
    opex_power = total_nodes * v("cost_power_per_node_year")
    opex_staff = max(2.0, total_nodes / ops_ratio) * v("cost_ops_engineer_year")
    opex_total = opex_uplinks + opex_power + opex_staff

    return {
        "cameras": cameras,
        "gpu_speedup": gpu_speedup,
        "compute": {
            "cameras_per_worker": cameras_per_worker,
            "workers": workers,
            "analysis_cores": analysis_cores,
            "analysis_nodes": analysis_nodes,
        },
        "events": {
            "events_per_s": events_per_s,
            "event_mbps": event_mbps,
            "ingest_stacks": ingest_stacks,
        },
        "bandwidth": {
            "centralised_video_gbps": centralised_video_gbps,
            "federated_event_mbps": event_mbps,
            "reduction_factor": reduction,
        },
        "storage_tb": {"hot": hot_tb, "warm": warm_tb, "cold": cold_tb},
        "sites": {
            "edge_sites": edge_sites,
            "db_nodes": db_nodes,
            "service_nodes": service_nodes,
            "total_nodes": total_nodes,
        },
        "capex_inr": {
            "edge_compute": capex_edge,
            "hot_storage": capex_hot,
            "bulk_storage": capex_bulk,
            "database": capex_db,
            "services": capex_service,
            "dr_site": capex_dr,
            "total": capex_total,
        },
        "opex_inr_year": {
            "uplinks": opex_uplinks,
            "power_cooling": opex_power,
            "staff": opex_staff,
            "total": opex_total,
        },
    }


# ── Presentation ──────────────────────────────────────────────────────
def inr(amount: float) -> str:
    """Indian-convention money: crore above 1e7, lakh above 1e5."""
    if amount >= 1e7:
        return f"₹{amount / 1e7:,.2f} crore"
    if amount >= 1e5:
        return f"₹{amount / 1e5:,.2f} lakh"
    return f"₹{amount:,.0f}"


def report(r: dict[str, Any]) -> None:
    n = r["cameras"]
    mode = (
        "CPU-only (measured)"
        if r["gpu_speedup"] == 1.0
        else (
            f"GPU, {r['gpu_speedup']:.0f}x assumed speedup — EXTRAPOLATED, never executed here"
        )
    )

    print(f"\n{'=' * 68}")
    print(f"  NagarNetra capacity model — {n:,} cameras")
    print(f"  Inference: {mode}")
    print(f"{'=' * 68}")

    c = r["compute"]
    print("\n── Inference compute ──")
    print(f"  cameras per worker            {c['cameras_per_worker']:>12,.1f}")
    print(f"  workers                       {c['workers']:>12,.0f}")
    print(f"  analysis cores                {c['analysis_cores']:>12,.0f}")
    print(
        f"  analysis nodes @ {v('cores_per_node'):.0f} cores   {c['analysis_nodes']:>12,.0f}"
    )

    e = r["events"]
    print("\n── Event tier ──")
    print(f"  events/s                      {e['events_per_s']:>12,.0f}")
    print(f"  metadata bandwidth            {e['event_mbps']:>12,.1f} Mbps")
    print(
        f"  central stacks needed         {e['ingest_stacks']:>12,.2f}  "
        f"(one measured at {v('ingest_ceiling_eps'):,.0f} events/s)"
    )

    b = r["bandwidth"]
    print("\n── Bandwidth: why federation ──")
    print(f"  if video centralised          {b['centralised_video_gbps']:>12,.1f} Gbps")
    print(f"  federated (events only)       {b['federated_event_mbps']:>12,.1f} Mbps")
    print(f"  reduction                     {b['reduction_factor']:>12,.0f}x")

    s = r["storage_tb"]
    print("\n── Storage ──")
    print(f"  hot   ({v('hot_days'):.0f} d, NVMe)          {s['hot']:>12,.1f} TB")
    print(f"  warm  ({v('warm_days'):.0f} d, + crops)      {s['warm']:>12,.1f} TB")
    print(f"  cold  ({v('cold_years'):.0f} y, detections)   {s['cold']:>12,.1f} TB")

    st = r["sites"]
    print("\n── Footprint ──")
    print(f"  edge sites                    {st['edge_sites']:>12,.0f}")
    print(f"  database nodes                {st['db_nodes']:>12,.0f}")
    print(f"  service nodes                 {st['service_nodes']:>12,.0f}")
    print(f"  total nodes                   {st['total_nodes']:>12,.0f}")

    cap = r["capex_inr"]
    print("\n── Capex (indicative, excludes cameras — they already exist) ──")
    for key, label in (
        ("edge_compute", "edge compute"),
        ("hot_storage", "hot storage"),
        ("bulk_storage", "warm + cold storage"),
        ("database", "database cluster"),
        ("services", "API / bus / search"),
        ("dr_site", "DR site"),
    ):
        print(f"  {label:<28}{inr(cap[key]):>22}")
    print(f"  {'TOTAL CAPEX':<28}{inr(cap['total']):>22}")

    op = r["opex_inr_year"]
    print("\n── Opex, per year ──")
    for key, label in (
        ("uplinks", "site uplinks"),
        ("power_cooling", "power and cooling"),
        ("staff", "operations staff"),
    ):
        print(f"  {label:<28}{inr(op[key]):>22}")
    print(f"  {'TOTAL OPEX / YEAR':<28}{inr(op['total']):>22}")

    print(
        f"\n  Cost per camera: capex {inr(cap['total'] / n)} · "
        f"opex {inr(op['total'] / n)}/yr"
    )
    print("\n  All rupee figures are indicative for discussion, not quotes.")
    print(f"{'=' * 68}\n")


def provenance() -> None:
    print(f"\n{'=' * 78}")
    print("  Constants and where they come from")
    print(f"{'=' * 78}")
    for kind in ("measured", "assumed"):
        print(f"\n── {kind.upper()} ──")
        for name, const in C.items():
            if const.kind != kind:
                continue
            print(f"\n  {name} = {const.value:g} {const.unit}")
            print(f"      {const.source}")
    print(f"\n{'=' * 78}")
    print("""
  KNOWN ERROR IN THE COMMITTED DOCS

  docs/INFRASTRUCTURE.md §2 says "one core sustains roughly 8 cameras" and
  sizes 80,000 cameras at ~10,000 cores / ~250 nodes. That took the measured
  8.78 cameras-per-worker figure and treated the worker as a single core. The
  worker used 4 threads, so the honest figure is ~2.2 cameras per core, and
  80,000 cameras needs roughly 36,000 cores — about 4x the published number.

  This script computes from cameras-per-worker x worker-cores, so its output
  supersedes those tables. Fix the documents before quoting either.
""")
    print(f"{'=' * 78}\n")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Size and cost a NagarNetra deployment for a given camera fleet."
    )
    parser.add_argument(
        "--cameras", type=int, default=100_000, help="fleet size (default 100000)"
    )
    parser.add_argument(
        "--gpu-speedup",
        type=float,
        default=1.0,
        help="assumed inference speedup from GPU. Default 1.0 (CPU, the only measured path). "
        "Anything above 1.0 is extrapolation — this repo has never run a GPU path.",
    )
    parser.add_argument(
        "--ops-ratio",
        type=int,
        default=40,
        help="nodes per operations engineer (default 40)",
    )
    parser.add_argument(
        "--compare", action="store_true", help="table across several fleet sizes"
    )
    parser.add_argument(
        "--provenance", action="store_true", help="print every constant's source"
    )
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args()

    if args.provenance:
        provenance()
        return

    if args.compare:
        sizes = (2_400, 10_000, 25_000, 50_000, 80_000, 100_000)
        print(
            f"\n{'cameras':>10} {'cores':>9} {'nodes':>7} {'events/s':>10} "
            f"{'video Gbps':>11} {'storage TB':>11} {'capex':>16} {'opex/yr':>16}"
        )
        print("-" * 96)
        for size in sizes:
            r = compute(size, args.gpu_speedup, args.ops_ratio)
            total_tb = sum(r["storage_tb"].values())
            print(
                f"{size:>10,} {r['compute']['analysis_cores']:>9,.0f} "
                f"{r['compute']['analysis_nodes']:>7,.0f} "
                f"{r['events']['events_per_s']:>10,.0f} "
                f"{r['bandwidth']['centralised_video_gbps']:>11,.0f} "
                f"{total_tb:>11,.0f} {inr(r['capex_inr']['total']):>16} "
                f"{inr(r['opex_inr_year']['total']):>16}"
            )
        print()
        return

    result = compute(args.cameras, args.gpu_speedup, args.ops_ratio)
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        report(result)


if __name__ == "__main__":
    main()
