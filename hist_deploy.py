#!/usr/bin/env python3
"""Put the live histogram overlay on a SIGMA fp (firmware Ver.5.02), over USB.

    ./hist_deploy.py surfaces     read the OSD buffer addresses off the camera
    ./hist_deploy.py place        write the payload into the pool and prime it
    ./hist_deploy.py once         one pass, for checking before going resident
    ./hist_deploy.py start        start the resident thread (about 12 passes/s)
    ./hist_deploy.py status       counters and the last refusal code
    ./hist_deploy.py stop         ask the thread to exit, and wait for it
    ./hist_deploy.py clear        wipe the whole OSD layer

RAM only. Nothing is flashed and no card file is written; a battery pull puts
the camera back exactly as it was. It needs the USB shell (a debug card) and
`fpshd` already running, and the camera must not be recording.

What it does on the camera:

  * reads the detection image channel's descriptor at 0xC375D8C0 -- an 8-bit
    grayscale frame, 320x180 on the camera this was written against
  * bins those samples and paints bars into the OSD layer's own buffers

The overlay layer rotates three buffers and only the camera's UI decides which
is on screen, so every pass paints all of them; painting one made the plot
vanish as soon as the UI presented another. `surfaces` is what learns those
addresses: `display osd 1` prints the buffer it just presented.

Honest limits, so nobody is misled by a pretty picture:

  * the camera's own histogram is better -- it is calibrated and needs no
    cable. This exists to prove our code can read the image and draw with it,
    which is the part the fp does not otherwise let you do
  * the detection feed has no frame lock, so a plot can straddle two frames
  * the bars are display-path grayscale codes, not calibrated raw clipping
  * a mode change wipes the layer; run `once` again, or leave the thread going
"""
import argparse
import pathlib
import re
import struct
import subprocess
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'reference/fpSup/fp_usb_shell'))
from armasm import assemble, symbols                            # noqa: E402

SOURCE = ROOT / 'src/payloads/hist_overlay.S'
POOL_PTR = 0xC3757A7C           # [0] = the pool the AutoRun asked for at boot
CODE_OFF = 0x30000              # clear of the loader window, gyro and the menu
STATE_OFF = 0x31000
ECHO_SLOT = 0xC0BAC2F8          # command table entry 17, echo's handler pointer
ECHO_ORIG = 0xC03D99A0
CACHE_FN = 0xC000E91C           # freshly written pool code is data until this runs
PLOT_X, PLOT_Y = 32, 64

# state block offsets, mirroring the header comment in hist_overlay.S
ST_STATUS, ST_PIXELS, ST_WIDTH, ST_HEIGHT = 0x00, 0x04, 0x08, 0x0C
ST_SAMPLES, ST_MAXBIN, ST_SHIFT = 0x10, 0x14, 0x18
ST_BASE, ST_STRIDE, ST_SURFH, ST_PASSES = 0x1C, 0x20, 0x24, 0x28
ST_BASE0, ST_STRIDE_SET = 0x2C, 0x30
ST_PLOTX, ST_PLOTY = 0x34, 0x38
ST_STOP, ST_LIVE, ST_EXITED = 0x3C, 0x40, 0x44
ST_ATTACH, ST_CREATE = 0x48, 0x4C
ST_BASE1, ST_BASE2 = 0x50, 0x54
ST_BODYOBJ, ST_VTABLE = 0xC0, 0xC4

REFUSALS = {
    0x11: 'frame pointer below the DRAM window', 0x12: 'frame pointer above it',
    0x13: 'zero width', 0x14: 'implausible width', 0x15: 'zero height',
    0x16: 'implausible height', 0x17: 'frame larger than the payload allows',
    0x18: 'every bin empty', 0x20: 'no drawable', 0x21: 'no backbuffer',
    0x22: 'backbuffer carries no geometry', 0x23: 'surface below the OSD window',
    0x24: 'surface above it', 0x25: 'plot wider than the surface',
    0x26: 'plot taller than the surface',
}


