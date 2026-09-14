#!/usr/bin/env python3
"""Linux test harness for the SIGMA fp via sigma-ptpy (no Windows SDK needed).

Read camera state, capture the LIVE-VIEW frame as JPEG (to SEE artifacts as
data), and start/stop movie recording. Works with a manual lens.

Needs "Camera Control" USB mode (stock PTP); cannot run with the fpshd shell.

Usage:
  .venv/bin/python sigma_test.py info
  .venv/bin/python sigma_test.py view out.jpg
  .venv/bin/python sigma_test.py rec 3 clip
"""
import sys
import time
import collections
import collections.abc as _abc
for _n in ("Sequence", "Mapping", "MutableMapping", "Callable", "Iterable"):
    if not hasattr(collections, _n):
        setattr(collections, _n, getattr(_abc, _n))
from sigma_ptpy import SigmaPTPy


def cmd_info(cam):
    for name in ("get_cam_data_group1", "get_cam_data_group2",
                 "get_cam_data_group3", "get_cam_data_group5"):
        try:
            print(f"== {name} ==")
            print(getattr(cam, name)())
        except Exception as e:
            print(f"  ({name} failed: {e})")


def cmd_view(cam, path):
    vf = cam.get_view_frame()
    data = vf.Data
    with open(path, "wb") as f:
        f.write(data)
    print(f"wrote live-view JPEG: {path} ({len(data)} bytes)")


def cmd_rec(cam, seconds, prefix):
    from sigma_ptpy.schema import SnapCommand
    from sigma_ptpy.enum import CaptureMode
    cmd_view(cam, f"{prefix}_pre.jpg")
    cam.snap_command(SnapCommand(CaptureMode=CaptureMode.StartRecMovie))
    print(f"recording {seconds}s ...")
    mid = max(1, seconds // 2)
    time.sleep(mid)
    try:
        cmd_view(cam, f"{prefix}_mid.jpg")
    except Exception as e:
        print(f"  (mid frame failed: {e})")
    time.sleep(seconds - mid)
    cam.snap_command(SnapCommand(CaptureMode=CaptureMode.StopRecMovie))
    print("stopped.")


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    op = sys.argv[1]
    cam = SigmaPTPy(ignore_events=True)
    with cam.session():
        cam.config_api()
        if op == "info":
            cmd_info(cam)
        elif op == "view":
            cmd_view(cam, sys.argv[2] if len(sys.argv) > 2 else "view.jpg")
        elif op == "rec":
            cmd_rec(cam, int(sys.argv[2]), sys.argv[3] if len(sys.argv) > 3 else "clip")
        else:
            print(f"unknown op: {op}")
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
