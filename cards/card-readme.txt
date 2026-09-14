SIGMA fp 5.02 ONLY (not fp L). @BUILD@ built @DATE@.

Copy AutoRun.txt and VSHL.BIN to the ROOT of the SD card, with a card reader.
Back up whatever is there first. Then: battery out and back in, card in, boot
with USB unplugged. Screen reads @BANNER@ when loaded.

The two files are a MATCHED PAIR. The menu code lives in VSHL.BIN and
AutoRun.txt only loads it, so never mix files from different builds.

RIGHT cycles, UP toggles. Everything boots OFF.
  STOCK      all features off
  OPEN GATE  M117  3032x2012  full sensor area, 2x2 binned   274 MB/s
  M98 30P    M98   3032x2012  same framing, slower readout   274 MB/s
  M130       M130  3968x2640  FULL 1:1, 1.53x crop, follows
                              the preset you select
  M130 FAST  M130  3968x2640  as above, rolling shutter
                              16.3 -> 12.1 ms (experimental)
  M98 60P    M98   3032x2012  open-gate framing at 59.94     548 MB/s
  M6 4K      M6    4176x2174  14-bit full readout            476 MB/s
  GYRO       .gcsv + .json beside CinemaDNG clips
  GYRO-GATE  open gate + gyro together
  SEL        read-only: SEL=selector C=cells changed L=cells on M130
  FALSE COL  false colour as a LATCH, no button to hold

FALSE COL is the one row that changes no geometry: turn it on and off freely,
no preset re-latch needed. It is also the newest row and the least tested.

M130: SELECT THE PRESET FIRST, then turn it on. It reads the selector the
camera is using and repoints that rate. 29.97 needs about 471 MB/s and stopped
near 5 s on test; 23.976 is 377 MB/s. If it says SEL=xx RATE UNKNOWN, that
framerate/bit-depth combination has not been measured yet -- note the two hex
digits and it can be added. Use 12-bit for now: bit depth changes the selector.

Open Gate and M98 30P are FHD 29.97; M98 60P is FHD 59.94. After toggling
anything, switch the recording preset away and back before rolling. Anything
over about 400 MB/s wants the SSD, attached BEFORE power-on.

To go back to stock: remove both files from the card, battery out/in.
Nothing is flashed; every change is RAM only.

AutoRun.txt sha256 @AUTORUN_SHA@
VSHL.BIN    sha256 @VSHL_SHA@
