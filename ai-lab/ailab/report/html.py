"""Self-contained HTML run report.

Crops are embedded as data URIs and the CSS is inline, so the file can be copied
off the machine, emailed, or opened on a laptop with no network and still show
every plate image. That property is the whole reason this is a single file
rather than a directory of assets.

The report leads with what went wrong. A page that opens with "94% of plates
resolved" invites you to stop reading; one that opens with the twelve vehicles
whose plates disagreed across frames is the one that improves the models.
"""

from __future__ import annotations

import base64
import html
import json
from pathlib import Path
from typing import Any

from jinja2 import Template

from ailab.logging import get_logger
from ailab.report.stats import vehicle_row
from ailab.types import Track

log = get_logger(__name__)

# Crops are small, but a busy junction produces thousands. Cap what gets
# inlined so the report stays openable in a browser.
MAX_EMBEDDED_BYTES = 48 * 1024 * 1024


def _data_uri(path: Path) -> str:
    try:
        raw = path.read_bytes()
    except OSError:
        return ""
    return "data:image/jpeg;base64," + base64.b64encode(raw).decode("ascii")


TEMPLATE = Template(
    """<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{{ run_name }} — AI Lab run</title>
<style>
  :root {
    --bg:#0b1020; --card:#141a2e; --card2:#1a2138; --fg:#e8ecf5; --muted:#8b97b3;
    --line:#26304d; --ok:#2ecc71; --warn:#f1c40f; --bad:#e74c3c; --accent:#4aa8ff;
  }
  * { box-sizing:border-box; }
  body { margin:0; background:var(--bg); color:var(--fg);
         font:14px/1.55 -apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif; }
  .wrap { max-width:1400px; margin:0 auto; padding:28px 20px 80px; }
  h1 { font-size:24px; margin:0 0 4px; }
  h2 { font-size:17px; margin:34px 0 12px; padding-bottom:7px;
       border-bottom:1px solid var(--line); }
  .sub { color:var(--muted); font-size:13px; margin-bottom:6px; }
  code, .mono { font-family:'SF Mono',Menlo,Consolas,monospace; }

  .kpis { display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:12px; margin-top:18px; }
  .kpi { background:var(--card); border:1px solid var(--line); border-radius:10px; padding:14px 16px; }
  .kpi .v { font-size:25px; font-weight:600; letter-spacing:-0.5px; }
  .kpi .l { color:var(--muted); font-size:11px; text-transform:uppercase; letter-spacing:.7px; margin-top:3px; }
  .kpi.ok .v{color:var(--ok);} .kpi.warn .v{color:var(--warn);} .kpi.bad .v{color:var(--bad);}

  table { width:100%; border-collapse:collapse; background:var(--card);
          border:1px solid var(--line); border-radius:10px; overflow:hidden; font-size:13px; }
  th { background:var(--card2); text-align:left; padding:9px 11px; color:var(--muted);
       font-weight:600; font-size:11px; text-transform:uppercase; letter-spacing:.6px; }
  td { padding:8px 11px; border-top:1px solid var(--line); vertical-align:middle; }
  tr:hover td { background:#182038; }
  .plate { font-family:'SF Mono',Menlo,monospace; font-weight:700; letter-spacing:1.4px; font-size:15px; }
  .thumb { height:34px; border-radius:4px; border:1px solid var(--line); display:block; }
  .vthumb { height:52px; border-radius:5px; border:1px solid var(--line); display:block; }

  .pill { display:inline-block; padding:2px 8px; border-radius:999px; font-size:11px; font-weight:600; }
  .pill.ok { background:rgba(46,204,113,.16); color:var(--ok); }
  .pill.warn { background:rgba(241,196,15,.16); color:var(--warn); }
  .pill.bad { background:rgba(231,76,60,.16); color:var(--bad); }
  .pill.mute { background:rgba(139,151,179,.14); color:var(--muted); }

  .bar { height:6px; background:var(--card2); border-radius:3px; overflow:hidden; min-width:64px; }
  .bar > i { display:block; height:100%; background:var(--accent); }

  details { background:var(--card); border:1px solid var(--line); border-radius:10px;
            padding:10px 14px; margin-top:10px; }
  summary { cursor:pointer; color:var(--muted); font-weight:600; }
  pre { background:#0d1223; padding:12px; border-radius:8px; overflow:auto; }
  .note { background:rgba(74,168,255,.08); border-left:3px solid var(--accent);
          padding:11px 14px; border-radius:0 8px 8px 0; color:#c8d6ee; font-size:13px; margin:14px 0; }
  .warnbox { background:rgba(241,196,15,.08); border-left:3px solid var(--warn); }
  .reads td { font-size:12px; color:var(--muted); padding:4px 9px; }
  .grid2 { display:grid; grid-template-columns:1fr 1fr; gap:20px; }
  @media (max-width:900px) { .grid2 { grid-template-columns:1fr; } }
  .cardbox { background:var(--card); border:1px solid var(--line); border-radius:10px; padding:14px 16px; }
  video { width:100%; border-radius:10px; border:1px solid var(--line); }
</style></head><body><div class="wrap">

<h1>{{ run_name }}</h1>
<div class="sub">
  <span class="mono">{{ source.name }}</span> ·
  {{ source.width }}×{{ source.height }} · {{ '%.1f'|format(source.fps) }} fps ·
  {{ '%.1f'|format(source.duration_s) }}s source ·
  run started {{ started_at }}
</div>
<div class="sub">
  detector <code>{{ components.detector.engine }}</code> ·
  tracker <code>{{ components.tracker.engine if components.tracker else 'n/a' }}</code> ·
  plates <code>{{ components.plate_detector.engine }}</code> ·
  OCR <code>{{ components.ocr.engine }}</code>
</div>

<div class="kpis">
  <div class="kpi"><div class="v">{{ p.frames_processed }}</div><div class="l">frames processed</div></div>
  <div class="kpi"><div class="v">{{ '%.1f'|format(p.processing_fps) }}</div><div class="l">processing fps</div></div>
  <div class="kpi"><div class="v">{{ '%.2f'|format(p.realtime_factor) }}×</div><div class="l">realtime factor</div></div>
  <div class="kpi"><div class="v">{{ t.unique_tracks }}</div><div class="l">unique tracks</div></div>
  <div class="kpi"><div class="v">{{ t.vehicle_tracks }}</div><div class="l">vehicles</div></div>
  <div class="kpi ok"><div class="v">{{ r.plates_resolved }}</div><div class="l">plates resolved</div></div>
  <div class="kpi {{ 'ok' if r.grammar_valid_rate > 0.8 else 'warn' }}">
      <div class="v">{{ r.plates_grammar_valid }}</div><div class="l">grammar valid</div></div>
  <div class="kpi {{ 'warn' if r.plates_ambiguous else '' }}">
      <div class="v">{{ r.plates_ambiguous }}</div><div class="l">ambiguous</div></div>
  <div class="kpi {{ 'bad' if r.vehicles_without_plate > r.plates_resolved else 'warn' }}">
      <div class="v">{{ r.vehicles_without_plate }}</div><div class="l">no plate found</div></div>
  <div class="kpi"><div class="v">{{ pl.ocr_attempts }}</div><div class="l">OCR attempts</div></div>
</div>

<div class="note">
  <strong>Confidence is not accuracy.</strong> Every percentage on this page is what the
  models reported about themselves. To learn what the pipeline actually gets right, supply
  ground truth and run <code>ailab evaluate {{ run_dir }} --ground-truth gt.csv</code>.
</div>

{% if evaluation %}
<h2>Measured accuracy — against ground truth</h2>
<div class="kpis">
  <div class="kpi {{ 'ok' if evaluation.plate_accuracy > 0.8 else 'warn' }}">
    <div class="v">{{ '%.1f'|format(evaluation.plate_accuracy * 100) }}%</div>
    <div class="l">exact plate match</div></div>
  <div class="kpi"><div class="v">{{ '%.3f'|format(evaluation.mean_cer) }}</div>
    <div class="l">character error rate</div></div>
  <div class="kpi"><div class="v">{{ evaluation.matched }}</div><div class="l">matched</div></div>
  <div class="kpi bad"><div class="v">{{ evaluation.wrong }}</div><div class="l">wrong</div></div>
  <div class="kpi bad"><div class="v">{{ evaluation.missed }}</div><div class="l">missed</div></div>
</div>
{% endif %}

{% if annotated_video %}
<h2>Annotated video</h2>
{% if browser_playable %}
<video controls preload="metadata" src="{{ annotated_video }}"></video>
{% else %}
<div class="note warnbox">
  Written as <code>{{ annotated_codec }}</code>, which browsers will not play inline.
  Open it directly: <code>{{ annotated_video }}</code> (VLC or QuickTime).
</div>
{% endif %}
{% endif %}

<h2>Vehicle results — {{ vehicles|length }} tracks</h2>
<div class="sub">Sorted by confidence. Expand a row to see every individual frame read,
including the ones consensus voted down.</div>
<table>
<thead><tr>
  <th>#</th><th>vehicle</th><th>type</th><th>plate</th><th>conf</th><th>reads</th>
  <th>agreement</th><th>seen</th><th>flags</th><th>plate crop</th>
</tr></thead>
<tbody>
{% for v in vehicles %}
<tr>
  <td class="mono">{{ v.row.track_id }}</td>
  <td>{% if v.vehicle_crop %}<img class="vthumb" src="{{ v.vehicle_crop }}" alt="">{% endif %}</td>
  <td>{{ v.row.class_name }}</td>
  <td class="plate">{{ v.row.plate or '—' }}</td>
  <td>
    <div class="bar" title="{{ v.row.plate_confidence }}"><i style="width:{{ (v.row.plate_confidence*100)|round }}%"></i></div>
    <span class="mono" style="font-size:11px">{{ '%.2f'|format(v.row.plate_confidence) }}</span>
  </td>
  <td class="mono">{{ v.row.reads_agreeing }}/{{ v.row.reads_total }}</td>
  <td class="mono">{{ '%.0f'|format(v.row.agreement * 100) }}%</td>
  <td class="mono" style="font-size:11px">{{ '%.1f'|format(v.row.first_seen_s) }}–{{ '%.1f'|format(v.row.last_seen_s) }}s</td>
  <td>
    {% if not v.row.plate %}<span class="pill mute">no plate</span>
    {% else %}
      {% if v.row.grammar_valid %}<span class="pill ok">valid</span>
      {% else %}<span class="pill bad" title="{{ v.row.grammar_note }}">invalid</span>{% endif %}
      {% if v.row.ambiguous %}<span class="pill warn">ambiguous</span>{% endif %}
      {% if v.row.corrected_from %}<span class="pill warn" title="was {{ v.row.corrected_from }}">corrected</span>{% endif %}
    {% endif %}
  </td>
  <td>{% if v.best_plate_crop %}<img class="thumb" src="{{ v.best_plate_crop }}" alt="">{% endif %}</td>
</tr>
{% if v.reads %}
<tr><td colspan="10" style="padding:0 11px 10px 40px;">
  <details><summary>{{ v.reads|length }} individual reads{% if v.row.candidates %} · candidates: {{ v.row.candidates }}{% endif %}</summary>
    <table class="reads" style="margin-top:8px;border:none;background:transparent">
      <thead><tr><th>frame</th><th>t</th><th>read</th><th>ocr</th><th>weight</th>
        <th>variant</th><th>sharpness</th><th>size</th><th>grammar</th><th>crop</th></tr></thead>
      <tbody>
      {% for rd in v.reads %}
        <tr>
          <td class="mono">{{ rd.frame_index }}</td>
          <td class="mono">{{ '%.2f'|format(rd.t_s) }}s</td>
          <td class="mono" style="color:{{ '#e8ecf5' if rd.text == v.row.plate else '#e74c3c' }}">{{ rd.text }}</td>
          <td class="mono">{{ '%.2f'|format(rd.ocr_confidence) }}</td>
          <td class="mono">{{ '%.3f'|format(rd.vote_weight) }}</td>
          <td>{{ rd.preprocess_variant }}</td>
          <td class="mono">{{ '%.0f'|format(rd.sharpness) }}</td>
          <td class="mono">{{ rd.crop_w }}×{{ rd.crop_h }}</td>
          <td>{{ '✓' if rd.grammar_valid else '✗' }}</td>
          <td>{% if rd.crop_uri %}<img class="thumb" style="height:26px" src="{{ rd.crop_uri }}" alt="">{% endif %}</td>
        </tr>
      {% endfor %}
      </tbody>
    </table>
  </details>
</td></tr>
{% endif %}
{% endfor %}
</tbody></table>

<h2>Where the time went</h2>
<table>
<thead><tr><th>stage</th><th>total</th><th>calls</th><th>mean</th><th>share</th><th></th></tr></thead>
<tbody>
{% for stage, s in stages.items() %}
<tr>
  <td><code>{{ stage }}</code></td>
  <td class="mono">{{ '%.2f'|format(s.total_s) }}s</td>
  <td class="mono">{{ s.calls }}</td>
  <td class="mono">{{ '%.2f'|format(s.mean_ms) }} ms</td>
  <td class="mono">{{ s.share_pct }}%</td>
  <td><div class="bar"><i style="width:{{ s.share_pct }}%"></i></div></td>
</tr>
{% endfor %}
</tbody></table>

<div class="grid2" style="margin-top:20px">
  <div class="cardbox">
    <strong>OCR confidence distribution</strong>
    <table style="margin-top:8px;border:none">
      <tbody>
      {% for bucket, count in pl.ocr_confidence_histogram.items() %}
        <tr><td class="mono" style="width:80px">{{ bucket }}</td>
            <td><div class="bar"><i style="width:{{ (count / (ocr_hist_max or 1) * 100)|round }}%"></i></div></td>
            <td class="mono" style="width:44px;text-align:right">{{ count }}</td></tr>
      {% endfor %}
      </tbody>
    </table>
  </div>
  <div class="cardbox">
    <strong>Preprocessing variants that won</strong>
    <div class="sub" style="margin:4px 0 8px">Which conditioning recipe produced the read that was kept.</div>
    <table style="border:none"><tbody>
    {% for variant, count in pl.reads_by_variant.items() %}
      <tr><td>{{ variant }}</td>
          <td class="mono" style="text-align:right">{{ count }}</td>
          <td class="mono" style="text-align:right;color:var(--muted)">
            mean {{ '%.2f'|format(pl.variant_mean_confidence.get(variant, 0)) }}</td></tr>
    {% endfor %}
    </tbody></table>
  </div>
</div>

{% if hard_cases %}
<h2>Hard cases — {{ hard_cases|length }} flagged for review</h2>
<div class="sub">What the pipeline struggled with. These are the examples worth labelling
and fine-tuning on; <code>ailab mine</code> exports them as a training set.</div>
<table>
<thead><tr><th>track</th><th>reason</th><th>detail</th><th>plate</th><th>conf</th><th>crop</th></tr></thead>
<tbody>
{% for h in hard_cases %}
<tr>
  <td class="mono">{{ h.track_id }}</td>
  <td><span class="pill warn">{{ h.reason }}</span></td>
  <td style="color:var(--muted);font-size:12px">{{ h.detail }}</td>
  <td class="plate">{{ h.plate or '—' }}</td>
  <td class="mono">{{ '%.2f'|format(h.confidence) }}</td>
  <td>{% if h.crop_uri %}<img class="thumb" src="{{ h.crop_uri }}" alt="">{% endif %}</td>
</tr>
{% endfor %}
</tbody></table>
{% endif %}

<h2>Run configuration</h2>
<details><summary>Full config, environment and model provenance</summary>
<pre class="mono" style="font-size:12px;color:#c8d6ee">{{ config_json }}</pre>
</details>

<div class="sub" style="margin-top:40px">
  NagarNetra AI Lab · standalone evaluation environment · not connected to the platform.
</div>
</div></body></html>"""
)


