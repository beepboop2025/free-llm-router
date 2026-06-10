#!/usr/bin/env bash
# Vendor the canonical free_llm_router package into each consuming app.
#
# These apps live in separate repos and one (OperatorOS) builds in Docker with a
# scoped `COPY . .`, so an editable/path install won't survive the image build.
# Vendoring from a single source keeps one source of truth without a private PyPI.
#
# Run this after editing /Users/mrinal/free-llm-router/python/free_llm_router/*.py
set -euo pipefail

PY_SRC="/Users/mrinal/free-llm-router/python/free_llm_router"
TS_SRC="/Users/mrinal/free-llm-router/typescript/free-llm-router"
NODE_SRC="/Users/mrinal/free-llm-router/node/free-llm-router.mjs"

# ── Python backends (importable as `free_llm_router`) ───────────────────────────
PY_TARGETS=(
  "/Users/mrinal/operatoros/backend/free_llm_router"
  "/Users/mrinal/social_scraper/free_llm_router"
  "/Users/mrinal/dev/DragonScope/backend/free_llm_router"
)
for dst in "${PY_TARGETS[@]}"; do
  mkdir -p "$dst"
  rm -f "$dst"/*.py
  cp "$PY_SRC"/*.py "$dst"/
  echo "py  -> $dst"
done

# ── TypeScript (VitalChain, Next.js server-only) ────────────────────────────────
TS_TARGETS=(
  "/Users/mrinal/Documents/vitalchain/src/lib/free-llm-router"
)
for dst in "${TS_TARGETS[@]}"; do
  mkdir -p "$dst"
  cp "$TS_SRC"/*.ts "$dst"/
  echo "ts  -> $dst"
done

# ── Node ESM port (DragonScope Express server) ──────────────────────────────────
NODE_TARGETS=(
  "/Users/mrinal/dev/DragonScope/server/lib/free-llm-router.mjs"
)
for dst in "${NODE_TARGETS[@]}"; do
  cp "$NODE_SRC" "$dst"
  echo "node-> $dst"
done

echo "Done. Synced Python + TS + Node twins."
