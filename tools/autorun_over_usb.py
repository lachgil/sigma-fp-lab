#!/usr/bin/env python3
"""Replay an AutoRun's commands over the live USB shell instead of at boot.

Why this exists: appending a payload's AutoRun to the shell's own card does not
work. The shell's loader becomes the worker part way through the script, and
whatever follows is never executed (camera, 2026-09-21: the shell came up, the
payload was not armed and the button was back to its stock hold behaviour).

With the shell up, the same `mem set` / `echo` lines can simply be sent, which
also means a payload can be armed, inspected and re-armed without a reboot.

    ./tools/autorun_over_usb.py builds/fcscale/AutoRun.txt

Only `mem set` and `echo` are sent by default: those are what arm a payload.
`display` lines are boot cosmetics and are skipped unless --display is given.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parent.parent
FPSH = ROOT / 'host/fpsh'


def once(command: list[str]) -> tuple[bool, str]:
    """One firmware shell command. The client prints the camera's own reply,
    which differs per command (`pong`, `set : A:.., D:..`, `OKX<hex>`), so a
    failure is recognised rather than a success."""
    result = subprocess.run([str(FPSH), *command], capture_output=True, text=True)
    out = (result.stdout + result.stderr).strip()
    if command == ['echo']:
        # `echo` is not an echo here: the script repoints its handler slot at
        # the cache-clean routine and issues it to make new code real to the
        # caches, so a silent reply is the expected one.
        return 'Traceback' not in out, out
    bad = bool(result.returncode) or not out or 'Traceback' in out or out.startswith('ERR')
    return not bad, out


def send(command: list[str], tries: int = 4) -> str:
    """Retry a command that the link, not the camera, refused. A single
    `mem set` timing out is common on a busy link and every write here is
    idempotent, so repeating one is safe."""
    for attempt in range(tries):
        good, out = once(command)
        if good:
            return out
        if 'TIMEOUT' not in out and 'ERR' not in out:
            break
        time.sleep(0.15 * (attempt + 1))
    raise SystemExit(f'{" ".join(command)} -> {out[:200] or "no reply"}')


def wanted(line: str, display: bool) -> bool:
    if line.startswith('mem set ') or line == 'echo':
        return True
    return display and line.startswith('display ')


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('autorun', type=Path)
    parser.add_argument('--display', action='store_true',
                        help="also send the script's display commands")
    args = parser.parse_args()
    if not FPSH.exists():
        raise SystemExit(f'{FPSH} is missing: build the shell host tools first')

    lines = [line.strip() for line in args.autorun.read_text().splitlines()]
    commands = [line for line in lines
                if line and not line.startswith('#') and wanted(line, args.display)]
    if not commands:
        raise SystemExit('nothing to send: no mem set or echo lines')

    send(['ping'])
    started = time.time()
    for number, line in enumerate(commands, 1):
        send(line.split())
        if number % 100 == 0:
            print(f'  {number}/{len(commands)}', flush=True)
    print(f'{len(commands)} commands in {time.time() - started:.1f}s '
          f'from {args.autorun}')


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        sys.exit('interrupted')