def render(
    run_path: Path,
    summary: dict[str, Any],
    manifest: dict[str, Any],
    tracks: dict[int, Track],
    hard_cases: list[dict[str, Any]] | None = None,
    evaluation: dict[str, Any] | None = None,
    annotated_codec: str = "",
) -> Path:
    """Write `report.html` into a run directory."""
    budget = MAX_EMBEDDED_BYTES

    def embed(relative: str | None) -> str:
        nonlocal budget
        if not relative:
            return ""
        path = run_path / relative
        if not path.exists():
            return ""
        size = path.stat().st_size
        if size > budget:
            return ""
        budget -= size
        return _data_uri(path)

    vehicles = []
    for track in sorted(
        tracks.values(),
        key=lambda t: (
            -(t.result.confidence if t.result else 0.0),
            -t.frame_count,
        ),
    ):
        row = vehicle_row(track)
        best_read = max(track.plate_reads, key=lambda r: r.vote_weight, default=None)
        reads = []
        for read in sorted(track.plate_reads, key=lambda r: r.frame_index):
            entry = read.to_row()
            entry["crop_uri"] = embed(read.crop_path)
            reads.append(entry)
        vehicles.append(
            {
                "row": row,
                "reads": reads,
                "vehicle_crop": embed(track.best_crop_path),
                "best_plate_crop": embed(best_read.crop_path) if best_read else "",
            }
        )

    cases = []
    for case in hard_cases or []:
        entry = dict(case)
        entry["crop_uri"] = embed(case.get("crop_path"))
        cases.append(entry)

    plates = summary.get("plates", {})
    histogram = plates.get("ocr_confidence_histogram", {})
    annotated = run_path / "annotated.mp4"

    rendered = TEMPLATE.render(
        run_name=manifest.get("run", {}).get("name", run_path.name),
        run_dir=run_path.name,
        started_at=manifest.get("run", {}).get("started_at", ""),
        source=manifest.get("source", {}),
        components=manifest.get("components", {}),
        p=summary.get("processing", {}),
        t=summary.get("tracking", {}),
        r=summary.get("results", {}),
        pl=plates,
        stages=summary.get("stages", {}),
        ocr_hist_max=max(histogram.values()) if histogram else 1,
        vehicles=vehicles,
        hard_cases=cases,
        evaluation=evaluation,
        annotated_video=annotated.name if annotated.exists() else None,
        annotated_codec=annotated_codec,
        browser_playable=annotated_codec == "avc1",
        config_json=html.escape(json.dumps(manifest, indent=2)),
    )

    target = run_path / "report.html"
    target.write_text(rendered, encoding="utf-8")
    log.info("report written: %s", target)
    return target
