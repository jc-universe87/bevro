#!/usr/bin/env sh
# Copies the supplied brand assets from the untouched source package into the
# places the web app serves them from. Run after the brand package changes.
# Never edit the copies; edit nothing in brand/bevro-brand either - it is the
# supplied source of truth.
set -eu
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC="$ROOT/brand/bevro-brand"
cp "$SRC"/logo/*.svg          "$ROOT/web/public/brand/logo/"
cp "$SRC"/icon/*              "$ROOT/web/public/icon/"
cp "$SRC"/tokens/bevro-tokens.css   "$ROOT/web/src/styles/bevro-tokens.css"
cp "$SRC"/tokens/tailwind.preset.js "$ROOT/web/bevro.tailwind.preset.cjs"
echo "brand assets synced"
