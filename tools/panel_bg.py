#!/usr/bin/env python3
"""Sweep the panel's background colour on a running camera, over the USB shell.

    ./tools/panel_bg.py            show the current value
    ./tools/panel_bg.py 0x20       set one value and leave it there
    ./tools/panel_bg.py sweep      step through candidates, pausing at each

The OSD layer is one byte per pixel into a palette we do not have a dump of, so
which index is an opaque black cannot be worked out from the firmware image.
This pokes the panel's background word and lets a human say which one looks
right. 0 is transparent, which is what the panel shipped with.

Needs the dev card (it has the USB shell) and `fpshd` running.
"""
import subprocess
import sys
import time

PANEL_STATE = 0xC072FC00
BG = PANEL_STATE + 0x160
CANDIDATES = [0x00, 0x01, 0x10, 0x20, 0x30, 0x40, 0x60, 0x80, 0xA0, 0xB2, 0xC0, 0xE0, 0xFF]


def shell(*args):
    out = subprocess.run(['./host/fpsh', *args], capture_output=True, text=True)
    if 'ERR' in out.stdout:
        raise SystemExit(f'shell refused: {out.stdout.strip()}')
    return out.stdout


def read():
    text = shell('mem', 'get', f'{BG:#x},,4')
    return int(text.split('D:0x')[1].split()[0], 16)


def write(value):
    shell('mem', 'set', f'{BG:#x}', f'{value:#x}')


def main():
    if len(sys.argv) == 1:
        print(f'background is {read():#04x} (0 = transparent)')
        return 0
    if sys.argv[1] == 'sweep':
        print('watch the camera; each value is held for three seconds')
        for value in CANDIDATES:
            write(value)
            print(f'  {value:#04x}')
            time.sleep(3)
        write(0)
        print('back to 0 (transparent). Re-run with the one you liked.')
        return 0
    write(int(sys.argv[1], 0))
    print(f'background set to {read():#04x}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
