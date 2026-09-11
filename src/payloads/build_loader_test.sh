#!/usr/bin/env bash
# Build the Stage-3 loader-pipeline proof (AutoRun.txt + VSHL.BIN).
# Puts our compiled payload in VSHL.BIN; the AutoRun reads it into RAM and
# branches to entry, which writes a marker word so the shell can confirm it ran.
set -e
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
SH="$ROOT/reference/fpSup/fp_usb_shell"
"$ROOT/.venv/bin/python" "$SH/build_autorun.py" --loader \
  --payload "$ROOT/src/payloads/payload_marker.S" --entry entry \
  --payload-addr 0xC072E400 --banner fpLAB-load \
  --out "$ROOT/autoruns/loader-test/AutoRun.txt"
echo "wrote autoruns/loader-test/{AutoRun.txt,VSHL.BIN}; marker 0x510AADED @ 0xC072FA00"