def shell(*args, attempts=6):
    """One shell command. Replies do go missing, so a lost one is retried."""
    last = ''
    for _ in range(attempts):
        run = subprocess.run([str(ROOT / 'host' / 'fpsh'), *args],
                             cwd=ROOT, capture_output=True, text=True, timeout=80)
        out = (run.stdout + run.stderr).strip()
        if out.startswith('OKX '):
            return bytes.fromhex(out[4:].strip()).decode(errors='replace')
        if not run.returncode and not out.startswith(('ERR', 'NG')):
            return out
        last = out
        time.sleep(0.4)
    raise SystemExit(f'shell command failed: {" ".join(args)}: {last}')


def read(addr, count=4):
    """Read `count` bytes. Reads are reliable; a short reply is an error."""
    out = bytearray()
    for base in range(0, count, 1024):
        size = min(1024, count - base)
        at = addr + base
        reply = shell('mem', 'get', f'{at:#x},,{size}')
        words = re.findall(r'A:0x([0-9a-fA-F]+), D:0x([0-9a-fA-F]+)', reply)
        if [int(a, 16) for a, _ in words] != list(range(at, at + size, 4)):
            raise SystemExit(f'short read at {at:#010x}')
        out += b''.join(struct.pack('<I', int(v, 16)) for _, v in words)
    return bytes(out)


def word(addr):
    return struct.unpack('<I', read(addr))[0]


def write(addr, value, tries=8):
    """`mem set` drops writes silently, so every one is read back."""
    for _ in range(tries):
        shell('mem', 'set', f'{addr:#x}', f'{value:#x}')
        if word(addr) == value:
            return
    raise SystemExit(f'write did not hold at {addr:#010x}')


def pool():
    seen = {word(POOL_PTR) for _ in range(3)}
    if len(seen) != 1:
        raise SystemExit('the pool pointer is not settling; is the card loaded?')
    base = seen.pop()
    if not 0x40000000 <= base < 0x50000000:
        raise SystemExit(f'the pool pointer reads {base:#010x}')
    return base


def build(state_addr):
    defines = (f'STATE_ADDR={hex(state_addr)}',)
    return assemble(SOURCE, defines), symbols(SOURCE, defines)


def call_once(addr, label):
    """Run a routine by borrowing echo's handler, and always give it back.

    A long pass can outlast the reply window, so a lost reply is not a failure;
    a handler left pointing at our code would be, which is why the restore
    retries until a read-back proves it.
    """
    if word(ECHO_SLOT) != ECHO_ORIG:
        raise SystemExit('echo handler is not free to borrow')
    write(ECHO_SLOT, addr)
    try:
        try:
            shell('echo', attempts=1)
        except SystemExit:
            print(f'  {label}: reply lost, checking the state block instead')
    finally:
        for _ in range(12):
            shell('mem', 'set', f'{ECHO_SLOT:#x}', f'{ECHO_ORIG:#x}')
            if word(ECHO_SLOT) == ECHO_ORIG:
                break
        else:
            raise SystemExit('ECHO HANDLER LEFT REDIRECTED -- reboot the camera')


def surfaces():
    """Present the layer three times; each present names the buffer it used."""
    found = []
    for _ in range(4):
        for line in shell('display', 'osd', '1').splitlines():
            hit = re.search(r'addr 0x([0-9a-fA-F]{8}) \((\d+),(\d+)\)', line)
            if hit:
                base = int(hit.group(1), 16)
                geometry = (int(hit.group(2)), int(hit.group(3)))
                if base not in [b for b, _ in found]:
                    found.append((base, geometry))
    if not found:
        raise SystemExit('`display osd 1` reported no buffer')
    if len({g for _, g in found}) != 1:
        raise SystemExit(f'buffers disagree about geometry: {found}')
    return [b for b, _ in found], found[0][1]


