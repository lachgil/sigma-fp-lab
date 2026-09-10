#!/usr/bin/env python3
"""Firmware analyzer for SIGMA fp MAIN (ARM, base 0xC0000000).

Builds a SQLite map of strings, functions (prologue-detected), BL call edges,
and literal-pool address references, so subsystems can be located by name/xref.

  fwmap.py build              # (re)build analysis/fw.sqlite
  fwmap.py str <regex>        # search strings
  fwmap.py xref <hexaddr>     # who references this address (BL or literal)
  fwmap.py near <hexaddr>     # nearest string/function to an address
"""
import re
import sqlite3
import struct
import sys
from pathlib import Path

BASE = 0xC0000000
IMG = Path("analysis/MAIN_c0000000.bin")
DB = Path("analysis/fw.sqlite")


def build():
    img = IMG.read_bytes()
    n = len(img)
    con = sqlite3.connect(DB)
    con.executescript("""
        DROP TABLE IF EXISTS strings; DROP TABLE IF EXISTS funcs;
        DROP TABLE IF EXISTS calls;   DROP TABLE IF EXISTS arefs;
        CREATE TABLE strings(addr INT, len INT, text TEXT);
        CREATE TABLE funcs(addr INT);
        CREATE TABLE calls(src INT, dst INT);
        CREATE TABLE arefs(src INT, target INT);
    """)
    # ASCII strings (>=4 printable, NUL/newline terminated)
    rows = []
    i = 0
    while i < n:
        c = img[i]
        if 32 <= c < 127:
            j = i + 1
            while j < n and 32 <= img[j] < 127:
                j += 1
            if j - i >= 4:
                rows.append((BASE + i, j - i, img[i:j].decode("latin1")))
            i = j
        else:
            i += 1
    con.executemany("INSERT INTO strings VALUES(?,?,?)", rows)
    # word scan: BL edges, function prologues, literal address refs
    funcs, calls, arefs = set(), [], []
    for off in range(0, n - 3, 4):
        w = struct.unpack_from("<I", img, off)[0]
        addr = BASE + off
        # BL (cond=E, 1011)
        if (w & 0x0F000000) == 0x0B000000:
            imm = w & 0xFFFFFF
            if imm & 0x800000:
                imm -= 0x1000000
            calls.append((addr, addr + 8 + imm * 4))
        # STMFD sp!, {..lr} prologue  (E92D....  with bit14 lr)
        if (w & 0xFFFF0000) == 0xE92D0000 and (w & 0x4000):
            funcs.add(addr)
        # literal pool word that points into the image (address ref)
        if BASE <= w < BASE + n:
            arefs.append((addr, w))
    con.executemany("INSERT INTO funcs VALUES(?)", [(f,) for f in sorted(funcs)])
    con.executemany("INSERT INTO calls VALUES(?,?)", calls)
    con.executemany("INSERT INTO arefs VALUES(?,?)", arefs)
    con.execute("CREATE INDEX i_calls_dst ON calls(dst)")
    con.execute("CREATE INDEX i_arefs_t ON arefs(target)")
    con.execute("CREATE INDEX i_str_addr ON strings(addr)")
    con.commit()
    print(f"strings={len(rows)} funcs={len(funcs)} calls={len(calls)} arefs={len(arefs)}")
    con.close()


def _con():
    return sqlite3.connect(DB)


def cmd_str(pat):
    rx = re.compile(pat, re.I)
    con = _con()
    for addr, text in con.execute("SELECT addr,text FROM strings"):
        if rx.search(text):
            print(f"  {addr:#010x}  {text!r}")


def cmd_xref(target):
    con = _con()
    print(f"BL callers of {target:#x}:")
    for (src,) in con.execute("SELECT src FROM calls WHERE dst=?", (target,)):
        print(f"  {src:#010x}")
    print(f"literal refs to {target:#x}:")
    for (src,) in con.execute("SELECT src FROM arefs WHERE target=?", (target,)):
        print(f"  {src:#010x}")


def cmd_near(addr):
    con = _con()
    r = con.execute("SELECT addr,text FROM strings WHERE addr<=? ORDER BY addr DESC LIMIT 1", (addr,)).fetchone()
    if r:
        print(f"  prev string {r[0]:#x}: {r[1]!r}")
    r = con.execute("SELECT addr FROM funcs WHERE addr<=? ORDER BY addr DESC LIMIT 1", (addr,)).fetchone()
    if r:
        print(f"  enclosing func ~ {r[0]:#x}")


def main():
    op = sys.argv[1] if len(sys.argv) > 1 else ""
    if op == "build":
        build()
    elif op == "str":
        cmd_str(sys.argv[2])
    elif op == "xref":
        cmd_xref(int(sys.argv[2], 16))
    elif op == "near":
        cmd_near(int(sys.argv[2], 16))
    else:
        print(__doc__)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
