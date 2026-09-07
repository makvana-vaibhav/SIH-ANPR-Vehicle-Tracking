"""Command line interface.

    ailab run input.mp4                      process footage end to end
    ailab run input.mp4 --config fast        with a named experiment config
    ailab evaluate <run> --ground-truth gt.csv    measure real accuracy
    ailab compare <run> <run> ...            put configurations side by side
    ailab sweep input.mp4 --configs a,b,c    run several configs on one input
    ailab mine <run>... --out datasets/v1    export hard cases for fine-tuning
    ailab report <run>                       rebuild the HTML report
    ailab doctor                             check models and dependencies
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.table import Table

from ailab import registry
from ailab.artifacts import environment_info, latest_run, runs_root
from ailab.config import REPO_ROOT, RunConfig, parse_override
from ailab.logging import die, get_logger, setup

console = Console()
log = get_logger(__name__)


# ─────────────────────────────────────────────────────────────────────
# run
# ─────────────────────────────────────────────────────────────────────
def cmd_run(args: argparse.Namespace) -> int:
    from ailab.evaluate import evaluate_predictions, groundtruth
    from ailab.evaluate import summarise as summarise_eval
    from ailab.evaluate.metrics import calibration_table, error_analysis, threshold_sweep
    from ailab.mining import hardcases
    from ailab.pipeline import Pipeline
    from ailab.report import html
    from ailab.report.stats import build_summary, write_outputs

    config = RunConfig.load(args.config)

    overrides: dict[str, Any] = {}
    for item in args.set or []:
        key, value = parse_override(item)
        overrides[key] = value
    if args.stride is not None:
        overrides["source.frame_stride"] = args.stride
    if args.limit is not None:
        overrides["source.max_frames"] = args.limit
    if args.no_video:
        overrides["output.annotated_video"] = False
    if args.detector:
        overrides["detector.engine"] = args.detector
    if args.ocr:
        overrides["ocr.engine"] = args.ocr
    if args.tracker:
        overrides["tracker.engine"] = args.tracker
    if overrides:
        try:
            config = config.with_overrides(overrides)
        except KeyError as exc:
            die(str(exc))

    try:
        pipeline = Pipeline(config)
    except (FileNotFoundError, ValueError, registry.ComponentNotFound) as exc:
        die(str(exc))
        return 1

    result = pipeline.run(args.input)

    # Everything below the frame loop used to be untimed, which made the profile
    # a claim about inference rather than about the run. Report generation in
    # particular embeds every crop as base64 and is not cheap.
    t0 = time.perf_counter()
    written = write_outputs(result, config)
    result.timer.add("write_outputs", time.perf_counter() - t0)

    # ── ground truth, when supplied ──
    evaluation: dict[str, Any] | None = None
    comparisons = []
    if args.ground_truth:
        t0 = time.perf_counter()
        truth = groundtruth.load(args.ground_truth)
        comparisons = evaluate_predictions(
            list(_predictions(result)), truth
        )
        evaluation = {
            "ground_truth": truth.to_dict(),
            **summarise_eval(comparisons),
            "calibration": calibration_table(comparisons),
            "threshold_sweep": threshold_sweep(comparisons),
            "errors": error_analysis(comparisons),
            "comparisons": [c.to_row() for c in comparisons],
        }
        result.run_dir.write_json("evaluation.json", evaluation)
        result.run_dir.write_csv("evaluation.csv", [c.to_row() for c in comparisons])
        result.timer.add("evaluate", time.perf_counter() - t0)

    # ── hard cases ──
    t0 = time.perf_counter()
    cases = hardcases.find(result.tracks, config, comparisons or None)
    result.run_dir.write_json(
        "hard_cases/index.json", {"summary": hardcases.summarise(cases), "cases": cases}
    )
    if cases:
        result.run_dir.write_csv("hard_cases/index.csv", cases)
    result.timer.add("hard_cases", time.perf_counter() - t0)

    # ── report ──
    if config.output.html_report:
        t0 = time.perf_counter()
        manifest = json.loads(written["run"].read_text())
        html.render(
            result.run_dir.path,
            result.stats,
            manifest,
            result.tracks,
            hard_cases=cases,
            evaluation=evaluation,
            annotated_codec=result.annotated_codec,
        )
        result.timer.add("html_report", time.perf_counter() - t0)

    # Rewrite the summary now that the output phases have been timed, so
    # summary.json describes the whole run and not just the frame loop.
    result.stats = build_summary(result, config)
    result.run_dir.write_json("summary.json", result.stats)

    _print_run_summary(result, cases, evaluation)
    console.print(f"\n[bold]output:[/bold] {result.run_dir.path}")
    if config.output.html_report:
        console.print(f"[bold]report:[/bold] {result.run_dir.path / 'report.html'}")
    return 0


def _predictions(result: Any) -> list[tuple[int, str, float]]:
    """One prediction per *physical vehicle*, not per raw track.

    Evaluating raw tracks double-counts a vehicle the tracker fragmented: the
    same car appears twice, one instance matches ground truth and the other is
    scored as a spurious detection. That penalises the pipeline for a tracker
    artefact the merge step has already repaired.
    """
    return [
        (v.vehicle_id, v.result.text, v.result.confidence)
        for v in result.vehicles
        if v.result and v.result.text
    ]


def _print_run_summary(
    result: Any, cases: list[dict[str, Any]], evaluation: dict[str, Any] | None
) -> None:
    stats = result.stats
    processing = stats["processing"]
    results = stats["results"]

    table = Table(title="run summary", show_header=False, box=None, padding=(0, 2))
    table.add_column(style="dim")
    table.add_column(style="bold")
    table.add_row("frames processed", f"{processing['frames_processed']}")
    table.add_row(
        "processing speed",
        f"{processing['processing_fps']:.2f} fps "
        f"({processing['realtime_factor']:.2f}x realtime, "
        f"{processing['ms_per_frame']:.0f} ms/frame)",
    )
    table.add_row("unique tracks", f"{stats['tracking']['unique_tracks']}")
    table.add_row("vehicles", f"{stats['tracking']['vehicle_tracks']}")
    table.add_row("plate detections", f"{stats['plates']['plate_detections']}")
    table.add_row("OCR attempts", f"{stats['plates']['ocr_attempts']}")
    table.add_row(
        "plates resolved",
        f"{results['plates_resolved']} of {results['vehicles_tracked']} vehicles "
        f"({results['resolution_rate'] * 100:.0f}%)",
    )
    table.add_row("grammar valid", f"{results['plates_grammar_valid']}")
    table.add_row("ambiguous", f"{results['plates_ambiguous']}")
    table.add_row("hard cases", f"{len(cases)}")
    console.print()
    console.print(table)

    if results["distinct_plates"]:
        console.print("\n[bold]plates read[/bold]")
        plates = Table(box=None, padding=(0, 2))
        plates.add_column("plate", style="bold cyan")
        plates.add_column("conf")
        plates.add_column("reads")
        plates.add_column("agree")
        plates.add_column("flags", style="yellow")
        for track in sorted(
            (t for t in result.tracks.values() if t.result and t.result.text),
            key=lambda t: -t.result.confidence,
        ):
            outcome = track.result
            flags = []
            if not outcome.grammar_valid:
                flags.append("invalid")
            if outcome.ambiguous:
                flags.append("ambiguous")
            if outcome.corrected_from:
                flags.append(f"was {outcome.corrected_from}")
            plates.add_row(
                outcome.text,
                f"{outcome.confidence:.2f}",
                str(outcome.reads_total),
                f"{outcome.agreement * 100:.0f}%",
                ", ".join(flags),
            )
        console.print(plates)

    stage_table = Table(title="\nstage timing (whole run, not just inference)", box=None, padding=(0, 2))
    stage_table.add_column("stage", style="dim")
    stage_table.add_column("calls", justify="right")
    stage_table.add_column("mean", justify="right")
    stage_table.add_column("total", justify="right")
    stage_table.add_column("share", justify="right")
    for stage, values in stats["stages"].items():
        stage_table.add_row(
            stage,
            f"{values['calls']}",
            f"{values['mean_ms']:.1f} ms",
            f"{values['total_s']:.1f} s",
            f"{values['share_pct']:.0f}%",
        )
    console.print(stage_table)

    invocations = stats.get("invocations", {})
    if invocations:
        inv = Table(title="\nmodel invocations", box=None, padding=(0, 2))
        inv.add_column("stage", style="dim")
        inv.add_column("calls", justify="right")
        inv.add_column("per frame", justify="right")
        inv.add_column("total", justify="right")
        for stage, values in invocations.items():
            if not isinstance(values, dict):
                continue
            inv.add_row(
                stage, f"{values['calls']}", f"{values['per_frame']}", f"{values['total_s']:.1f} s"
            )
        console.print(inv)

    schedule = stats.get("plate_schedule", {})
    if schedule.get("considered"):
        console.print(
            f"\n[bold]plate search scheduling[/bold] — searched "
            f"{schedule['searched']} of {schedule['considered']} opportunities "
            f"({schedule['search_rate'] * 100:.0f}%)"
        )
        for reason, count in schedule.get("by_reason", {}).items():
            console.print(f"  {reason:24} {count}")

    if evaluation:
        console.print("\n[bold]measured accuracy[/bold] (against ground truth)")
        accuracy = Table(box=None, padding=(0, 2), show_header=False)
        accuracy.add_column(style="dim")
        accuracy.add_column(style="bold")
        accuracy.add_row("exact plate match", f"{evaluation['plate_accuracy'] * 100:.1f}%")
        accuracy.add_row("end-to-end", f"{evaluation['end_to_end_accuracy'] * 100:.1f}%")
        accuracy.add_row("character error rate", f"{evaluation['mean_cer']:.3f}")
        accuracy.add_row(
            "correct / wrong / missed",
            f"{evaluation['correct']} / {evaluation['wrong']} / {evaluation['missed']}",
        )
        console.print(accuracy)
    else:
        console.print(
            "\n[dim]No ground truth supplied — every number above is model confidence, "
            "not measured accuracy. Use --ground-truth to measure.[/dim]"
        )


# ─────────────────────────────────────────────────────────────────────
# stream
# ─────────────────────────────────────────────────────────────────────
def cmd_stream(args: argparse.Namespace) -> int:
    """Process a live stream (or a file paced like one) and emit events."""
    from ailab.artifacts import runs_root
    from ailab.stream import EventSink, SourceIdentity, StreamRunner

    config = RunConfig.load(args.config)
    overrides: dict[str, Any] = {}
    for item in args.set or []:
        key, value = parse_override(item)
        overrides[key] = value
    if overrides:
        try:
            config = config.with_overrides(overrides)
        except KeyError as exc:
            die(str(exc))

    location = None
    if args.lat is not None and args.lon is not None:
        location = {"lat": args.lat, "lon": args.lon}
    source = SourceIdentity(
        camera_id=args.camera_id, name=args.name or args.camera_id,
        site=args.site, location=location,
    )

    events_path = Path(args.events) if args.events else (runs_root() / f"{args.camera_id}.events.jsonl")
    events_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        runner = StreamRunner(config, source, EventSink(path=events_path, echo=args.echo).open())
    except (FileNotFoundError, ValueError, registry.ComponentNotFound) as exc:
        die(str(exc))
        return 1

    try:
        report = runner.run(
            args.url,
            max_seconds=args.seconds,
            max_frames=args.frames,
            realtime=not args.as_fast_as_possible,
        )
    finally:
        runner.sink.close()

    _print_stream_summary(report, events_path)
    return 0


def _print_stream_summary(report: dict[str, Any], events_path: Path) -> None:
    stream, reader = report["stream"], report["reader"]
    latency = stream["latency_ms"]

    table = Table(title="stream summary", show_header=False, box=None, padding=(0, 2))
    table.add_column(style="dim")
    table.add_column(style="bold")
    table.add_row("camera", str(report["source"].get("camera_id", "")))
    table.add_row("frames processed", f"{stream['frames_processed']}")
    table.add_row("processing rate", f"{stream['processing_fps']:.2f} fps")
    table.add_row(
        "frames decoded / dropped",
        f"{reader['frames_decoded']} / {reader['frames_dropped']} "
        f"({reader['drop_rate'] * 100:.0f}%)",
    )
    table.add_row("decode rate", f"{reader['decode_fps']:.2f} fps")
    table.add_row("tracks started / retired", f"{stream['tracks_started']} / {stream['tracks_retired']}")
    table.add_row("plate reads", f"{stream['plates_read']}")
    table.add_row(
        "events", f"{report['events']['written']} "
        f"({stream['events_observed']} observed, {stream['events_completed']} completed)",
    )
    console.print()
    console.print(table)

    console.print("\n[bold]capture-to-event latency[/bold]")
    lat = Table(box=None, padding=(0, 2), show_header=False)
    lat.add_column(style="dim")
    lat.add_column(style="bold")
    for label, key in (("median", "median"), ("p90", "p90"), ("p99", "p99"), ("max", "max")):
        lat.add_row(label, f"{latency[key]:.0f} ms")
    console.print(lat)

    if reader["drop_rate"] > 0.5:
        console.print(
            f"\n[yellow]Dropping {reader['drop_rate'] * 100:.0f}% of frames.[/yellow] "
            "Inference is slower than the camera. That is the intended behaviour "
            "under load — latency stays bounded — but this camera needs a faster "
            "configuration or its own worker."
        )
    console.print(f"\n[bold]events:[/bold] {events_path}")


# ─────────────────────────────────────────────────────────────────────
# report / evaluate / compare / sweep / mine
# ─────────────────────────────────────────────────────────────────────
def _resolve_run(value: str | None) -> Path:
    if value in (None, "latest"):
        path = latest_run()
        if path is None:
            die("no runs found under runs/. Process something first: ailab run input.mp4")
        return path  # type: ignore[return-value]
    path = Path(value)  # type: ignore[arg-type]
    if not path.exists():
        candidate = runs_root() / value  # type: ignore[operator]
        if candidate.exists():
            return candidate
        die(f"run not found: {value}")
    return path


def cmd_report(args: argparse.Namespace) -> int:
    from ailab.report import html
    from ailab.runstore import LoadedRun

    run = LoadedRun(_resolve_run(args.run))
    evaluation_path = run.path / "evaluation.json"
    evaluation = json.loads(evaluation_path.read_text()) if evaluation_path.exists() else None
    hard = run.hard_case_index()
    cases = hard.get("cases", []) if isinstance(hard, dict) else hard

    html.render(
        run.path, run.summary, run.manifest, run.tracks,
        hard_cases=cases, evaluation=evaluation, annotated_codec=run.annotated_codec,
    )
    console.print(f"report: {run.path / 'report.html'}")
    return 0


def cmd_evaluate(args: argparse.Namespace) -> int:
    from ailab.evaluate import evaluate_predictions, groundtruth
    from ailab.evaluate import summarise as summarise_eval
    from ailab.evaluate.metrics import calibration_table, error_analysis, threshold_sweep
    from ailab.runstore import LoadedRun

    run = LoadedRun(_resolve_run(args.run))
    truth = groundtruth.load(args.ground_truth)
    comparisons = evaluate_predictions(run.predictions(), truth)
    summary = summarise_eval(comparisons)

    payload = {
        "run": run.path.name,
        "ground_truth": truth.to_dict(),
        **summary,
        "calibration": calibration_table(comparisons),
        "threshold_sweep": threshold_sweep(comparisons),
        "errors": error_analysis(comparisons),
        "comparisons": [c.to_row() for c in comparisons],
    }
    (run.path / "evaluation.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")

    table = Table(title=f"accuracy — {run.name}", box=None, padding=(0, 2), show_header=False)
    table.add_column(style="dim")
    table.add_column(style="bold")
    for label, key, suffix in (
        ("ground-truth plates", "ground_truth_plates", ""),
        ("correct", "correct", ""),
        ("wrong", "wrong", ""),
        ("missed", "missed", ""),
        ("spurious", "spurious", ""),
        ("plate accuracy", "plate_accuracy", "%"),
        ("end-to-end accuracy", "end_to_end_accuracy", "%"),
        ("precision", "precision", "%"),
        ("recall", "recall", "%"),
        ("mean CER", "mean_cer", ""),
    ):
        value = summary[key]
        table.add_row(
            label, f"{value * 100:.1f}%" if suffix == "%" else f"{value}"
        )
    console.print(table)

    calibration = payload["calibration"]
    if calibration:
        console.print(
            "\n[bold]calibration[/bold] — what the pipeline claims vs what it delivers"
        )
        cal = Table(box=None, padding=(0, 2))
        cal.add_column("confidence")
        cal.add_column("n", justify="right")
        cal.add_column("claimed", justify="right")
        cal.add_column("measured", justify="right")
        cal.add_column("gap", justify="right")
        for row in calibration:
            gap = row["calibration_gap"]
            colour = "red" if gap > 0.15 else ("green" if abs(gap) <= 0.07 else "yellow")
            cal.add_row(
                row["bucket"], str(row["count"]),
                f"{row['mean_confidence'] * 100:.0f}%",
                f"{row['measured_accuracy'] * 100:.0f}%",
                f"[{colour}]{gap * 100:+.0f}pp[/{colour}]",
            )
        console.print(cal)

    errors = payload["errors"]
    if errors["top_substitutions"]:
        console.print("\n[bold]most common character errors[/bold] (truth -> read)")
        for pair, count in list(errors["top_substitutions"].items())[:8]:
            console.print(f"  {pair}   x{count}")

    console.print(f"\nwritten: {run.path / 'evaluation.json'}")
    return 0


def cmd_compare(args: argparse.Namespace) -> int:
    from ailab.runstore import LoadedRun

    runs = [LoadedRun(_resolve_run(value)) for value in args.runs]
    table = Table(title="run comparison", box=None, padding=(0, 2))
    table.add_column("run", style="dim")
    for column in (
        "detector", "tracker", "ocr", "fps", "tracks", "vehicles",
        "resolved", "valid", "ambiguous", "accuracy",
    ):
        table.add_column(column, justify="right")

    for run in runs:
        components = run.manifest.get("components", {})
        summary = run.summary
        processing = summary.get("processing", {})
        results = summary.get("results", {})
        tracking = summary.get("tracking", {})

        evaluation_path = run.path / "evaluation.json"
        accuracy = "—"
        if evaluation_path.exists():
            evaluation = json.loads(evaluation_path.read_text())
            accuracy = f"{evaluation.get('plate_accuracy', 0) * 100:.0f}%"

        table.add_row(
            run.name,
            str(components.get("detector", {}).get("engine", "?")),
            str(components.get("tracker", {}).get("engine", "?")),
            str(components.get("ocr", {}).get("engine", "?")),
            f"{processing.get('processing_fps', 0):.1f}",
            str(tracking.get("unique_tracks", 0)),
            str(tracking.get("vehicle_tracks", 0)),
            str(results.get("plates_resolved", 0)),
            str(results.get("plates_grammar_valid", 0)),
            str(results.get("plates_ambiguous", 0)),
            accuracy,
        )
    console.print(table)
    console.print(
        "\n[dim]'accuracy' is blank unless that run was evaluated against ground truth. "
        "Resolved-plate counts are not accuracy — a run can resolve more plates by "
        "resolving them wrongly.[/dim]"
    )
    return 0


def cmd_sweep(args: argparse.Namespace) -> int:
    """Run the same input through several configurations."""
    names = [n.strip() for n in args.configs.split(",") if n.strip()]
    if len(names) < 2:
        die("--configs needs at least two comma-separated config names")

    produced: list[str] = []
    for name in names:
        console.rule(f"[bold]{name}")
        sweep_args = argparse.Namespace(
            input=args.input, config=name, set=args.set, stride=args.stride,
            limit=args.limit, no_video=args.no_video, ground_truth=args.ground_truth,
            detector=None, ocr=None, tracker=None,
        )
        code = cmd_run(sweep_args)
        if code != 0:
            return code
        newest = latest_run()
        if newest:
            produced.append(str(newest))

    if len(produced) > 1:
        console.rule("[bold]comparison")
        return cmd_compare(argparse.Namespace(runs=produced))
    return 0


def cmd_mine(args: argparse.Namespace) -> int:
    from ailab.mining import DatasetExporter
    from ailab.runstore import LoadedRun

    output = Path(args.out) if args.out else (REPO_ROOT / "datasets" / "mined")
    exporter = DatasetExporter(output)

    for value in args.runs:
        run = LoadedRun(_resolve_run(value))
        hard = run.hard_case_index()
        cases = hard.get("cases", []) if isinstance(hard, dict) else hard
        hard_ids = {c["track_id"] for c in cases if "track_id" in c}
        exporter.add_run(run.path, run.tracks, hard_ids)
        console.print(f"  harvested {run.name}: {len(run.tracks)} tracks, {len(cases)} hard cases")

    manifest = exporter.finalise()
    table = Table(title="dataset export", box=None, padding=(0, 2), show_header=False)
    table.add_column(style="dim")
    table.add_column(style="bold")
    table.add_row("plate detection (YOLO)", str(manifest["counts"]["detection"]))
    table.add_row("plate OCR (auto-labelled)", str(manifest["counts"]["ocr"]))
    table.add_row("awaiting human labels", str(manifest["counts"]["to_label"]))
    table.add_row("output", str(output))
    console.print(table)
    console.print(
        f"\n[yellow]{manifest['warning']}[/yellow]\n"
        f"Fill in [bold]{output / 'to_label' / 'labels_to_fill.csv'}[/bold] "
        "before using those crops for training."
    )
    return 0


# ─────────────────────────────────────────────────────────────────────
# doctor / models
# ─────────────────────────────────────────────────────────────────────
def cmd_doctor(args: argparse.Namespace) -> int:
    info = environment_info()
    table = Table(title="environment", box=None, padding=(0, 2), show_header=False)
    table.add_column(style="dim")
    table.add_column()
    for key in ("python", "platform", "machine", "cpu_count", "opencv", "numpy", "onnxruntime", "torch"):
        if key in info:
            table.add_row(key, str(info[key]))
    if "onnxruntime_providers" in info:
        table.add_row("providers", ", ".join(info["onnxruntime_providers"]))
    console.print(table)

    console.print("\n[bold]components[/bold]")
    components = Table(box=None, padding=(0, 2))
    components.add_column("kind", style="dim")
    components.add_column("name")
    components.add_column("status")
    for kind in registry.kinds():
        for name in registry.available(kind):
            ok, note = registry.probe(kind, name)
            components.add_row(
                kind, name,
                "[green]available[/green]" if ok else f"[yellow]unavailable[/yellow] ({note})",
            )
    console.print(components)

    console.print("\n[bold]models[/bold]")
    models = Table(box=None, padding=(0, 2))
    models.add_column("file", style="dim")
    models.add_column("size", justify="right")
    models.add_column("status")
    model_dir = REPO_ROOT / "models"
    expected = ["yolov8n.onnx", "plate_yolo11n.onnx"]
    for name in expected:
        path = model_dir / name
        if path.exists():
            models.add_row(name, f"{path.stat().st_size / 1e6:.1f} MB", "[green]present[/green]")
        else:
            models.add_row(name, "—", "[red]missing[/red] — run ./scripts/fetch_models.sh")
    for path in sorted(model_dir.glob("*.onnx")):
        if path.name not in expected:
            models.add_row(path.name, f"{path.stat().st_size / 1e6:.1f} MB", "[dim]extra[/dim]")
    console.print(models)

    missing = [n for n in expected if not (model_dir / n).exists()]
    if missing:
        console.print(
            f"\n[yellow]{len(missing)} model(s) missing.[/yellow] "
            "Run [bold]./scripts/fetch_models.sh[/bold] before processing footage."
        )
        return 1
    console.print("\n[green]ready[/green] — try: ailab run ../data/videos/road_cctv.mp4")
    return 0


def cmd_models(args: argparse.Namespace) -> int:
    for kind in registry.kinds():
        console.print(f"[bold]{kind}[/bold]: {', '.join(registry.available(kind))}")
    console.print("\n[dim]Select with: --set detector.engine=<name> (or the shorthand flags)[/dim]")
    return 0


# ─────────────────────────────────────────────────────────────────────
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ailab",
        description="NagarNetra AI lab — CCTV vehicle and licence-plate pipeline, "
                    "built to be measured rather than trusted.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run = subparsers.add_parser("run", help="process a video, image directory or RTSP URL")
    run.add_argument("input", help="input.mp4, a directory of images, or an rtsp:// URL")
    run.add_argument("-c", "--config", help="config file or name under configs/")
    run.add_argument("--set", action="append", metavar="KEY=VALUE",
                     help="override any config value, e.g. --set detector.confidence=0.4")
    run.add_argument("--stride", type=int, help="analyse every Nth frame")
    run.add_argument("--limit", type=int, help="stop after N analysed frames")
    run.add_argument("--no-video", action="store_true", help="skip the annotated video")
    run.add_argument("--ground-truth", help="CSV of known plates; enables accuracy measurement")
    run.add_argument("--detector", help="shorthand for --set detector.engine=")
    run.add_argument("--ocr", help="shorthand for --set ocr.engine=")
    run.add_argument("--tracker", help="shorthand for --set tracker.engine=")
    run.set_defaults(func=cmd_run)

    stream = subparsers.add_parser(
        "stream", help="process a live RTSP stream (or a file paced like one)"
    )
    stream.add_argument("url", help="rtsp:// URL, or a video file to pace in real time")
    stream.add_argument("--camera-id", required=True, help="identifies this camera in every event")
    stream.add_argument("--name", help="human-readable camera name")
    stream.add_argument("--site", default="", help="site or junction name")
    stream.add_argument("--lat", type=float, help="camera latitude, passed through to events")
    stream.add_argument("--lon", type=float, help="camera longitude")
    stream.add_argument("-c", "--config", help="config file or name under configs/")
    stream.add_argument("--set", action="append", metavar="KEY=VALUE")
    stream.add_argument("--events", help="event JSONL path (default runs/<camera-id>.events.jsonl)")
    stream.add_argument("--echo", action="store_true", help="also print events to stdout")
    stream.add_argument("--seconds", type=float, help="stop after N seconds")
    stream.add_argument("--frames", type=int, help="stop after N processed frames")
    stream.add_argument(
        "--as-fast-as-possible", action="store_true",
        help="do not pace a file at its native rate (throughput test, not a latency test)",
    )
    stream.set_defaults(func=cmd_stream)

    report = subparsers.add_parser("report", help="rebuild the HTML report for a run")
    report.add_argument("run", nargs="?", default="latest")
    report.set_defaults(func=cmd_report)

    evaluate = subparsers.add_parser("evaluate", help="measure a run against ground truth")
    evaluate.add_argument("run", nargs="?", default="latest")
    evaluate.add_argument("-g", "--ground-truth", required=True)
    evaluate.set_defaults(func=cmd_evaluate)

    compare = subparsers.add_parser("compare", help="put runs side by side")
    compare.add_argument("runs", nargs="+")
    compare.set_defaults(func=cmd_compare)

    sweep = subparsers.add_parser("sweep", help="run one input through several configs")
    sweep.add_argument("input")
    sweep.add_argument("--configs", required=True, help="comma-separated config names")
    sweep.add_argument("--set", action="append", metavar="KEY=VALUE")
    sweep.add_argument("--stride", type=int)
    sweep.add_argument("--limit", type=int)
    sweep.add_argument("--no-video", action="store_true")
    sweep.add_argument("--ground-truth")
    sweep.set_defaults(func=cmd_sweep)

    mine = subparsers.add_parser("mine", help="export hard cases as a fine-tuning dataset")
    mine.add_argument("runs", nargs="+")
    mine.add_argument("-o", "--out", help="output directory (default datasets/mined)")
    mine.set_defaults(func=cmd_mine)

    doctor = subparsers.add_parser("doctor", help="check dependencies, engines and model files")
    doctor.set_defaults(func=cmd_doctor)

    models = subparsers.add_parser("engines", help="list available pipeline components")
    models.set_defaults(func=cmd_models)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    setup(verbose=getattr(args, "verbose", False))
    try:
        return int(args.func(args))
    except KeyboardInterrupt:
        console.print("\n[yellow]interrupted[/yellow]")
        return 130
    except FileNotFoundError as exc:
        die(str(exc))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
