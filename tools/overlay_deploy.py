#!/usr/bin/env python3
"""Put our own overlays on a SIGMA fp (firmware Ver.5.02), over USB.

    ./tools/overlay_deploy.py surfaces  read the OSD buffer addresses off the camera
    ./tools/overlay_deploy.py place     write both payloads into the pool and prime them
    ./tools/overlay_deploy.py once      one pass of each, to check before going resident
    ./tools/overlay_deploy.py start     start the single resident thread
    ./tools/overlay_deploy.py status    counters and the last refusal code
    ./tools/overlay_deploy.py stop      ask the thread to exit, and wait for it
    ./tools/overlay_deploy.py clear     wipe the whole OSD layer

Two overlays are placed:

  * a **histogram** of the detection image channel (0xC375D8C0), an 8-bit
    grayscale frame, 320x180 on the camera this was written against
  * a **menu panel** listing every option in the card's menu at once, with the
    row the camera's cursor is on highlighted. It only reads menu.S's state
    block; RIGHT and UP still belong to the card

RAM only. Nothing is flashed and no card file is written; a battery pull puts
the camera back exactly as it was. It needs the USB shell (a debug card) and
`fpshd` already running, and the camera must not be recording.

**One thread draws both.** The menu panel is a word in the histogram's state
block, called after each pass, rather than a thread of its own: spawning a
second one from the shell's own task preceded a session where the shell stopped
answering, and nothing about drawing needs its own thread.

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
  * a mode change wipes the layer; the resident thread paints it back
"""
import argparse
import pathlib
import re
import struct
import subprocess
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'reference/fpSup/fp_usb_shell'))
from armasm import assemble, symbols                            # noqa: E402

SOURCE = ROOT / 'src/hist_overlay.S'
MENU_SOURCE = ROOT / 'src/menu_overlay.S'
POOL_PTR = 0xC3757A7C           # [0] = the pool the AutoRun asked for at boot
CODE_OFF = 0x30000              # clear of the loader window, gyro and the menu
STATE_OFF = 0x31000
MENU_STATE_OFF = 0x32000
MENU_CODE_OFF = 0x33000
HOOK_CODE_OFF = 0x34000
HOOK_STATE_OFF = 0x35000
HOOK_SOURCE = ROOT / 'src/keyhook.S'
KEY_HANDLER = 0xC091EA38        # where the card publishes its key handler
STOCK_KEYS = 0xC0265800         # the camera's own
ECHO_SLOT = 0xC0BAC2F8          # command table entry 17, echo's handler pointer
ECHO_ORIG = 0xC03D99A0
CACHE_FN = 0xC000E91C           # freshly written pool code is data until this runs
PLOT_X, PLOT_Y = 32, 64
PANEL_X, PANEL_Y = 780, 60

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
ST_SECOND = 0x58                # the menu renderer, called on the same thread
ST_HIST_ON = 0x5C               # draw the histogram at all

# and menu_overlay.S's own block
MN_STATUS, MN_PASSES, MN_STOP = 0x00, 0x04, 0x08
MN_BASE0, MN_BASE1, MN_BASE2 = 0x1C, 0x20, 0x24
MN_STRIDE, MN_SURFH, MN_X, MN_Y = 0x28, 0x2C, 0x30, 0x34
MN_CURSOR, MN_MENUINIT = 0x38, 0x44
MN_SET_FN, MN_SET_VALUE, MN_SET_DONE = 0x48, 0x4C, 0x50
MN_FOLLOW = 0x54                # 1 = an option that turns on selects its rate
MN_IDLE = 0x58                  # passes of no change before the panel hides
MN_GATE = 0x70                  # address of the key hook's open flag

# keyhook.S's block
HK_EVENTS, HK_CARD, HK_STOCK, HK_OPEN = 0x00, 0x04, 0x08, 0x0C
HK_OPENS, HK_LASTKEY, HK_SPAWN, HK_SPAWNS = 0x1C, 0x20, 0x24, 0x28

