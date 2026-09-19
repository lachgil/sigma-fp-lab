SIGMA fp 5.02 ONLY (not fp L). @BUILD@ built @DATE@.

Copy AutoRun.txt and VSHL.BIN to the ROOT of the SD card, with a card reader.
Back up whatever is there first. Then: battery out and back in, card in, boot
with USB unplugged. Screen reads @BANNER@ when loaded.

The two files are a MATCHED PAIR. The menu code lives in VSHL.BIN and
AutoRun.txt only loads it, so never mix files from different builds.

FIRST: make sure UP and RIGHT are UNMAPPED (Menu -> Custom -> button settings).
This card uses both keys while you are in live view, so anything assigned to
them will not fire. In the camera's own menu and in playback they stay the
camera's, so you navigate normally there.

The modes here are not our discovery: the loader, the USB shell, the gyro build
and the sensor mode table all come from ijigen/fpSup
(github.com/ijigen/fpSup). The standby green fix builds on FP3K's display
accessor. This card is a menu around their work.

RIGHT cycles, UP toggles. Everything boots OFF. The panel is drawn top right;
there is no key to open it, the keys are ours whenever the card is running.
It hides itself after about four seconds of no changes and comes back on the
next key. Turning an option on now also sets the framerate that option needs,
which is what makes the camera adopt it -- you should no longer have to switch
the preset away and back by hand.
ON THE RELEASE CARD (four rows):
  STOCK      all features off
  M130       M130  3968x2640  FULL 1:1, 1.53x crop, follows
                              the preset you select
  OPEN GATE  M117  3032x2012  full sensor area, 2x2 binned   274 MB/s
  FHD 120    M58   1920x1080  12-bit, 120 fps, a burst only  389 MB/s

ONLY ON THE DEBUG CARD (experiments, dead ends and diagnostics):
  GREEN FIX  3:2 standby preview, so OPEN GATE stops greening. Ported from
             FP3K and NEVER RUN ON A CAMERA, which is why it is not on the
             release card. Shoot open gate from the debug card if the green
             standby preview is in your way.
  M98 30P    M98   3032x2012  open-gate framing, slower readout
  M130 FAST  M130  3968x2640  rolling shutter 16.3 -> 12.1 ms
  M98 60P    M98   3032x2012  open-gate framing at 59.94      548 MB/s
  M6 4K      M6    4176x2174  14-bit full readout             476 MB/s
  2K120      M56   2016x1344  at 120 instead of 60   SSD, UNTESTED cadence
  672 240    M12   2016x672   at 240 instead of 60   SSD, UNTESTED cadence
  2088 120   M103  2088x1174  at 120 instead of 60   SSD, UNTESTED cadence
  FALSE COL  false colour as a LATCH, no button to hold
  GYRO       .gcsv + .json beside CinemaDNG clips
  GYRO-GATE  open gate + gyro together
  SEL        read-only: SEL=selector C=cells changed L=cells on M130

If open gate is what you are here for, ijigen/fpSup's own OG3K v0.2.2a is a
much more complete implementation (eight rates, 3024x2010, in-camera playback,
correct ISO headroom, shutter angle, 8/10/12-bit). Nothing here is a port of
it. Ours is the original FHD-slot M117 swap with a menu around it.

M130: SELECT THE PRESET FIRST, then turn it on. It reads the selector the
camera is using and repoints that rate. 29.97 needs about 471 MB/s and stopped
near 5 s on test; 23.976 is 377 MB/s. If it says SEL=xx RATE UNKNOWN, that
framerate/bit-depth combination has not been measured yet -- note the two hex
digits and it can be added. Use 12-bit for now: bit depth changes the selector.

Open Gate and M98 30P are FHD 29.97; M98 60P is FHD 59.94. The card now selects
the right rate for you when you switch an option on; if a take still looks like
the old mode, switch the preset away and back once and tell us. Anything over
about 400 MB/s wants the SSD, attached BEFORE power-on.

To go back to stock: remove both files from the card, battery out/in.
Nothing is flashed; every change is RAM only.

AutoRun.txt sha256 @AUTORUN_SHA@
VSHL.BIN    sha256 @VSHL_SHA@
