#!/usr/bin/env bash
# Build the combined card, run the offline checks, then publish to the NAS.
# Nothing is published unless the build and both emulations pass.
#
#   ./publish_card.sh                 # build, verify, publish
#   ./publish_card.sh --og60-sel 0    # any build_combined_card.py argument
set -euo pipefail
cd "$(dirname "$0")"

NAS="${FP_CARD_DEST:-/run/user/1000/gvfs/smb-share:server=192.168.1.40,share=media/fp/card}"
PY=.venv/bin/python
OUT=builds/combined-menu

$PY build_combined_card.py "$@"
$PY emulate_hook.py > /dev/null
$PY emulate_menu.py > /dev/null
echo "checks   : geometry hook + packaged menu binary pass"

# The share is gvfs, so it can be absent without the mount failing loudly.
if [ ! -d "$(dirname "$NAS")" ]; then
    echo "dest     : $NAS not mounted -- open the share in the file manager first" >&2
    exit 1
fi
mkdir -p "$NAS"

# Written in place, so a half-copied VSHL.BIN beside a new AutoRun.txt is the
# failure to avoid: copy to a temp name on the same share, then move.
for f in AutoRun.txt VSHL.BIN manifest.json; do
    cp "$OUT/$f" "$NAS/.$f.new"
    mv "$NAS/.$f.new" "$NAS/$f"
done
sync

for f in AutoRun.txt VSHL.BIN; do
    a=$(sha256sum "$OUT/$f" | cut -d' ' -f1)
    b=$(sha256sum "$NAS/$f" | cut -d' ' -f1)
    if [ "$a" != "$b" ]; then
        echo "verify   : $f differs on the share -- do NOT copy it to the card" >&2
        exit 1
    fi
    echo "published: $f  ${a:0:16}"
done

{
    echo "SIGMA fp 5.02 ONLY (not fp L). Built $(date '+%Y-%m-%d %H:%M')."
    echo
    echo "Copy AutoRun.txt and VSHL.BIN to the ROOT of the SD card, with a card"
    echo "reader. Back up whatever is there first. Then: battery out and back in,"
    echo "card in, boot with USB unplugged. Screen reads fpLAB MENU when loaded."
    echo
    echo "RIGHT cycles, UP toggles. Everything boots OFF."
    echo "  STOCK      all features off"
    echo "  OPEN GATE  M117  3032x2012  full sensor area, 2x2 binned   274 MB/s"
    echo "  M98 30P    M98   3032x2012  same framing, slower readout   274 MB/s"
    echo "  M130       M130  3968x2640  FULL 1:1, 1.53x crop, follows"
    echo "                              the preset you select"
    echo "  M130 FAST  M130  3968x2640  as above, rolling shutter"
    echo "                              16.3 -> 12.1 ms (experimental)"
    echo "  M98 60P    M98   3032x2012  open-gate framing at 59.94     548 MB/s"
    echo "  M6 4K      M6    4176x2174  14-bit full readout            476 MB/s"
    echo "  GYRO       .gcsv + .json beside CinemaDNG clips"
    echo "  GYRO-GATE  open gate + gyro together"
    echo "  SEL        read-only: SEL=selector C=cells changed L=cells on M130"
    echo
    echo "M130: SELECT THE PRESET FIRST, then turn it on. It reads the selector"
    echo "the camera is using and repoints that rate. 29.97 needs about 471"
    echo "MB/s and stopped near 5 s on test; 23.976 is 377 MB/s. If it says"
    echo "SEL=xx RATE UNKNOWN, that framerate/bit-depth combination has not"
    echo "been measured yet -- note the two hex digits and it can be added."
    echo "Use 12-bit for now: bit depth changes the selector."
    echo
    echo "Open Gate and M98 30P are FHD 29.97; M98 60P is FHD 59.94. After"
    echo "toggling anything, switch the recording preset away and back before"
    echo "rolling. Anything over about 400 MB/s wants the SSD, attached BEFORE"
    echo "power-on."
    echo
    echo "To go back to stock: remove both files from the card, battery out/in."
    echo "Nothing is flashed; every change is RAM only."
    echo
    echo "AutoRun.txt sha256 $(sha256sum "$OUT/AutoRun.txt" | cut -d' ' -f1)"
    echo "VSHL.BIN    sha256 $(sha256sum "$OUT/VSHL.BIN" | cut -d' ' -f1)"
} > "$NAS/README.txt"
sync
echo "dest     : $NAS (README.txt written)"