# analysis/menu_setters.json, the rows worth reaching from a keypress
SETTERS = {
    'framerate': 0xC005C0B8,        # SetMovFramerate
    'recsize': 0xC005C020,          # SetMovRecSize
    'recformat': 0xC005BE58,        # SetMovRecFormat
    'dngquality': 0xC005BEF0,       # SetMovCinemaDNGQuality
    'cropmode': 0xC005BB60,         # SetCropMode
}

REFUSALS = {
    0x11: 'frame pointer below the DRAM window', 0x12: 'frame pointer above it',
    0x13: 'zero width', 0x14: 'implausible width', 0x15: 'zero height',
    0x16: 'implausible height', 0x17: 'frame larger than the payload allows',
    0x18: 'every bin empty', 0x20: 'no drawable', 0x21: 'no backbuffer',
    0x22: 'backbuffer carries no geometry', 0x23: 'surface below the OSD window',
    0x24: 'surface above it', 0x25: 'plot wider than the surface',
    0x26: 'plot taller than the surface',
}

MENU_REFUSALS = {
    0x30: 'the card menu is not loaded, so there is no state to show',
    0x31: 'panel wider than the surface', 0x32: 'panel taller than the surface',
    0x33: 'surface below the OSD window', 0x34: 'surface above it',
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


def build(source, state_addr):
    defines = (f'STATE_ADDR={hex(state_addr)}',)
    return assemble(source, defines), symbols(source, defines)


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

def menu_status(menu_state):
    fields = struct.unpack('<18I', read(menu_state, 72))
    code = fields[0]
    print(f'  menu status   {code}'
          + ('  done' if code == 1 else '  running' if code == 0
             else '  refused: ' + MENU_REFUSALS.get(code, 'unknown')))
    print(f'  menu passes   {fields[1]}   cursor row {fields[14]}'
          f'   card menu armed={fields[17]}')


def place_code(code_at, blob, what):
    """Write, verify, repair. `mem set` drops writes, so nothing is assumed."""
    expected = list(struct.unpack(f'<{len(blob)//4}I', blob))
    for i, value in enumerate(expected):
        shell('mem', 'set', f'{code_at + i*4:#x}', f'{value:#x}')
    for _ in range(6):
        got = list(struct.unpack(f'<{len(blob)//4}I', read(code_at, len(blob))))
        holes = [i for i, v in enumerate(expected) if got[i] != v]
        if not holes:
            return
        for i in holes:
            shell('mem', 'set', f'{code_at + i*4:#x}', f'{expected[i]:#x}')
    raise SystemExit(f'{what} would not land intact')

def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('command', choices=('surfaces', 'place', 'once', 'start',
                                            'status', 'stop', 'clear', 'set',
                                            'unhook'))
    parser.add_argument('--setting', choices=sorted(SETTERS),
                        help='which camera setting `set` changes')
    parser.add_argument('--value', type=int, help='the value `set` writes')
    parser.add_argument('--no-histogram', action='store_true',
                        help='place the menu panel only, and leave the rest of '
                             'the screen alone')
    parser.add_argument('--keys', action='store_true',
                        help="take over the key handler: AEL opens and closes "
                             "the panel, and RIGHT/UP are the camera's again "
                             "whenever it is closed")
    parser.add_argument('--hide-after', type=int, default=25, metavar='PASSES',
                        help='hide the panel after this many passes with nothing '
                             'moving, roughly a fifth of a second each; 0 keeps '
                             'it up permanently')
    parser.add_argument('--follow', action='store_true',
                        help='let an option that turns on select its recording '
                             'rate (place/start only)')
    parser.add_argument('--x', type=int, default=PLOT_X)
    parser.add_argument('--y', type=int, default=PLOT_Y)
    parser.add_argument('--panel-x', type=int, default=PANEL_X)
    parser.add_argument('--panel-y', type=int, default=PANEL_Y)
    args = parser.parse_args()

    base = pool()
    code_at, state = base + CODE_OFF, base + STATE_OFF
    menu_code_at, menu_state = base + MENU_CODE_OFF, base + MENU_STATE_OFF
    hook_code_at, hook_state = base + HOOK_CODE_OFF, base + HOOK_STATE_OFF

    if args.command == 'clear':
        for _ in range(3):
            shell('display', 'osd', '1', '0x00000000')
        print('OSD layer cleared')
        return 0

    if args.command == 'status':
        print(f'pool {base:#010x}  code {code_at:#010x}  state {state:#010x}')
        status(state)
        menu_status(menu_state)
        return 0

    blob, syms = build(SOURCE, state)
    entry, body, spawn = (code_at + syms[n] for n in ('hist_entry', 'hist_body',
                                                      'hist_spawn'))
    menu_blob, menu_syms = build(MENU_SOURCE, menu_state)
    hook_blob, hook_syms = build(HOOK_SOURCE, hook_state)
    hook_entry = hook_code_at + hook_syms['keyhook']
    menu_entry = menu_code_at + menu_syms['menu_entry']
    menu_core = menu_code_at + menu_syms['menu_core']

    if args.command == 'unhook':
        published = word(KEY_HANDLER)
        original = word(hook_state + HK_CARD)
        if published != hook_entry:
            print(f'the key handler is {published:#010x}, not ours; leaving it')
            return 0
        if not (0x40000000 <= original < 0x50000000 or original == STOCK_KEYS):
            raise SystemExit(f'refusing to publish {original:#010x}')
        write(KEY_HANDLER, original)
        print(f"key handler back to {original:#010x}; AEL is the camera's again")
        return 0

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
        # A cold boot leaves the pool full of whatever was there, so the state
        # block cannot be trusted on its own: an old counter reads as a running
        # thread and refuses a perfectly safe placement. Ask the only question
        # that matters instead -- is our code there, and is it still counting?
        placed = read(code_at, 32) == blob[:32]
        if placed and word(state + ST_EXITED) != 1:
            before = word(state + ST_LIVE)
            time.sleep(0.5)
            if word(state + ST_LIVE) != before:
                raise SystemExit('the resident thread is running; `stop` first')
        place_code(code_at, blob, 'the histogram payload')
        place_code(menu_code_at, menu_blob, 'the menu payload')
        call_once(CACHE_FN, 'cache maintenance')

        buffers, geometry = surfaces()
        base1 = buffers[1] if len(buffers) > 1 else 0
        base2 = buffers[2] if len(buffers) > 2 else 0
        # Clear the WHOLE state block, not just the first 0x60. A cold boot
        # leaves the pool holding garbage, and the menu payload keeps live
        # fields well past that: +0xC0 is "re-arm false colour", which read as
        # armed on a fresh place and made our own thread post event 0x21 about
        # once a second. False colour then could not be switched off from the
        # camera -- a half-press cleared it and we turned it back on.
        for offset in range(0x00, 0x180, 4):
            write(state + offset, 0)
            write(menu_state + offset, 0)
        for offset, value in ((ST_BASE0, buffers[0]), (ST_BASE1, base1),
                              (ST_BASE2, base2),
                              (ST_STRIDE_SET, geometry[0]), (ST_SURFH, geometry[1]),
                              (ST_PLOTX, args.x), (ST_PLOTY, args.y),
                              (ST_BODYOBJ, state + ST_VTABLE),
                              (ST_VTABLE + 0xC, body),
                              (ST_SECOND, menu_core),
                              (ST_HIST_ON, 0 if args.no_histogram else 1)):
            write(state + offset, value)
        for offset, value in ((MN_BASE0, buffers[0]), (MN_BASE1, base1),
                              (MN_BASE2, base2),
                              (MN_STRIDE, geometry[0]), (MN_SURFH, geometry[1]),
                              (MN_X, args.panel_x), (MN_Y, args.panel_y),
                              (MN_FOLLOW, 1 if args.follow else 0),
                              (MN_IDLE, max(0, args.hide_after)),
                              (MN_GATE, hook_state + HK_OPEN if args.keys else 0)):
            write(menu_state + offset, value)
        print(f'histogram {code_at:#010x} ({len(blob)} bytes), state {state:#010x}')
        print(f'menu      {menu_code_at:#010x} ({len(menu_blob)} bytes),'
              f' state {menu_state:#010x}')
        print(f'surfaces  {", ".join(f"{b:#010x}" for b in buffers)}')
        print('histogram ' + ('off' if args.no_histogram else
                              f'on at {args.x},{args.y}'))
        print(f'placed    plot at {args.x},{args.y}'
              f'   panel at {args.panel_x},{args.panel_y}')
        print('hide      ' + (f'after {args.hide_after} idle passes; any key'
                              ' brings it back' if args.hide_after else 'never'))
        print('follow    ' + ('on: turning an option on selects its rate'
                              if args.follow else 'off: the camera is only read'))
        if args.keys:
            # Published last, and only after every pointer it needs is in
            # place: a half-wired handler here is a camera with no buttons.
            #
            # The code itself MUST be written first. Publishing hook_entry
            # without it points the camera's key handler at whatever the pool
            # happened to hold, and the next button press runs that. It cost a
            # crash: `--keys` used to publish an address nothing had filled in.
            place_code(hook_code_at, hook_blob, 'the key gate')
            for offset in range(0x00, 0x40, 4):
                write(hook_state + offset, 0)
            write(hook_state + HK_CARD, word(KEY_HANDLER))
            write(hook_state + HK_STOCK, STOCK_KEYS)
            call_once(CACHE_FN, 'cache maintenance')
            write(KEY_HANDLER, hook_entry)
            print(f'keys      AEL opens and closes the panel; RIGHT/UP are the'
                  f" camera's while it is closed\n"
                  f'          chaining to {word(hook_state + HK_CARD):#010x};'
                  f' `unhook` puts it back')
        return 0

    if args.command == 'set':
        # The write happens on the overlay thread, not in the shell's
        # dispatcher: this is the camera's own property call, and the one note
        # we have about it says it was proven from a task rather than from the
        # shell. The thread has to be running to pick the request up.
        if args.setting is None or args.value is None:
            raise SystemExit('`set` needs --setting and --value')
        if word(state + ST_LIVE) == 0:
            raise SystemExit('`start` the thread first; it is what applies this')
        done = word(menu_state + MN_SET_DONE)
        write(menu_state + MN_SET_VALUE, args.value)
        write(menu_state + MN_SET_FN, SETTERS[args.setting])
        for _ in range(20):
            time.sleep(0.2)
            if word(menu_state + MN_SET_DONE) != done:
                print(f'{args.setting} = {args.value} applied by the camera-side thread')
                return 0
        raise SystemExit('the thread did not apply it; is it still running?')

    if args.command == 'once':
        call_once(entry, 'one histogram pass')
        call_once(menu_entry, 'one menu pass')
        status(state)
        menu_status(menu_state)
        return 0

    if args.command == 'start':
        if word(state + ST_VTABLE + 0xC) != body:
            raise SystemExit('run `place` first')
        if word(state + ST_SECOND) != menu_core:
            raise SystemExit('the menu renderer is not wired up; run `place`')

        # Spawned from the borrowed shell handler. Doing it from the key hook
        # instead froze the camera outright -- record light on, no UI, no
        # buttons, no USB -- because that handler runs in the camera's own UI
        # path and a blocking call there takes everything with it.
        call_once(spawn, 'spawn')
        time.sleep(1.0)
        first = word(state + ST_LIVE)
        time.sleep(2.0)
        rate = (word(state + ST_LIVE) - first) / 2.0
        if rate <= 0:
            status(state)
            raise SystemExit('the thread is not running')
        print(f'one thread drawing both overlays, {rate:.1f} passes per second')
        print('`stop` ends it; a battery pull removes everything')
        return 0
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