def status(state):
    fields = struct.unpack('<20I', read(state, 80))
    code = fields[0]
    print(f'  status        {code}'
          + ('  done' if code == 1 else '  running' if code == 0
             else '  refused: ' + REFUSALS.get(code, 'unknown')))
    print(f'  frame         {fields[1]:#010x}  {fields[2]}x{fields[3]}'
          f'  {fields[4]} samples')
    print(f'  bins          max {fields[5]}, scaled down by {fields[6]} bits')
    print(f'  surface       {fields[7]:#010x}  stride {fields[8]}  height {fields[9]}')
    print(f'  passes        {fields[10]} total, {fields[16]} resident')
    print(f'  thread        exited={fields[17]} attach={fields[18]:#x}'
          f' create={fields[19]:#010x}')


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('command', choices=('surfaces', 'place', 'once', 'start',
                                            'status', 'stop', 'clear'))
    parser.add_argument('--x', type=int, default=PLOT_X)
    parser.add_argument('--y', type=int, default=PLOT_Y)
    args = parser.parse_args()

    base = pool()
    code_at, state = base + CODE_OFF, base + STATE_OFF

    if args.command == 'clear':
        for _ in range(3):
            shell('display', 'osd', '1', '0x00000000')
        print('OSD layer cleared')
        return 0

    if args.command == 'status':
        print(f'pool {base:#010x}  code {code_at:#010x}  state {state:#010x}')
        status(state)
        return 0

    blob, syms = build(state)
    entry, body, spawn = (code_at + syms[n] for n in ('hist_entry', 'hist_body',
                                                      'hist_spawn'))

    if args.command == 'stop':
        write(state + ST_STOP, 1)
        for _ in range(20):
            time.sleep(0.3)
            if word(state + ST_EXITED) == 1:
                print('the thread stepped out; its code can be replaced now')
                return 0
        raise SystemExit('the thread did not exit -- do not overwrite its code')

    if args.command == 'surfaces':
        buffers, geometry = surfaces()
        print(f'{len(buffers)} buffers, {geometry[0]}x{geometry[1]}, one byte per pixel:')
        for b in buffers:
            print(f'  {b:#010x}')
        return 0

    if args.command == 'place':
        if word(state + ST_LIVE) and word(state + ST_EXITED) != 1:
            raise SystemExit('a resident thread may still be running; `stop` first')
        expected = list(struct.unpack(f'<{len(blob)//4}I', blob))
        for i, value in enumerate(expected):
            shell('mem', 'set', f'{code_at + i*4:#x}', f'{value:#x}')
        for _ in range(6):
            got = list(struct.unpack(f'<{len(blob)//4}I', read(code_at, len(blob))))
            holes = [i for i, v in enumerate(expected) if got[i] != v]
            if not holes:
                break
            for i in holes:
                shell('mem', 'set', f'{code_at + i*4:#x}', f'{expected[i]:#x}')
        else:
            raise SystemExit('the payload would not land intact')
        call_once(CACHE_FN, 'cache maintenance')

        buffers, geometry = surfaces()
        for offset in range(0x00, 0x60, 4):
            write(state + offset, 0)
        for offset, value in ((ST_BASE0, buffers[0]),
                              (ST_BASE1, buffers[1] if len(buffers) > 1 else 0),
                              (ST_BASE2, buffers[2] if len(buffers) > 2 else 0),
                              (ST_STRIDE_SET, geometry[0]), (ST_SURFH, geometry[1]),
                              (ST_PLOTX, args.x), (ST_PLOTY, args.y),
                              (ST_BODYOBJ, state + ST_VTABLE),
                              (ST_VTABLE + 0xC, body)):
            write(state + offset, value)
        print(f'payload at {code_at:#010x} ({len(blob)} bytes), state {state:#010x}')
        print(f'surfaces {", ".join(f"{b:#010x}" for b in buffers)}'
              f'  plot at {args.x},{args.y}')
        return 0

    if args.command == 'once':
        call_once(entry, 'one pass')
        status(state)
        return 0

    if args.command == 'start':
        if word(state + ST_VTABLE + 0xC) != body:
            raise SystemExit('run `place` first')
        call_once(spawn, 'spawn')
        time.sleep(1.0)
        first = word(state + ST_LIVE)
        time.sleep(2.0)
        rate = (word(state + ST_LIVE) - first) / 2.0
        if rate <= 0:
            status(state)
            raise SystemExit('the thread is not running')
        print(f'resident histogram running, {rate:.1f} passes per second')
        print('`stop` ends it; a battery pull removes everything')
        return 0
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
