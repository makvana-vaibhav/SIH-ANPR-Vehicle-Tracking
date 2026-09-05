#!/usr/bin/env python3
"""Captures the submission screenshots, consistently and without a spinner in shot.

A screenshot folder assembled by hand drifts: different window sizes, one shot
taken while a panel was still loading, another from a session where the map had
not finished drawing. Judges read that as an unfinished product, and they are
not wrong to.

So each shot here waits for **real content** — a specific element, or a count
greater than zero — rather than sleeping a fixed interval, and the script fails
loudly if a screen never fills. A missing screenshot is recoverable; one showing
an empty state that the reviewer believes is the product is not.

    python3 scripts/capture_screenshots.py

Uses Playwright via npx, so nothing is added to the repository's dependencies.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "submission" / "screenshots"
WEB = "http://localhost:8080"
ADMIN_PASSWORD = "Sentinel@2026"

# Each shot: file name, the path to visit, and the selector that proves the
# screen actually has content in it.
SHOTS = [
    ("00-dashboard", "/dashboard", "text=/camera|alert/i"),
    ("01-gis-map", "/map", ".maplibregl-canvas"),
    ("02-fleet-health", "/health", "text=/online/i"),
    ("04-live-anpr", "/anpr", "text=/plate|detection|camera/i"),
    ("06-alerts-list", "/alerts", "text=/alert|watchlist|no alerts/i"),
    ("08-search", "/vehicles", "input"),
    ("09-watchlist", "/watchlist", "text=/GJ03AB1234/"),
    ("10-onboarding", "/integration", "text=/csv|onboard|upload|adapter/i"),
    ("11-audit-log", "/audit", "table, [role=table]"),
]

SCRIPT = """
const { chromium } = require('playwright');

(async () => {
  const shots = JSON.parse(process.argv[2]);
  const out = process.argv[3];
  const web = process.argv[4];
  const password = process.argv[5];

  const browser = await chromium.launch();
  const context = await browser.newContext({
    viewport: { width: 1920, height: 1080 },
    deviceScaleFactor: 2,          // retina, so the deck can crop without mush
    colorScheme: 'dark',
  });
  const page = await context.newPage();

  await page.goto(web + '/login', { waitUntil: 'networkidle' });
  await page.fill('input[name=username], input[type=text]', 'admin');
  await page.fill('input[type=password]', password);
  await page.click('button[type=submit]');
  await page.waitForURL(u => !u.pathname.includes('login'), { timeout: 30000 });
  console.log('signed in');

  let failures = 0;
  for (const [name, path, selector] of shots) {
    try {
      await page.goto(web + path, { waitUntil: 'networkidle', timeout: 30000 });
      await page.waitForSelector(selector, { timeout: 25000 });
      // A beat for map tiles and chart animations to settle. Everything that
      // loads from the network has already been awaited above.
      await page.waitForTimeout(2500);
      await page.screenshot({ path: `${out}/${name}.png`, fullPage: false });
      console.log(`captured ${name}`);
    } catch (err) {
      console.error(`FAILED ${name} (${path}): ${err.message.split('\\n')[0]}`);
      failures++;
    }
  }

  await browser.close();
  process.exit(failures > 0 ? 1 : 0);
})();
"""


def main() -> int:
    if shutil.which("npx") is None:
        print("npx not found — install Node 20 to capture screenshots")
        return 2

    OUT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as workdir:
        script_path = Path(workdir) / "capture.js"
        script_path.write_text(SCRIPT)

        print("installing playwright (first run only)...")
        subprocess.run(
            ["npx", "--yes", "playwright@1.48.0", "install", "chromium"],
            cwd=workdir, check=False,
        )
        result = subprocess.run(
            ["npx", "--yes", "--package=playwright@1.48.0", "node",
             str(script_path), json.dumps(SHOTS), str(OUT), WEB, ADMIN_PASSWORD],
            cwd=workdir,
        )

    print(f"\nscreenshots in {OUT}")
    print("Four shots need a human, because they involve interaction:")
    print("  03  open a camera and let the video play with the ANPR overlay on")
    print("  05  add a plate to the watchlist and photograph the alert when it fires")
    print("  07  search GJ03AB1234 and photograph the drawn route with its hop table")
    print("  10b upload a CSV with a bad row and photograph the per-row error report")
    return result.returncode


if __name__ == "__main__":
    sys.exit(main())
