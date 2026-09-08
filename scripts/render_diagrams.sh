#!/usr/bin/env bash
# Renders the submission diagrams to PNG.
#
# The SVGs are the source — text stays editable and they scale without loss.
# But submission forms and slide tools handle PNG more reliably than SVG, and
# a diagram that fails to render in the reviewer's browser is a diagram that
# was not submitted. So both formats ship, and this regenerates the PNGs
# whenever the SVGs change.
set -euo pipefail
cd "$(dirname "$0")/.."

CHROME="${CHROME:-/Applications/Google Chrome.app/Contents/MacOS/Google Chrome}"
if [ ! -x "$CHROME" ]; then
    echo "Chrome not found at $CHROME — set CHROME=/path/to/chrome" >&2
    exit 1
fi

render() {
    "$CHROME" --headless --disable-gpu --force-device-scale-factor=2 \
        --hide-scrollbars --window-size="$2","$3" \
        --screenshot="$PWD/docs/submission/$1.png" \
        "file://$PWD/docs/submission/$1.svg" 2>/dev/null
    printf '  %-14s → docs/submission/%s.png\n' "$1.svg" "$1"
}

render architecture 1400 990
render workflow 1400 1020
echo "done — 2x scale, ready for slides"
