#!/usr/bin/env python3
"""Read src/fcscale.S's state block over the live USB shell, with names.

The shell answers `mem get` as one hex-encoded blob of its own console output,
which is unreadable by eye, so this decodes it and labels the fields. Use it to
watch what the payload thinks is happening while the camera is operated:

    ./tools/fcstate.py                 # one reading
    ./tools/fcstate.py --watch         # until interrupted, changes only

The question it was written for: whether keys other than the one assigned to
False Color reach the CameraIF slots the payload replaces. Press MENU and watch
`presses`.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import re
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'tools'))
import build_fcscale_autorun as card

FPSH = ROOT / 'host/fpsh'
FIELDS = (
    (0x00, 'mode', '0 off, 1 on, 2 on with the scale'),
    (0x04, 'presses', 'calls into fc_press, whoever made them'),
    (0x08, 'releases', 'calls into fc_release'),
    (0x0C, 'scale', 'the scale flag'),
    (0x14, 'held_ms', 'how long the last press was'),
    (0x18, 'holds', 'presses long enough to count as a hold'),
    (0x1C, 'started', 'the mode the last press started from'),
    (0x74, 'submits', 'frames seen by the hook'),
    (0x78, 'painted', 'frames it painted'),
    (0x6C, 'lock', 'the paint lock'),
)


def words(address: int, length: int) -> dict[int, int]:
    """`mem get` decoded: the reply is the console's own text, hex-encoded.

    The client writes that reply to stderr and exits nonzero, so both streams
    are read and the status is ignored."""
    result = subprocess.run([str(FPSH), 'mem', 'get', f'0x{address:08X},,{length:#x}'],
                            capture_output=True, text=True)
    out = (result.stdout + result.stderr).strip()
    if out.startswith('OKX'):
        out = bytes.fromhex(out[3:].strip()).decode('latin-1')
    return {int(a, 16): int(d, 16)
            for a, d in re.findall(r'A:0x([0-9a-fA-F]+), D:0x([0-9a-fA-F]+)', out)}


def read() -> dict[str, int]:
    seen = words(card.STATE, 0x20)
    seen.update(words(card.STATE + 0x60, 0x20))
    return {name: seen.get(card.STATE + offset, -1) for offset, name, _ in FIELDS}


def show(state: dict[str, int], previous: dict[str, int] | None) -> None:
    stamp = time.strftime('%H:%M:%S')
    parts = []
    for _offset, name, _why in FIELDS:
        value = state[name]
        moved = previous is not None and previous[name] != value
        parts.append(f"{'*' if moved else ' '}{name}={value}")
    print(f'{stamp} ' + ' '.join(parts), flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--watch', action='store_true', help='poll until interrupted')
    parser.add_argument('--interval', type=float, default=0.5)
    parser.add_argument('--seconds', type=float, default=60.0,
                        help='how long to watch for')
    args = parser.parse_args()
    if not FPSH.exists():
        raise SystemExit(f'{FPSH} is missing')
    state = read()
    show(state, None)
    if not args.watch:
        return
    deadline = time.time() + args.seconds
    while time.time() < deadline:
        time.sleep(args.interval)
        current = read()
        if current != state:
            show(current, state)
            state = current


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
