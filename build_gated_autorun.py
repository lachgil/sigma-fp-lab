#!/usr/bin/env python3
"""Build an experimental selector-gated open-gate AutoRun for fp 5.02.

Uses fpSup's slot-7 picker, mode-117 VMAX and four profile-122 RWZM patches.
The canvas hook additionally requires ARMED, a 1936x1090 row and selector
r5==175. r5==180 (observed FHD/25) passes through. This is not a check of the
selected sensor-mode register and does not establish safety for every mode.

Build-time checks verify the exact MAIN, stock patch words and empty cave.
The generated AutoRun has no runtime firmware-version guard. Use only on fp
5.02, with USB disconnected during cold boot as required by fpSup. This is
not a green-preview fix; recording correctness still needs hardware testing.
"""
from __future__ import annotations

import argparse
import hashlib
import pathlib
import struct
import subprocess
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
REF = HERE / "reference" / "fpSup"
SHELL_DIR = REF / "fp_usb_shell"
SHELL_BUILDER = SHELL_DIR / "build_autorun.py"
SOURCE = HERE / "src" / "rowpatch_gated.S"
sys.path.insert(0, str(SHELL_DIR))
from armasm import assemble, words  # noqa: E402

# Reuse the reviewed helpers from the upstream open-gate builder verbatim.
sys.path.insert(0, str(REF / "opengate"))
import build_test_autorun as og  # noqa: E402

CODE = 0xC072F800
LOG = 0xC072FA00
ARMED = 0xC072FA10
HOOK = 0xC043A19C
HOOK_STOCK = 0xE1A00004
HOOK_BRANCH = 0xEB000000 | ((((CODE - HOOK - 8) >> 2) & 0xFFFFFF))
BANNER = "fpOGgate!"
FIRMWARE_BASE = 0xC0000000


def firmware_word(image: bytes, address: int) -> int:
    return struct.unpack_from("<I", image, address - FIRMWARE_BASE)[0]


def verify_firmware(path: pathlib.Path, blob: bytes) -> None:
    image = path.read_bytes()
    if (len(image) != og.FIRMWARE_BYTES
            or hashlib.sha256(image).hexdigest() != og.FIRMWARE_SHA256):
        raise SystemExit("reference firmware is not the verified fp Ver.5.02 image")
    for address, stock, _, _ in og.DATA_PATCHES:
        if firmware_word(image, address) != stock:
            raise SystemExit(f"stock mismatch at 0x{address:08X}")
    if firmware_word(image, HOOK) != HOOK_STOCK:
        raise SystemExit("hook site is not the stock mov r0,r4")
    cave_start = CODE - FIRMWARE_BASE
    cave_end = ARMED + 4 - FIRMWARE_BASE
    if any(image[cave_start:cave_end]):
        raise SystemExit("cave 0xC072F800..0xC072FA14 is not empty in firmware")
    if CODE + len(blob) > LOG:
        raise SystemExit("gated payload overlaps the telemetry log")


def gated_section(blob: bytes) -> str:
    lines = [
        "# --- MODE-GATED 3032x2012 open gate @29.97 -------------------------",
        "# fp Ver.5.02 ONLY. Cold-boot RAM patches; remove AutoRun.txt and",
        "# power-cycle (battery out) to return to stock. Canvas rewrite requires",
        "# ARMED, a 1936x1090 row and selector r5==175 (not a mode-id check).",
        "",
    ]
    for address, stock, value, comment in og.DATA_PATCHES:
        lines.append(f"# {comment}; stock 0x{stock:08X}")
        lines.append(f"mem set 0x{address:08X} 0x{value:08X}")
    lines += ["", f"# clear telemetry words at 0x{LOG:08X}"]
    for offset in range(0, 0x10, 4):
        lines.append(f"mem set 0x{LOG + offset:08X} 0x00000000")
    lines += ["", "# arm the feature (controller flag)",
              f"mem set 0x{ARMED:08X} 0x00000001"]
    lines += ["", f"# mode-gated rowpatch, {len(blob)} bytes"]
    for index, value in enumerate(words(blob)):
        lines.append(f"mem set 0x{CODE + index * 4:08X} 0x{value:08X}")
    lines += [
        "",
        "# Arm last: stock instruction is 0xE1A00004 (mov r0,r4).",
        f"mem set 0x{HOOK:08X} 0x{HOOK_BRANCH:08X}",
        "# --- END gated open gate --------------------------------------------",
        "",
    ]
    return "\n".join(lines)


def build(firmware: pathlib.Path, out: pathlib.Path) -> None:
    blob = assemble(SOURCE)
    if words(blob)[-2:] != (0xE1A00004, 0xE12FFF1E):
        raise SystemExit("payload must end with the displaced mov and bx lr")
    verify_firmware(firmware, blob)
    with tempfile.TemporaryDirectory() as tmp:
        base_path = pathlib.Path(tmp) / "AutoRun.base.txt"
        result = subprocess.run(
            (sys.executable, str(SHELL_BUILDER), "--out", str(base_path),
             "--banner", BANNER, "--no-ep-patches", "--no-pad"), check=False)
        if result.returncode:
            raise SystemExit("canonical shell builder failed")
        base = og.describe_minimal_usb_shell(base_path.read_text(encoding="utf-8"))
    section = gated_section(blob)
    output = og.insert_before_done(base, section)
    # Structural checks reused from upstream, adapted for the ARMED init line.
    if output.count(section) != 1:
        raise SystemExit("gated section missing or duplicated")
    hook_line = f"mem set 0x{HOOK:08X} 0x{HOOK_BRANCH:08X}"
    if og.parse_mem_sets(output)[-1] != (HOOK, HOOK_BRANCH):
        raise SystemExit("hook must be the final mem set")
    prefix = output[:output.find(section)]
    for address, _ in og.parse_mem_sets(prefix):
        if CODE <= address < ARMED + 4:
            raise SystemExit(f"shell overlaps cave at 0x{address:08X}")
    output_bytes = og.pad_bytes(output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(output_bytes)
    print(f"built {out} ({len(output_bytes)} bytes)")
    print(f"sha256 {hashlib.sha256(output_bytes).hexdigest()}")
    print(f"payload {len(blob)} bytes at 0x{CODE:08X}; ARMED at 0x{ARMED:08X}; "
          f"hook branch 0x{HOOK_BRANCH:08X}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--firmware", type=pathlib.Path,
                        default=HERE / "analysis" / "MAIN_c0000000.bin")
    parser.add_argument("--out", type=pathlib.Path,
                        default=HERE / "builds" / "opengate-gated" / "AutoRun.txt")
    args = parser.parse_args()
    build(args.firmware, args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
