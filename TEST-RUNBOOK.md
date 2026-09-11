# Live test runbook (fp Ver.5.02)

Fast path for a hands-on session. fp Ver.5.02 ONLY. Everything is RAM; a
battery-out cold boot restores stock. Do not record anything you cannot lose.

## 0. Before you plug in
- Confirm only one shell daemon will run: `pgrep -af fpshd` should be empty.
  If not: `pkill -9 -f fpshd` (all current ones are stale unless a take is live).
- Have a FAST USB-C SSD ready for any high-fps or open-gate recording.

## A. Boot-and-shoot cards (no computer needed during the take)
Copy one file from `autoruns/` to the card root as `AutoRun.txt`, cold boot,
select the preset, switch mode away/back once to re-latch, then shoot.

- `AutoRun-opengate.txt` — open gate 3:2 (files correct; monitor greens; has shell).
- `AutoRun-opengate-greenfix.txt` — open gate + standby green fix. Live view should
  be clean 3:2; the RECORD monitor may still band (that is the open problem below).
- `AutoRun-fhd120.txt` / `AutoRun-2k120.txt` — high-fps RAW; SSD required; verify the
  files (these are unproven for cadence).

Green fix engages only if this camera's display object matches the FP3K
fingerprint. If the preview does not change, that is the safe no-op, not a crash;
tell me and we read the fingerprint live (step C).

## B. Live shell session (to actually crack the record-monitor green)
1. Plug in with the camera OFF, then power on. Attach USB after the `fp` banner if
   using a shell card (`AutoRun-opengate.txt` includes the shell).
2. Start one daemon (needs sudo for USB):
   `sudo ./host/fpshd --socket /tmp/fpshd.sock &`
3. Sanity: `./host/fpsh ping` -> `pong`.

## C. Crack the record-monitor green (the real target)
The standby accessor `0xC0437E98` is handled; the record monitor reads
`[0xC375D840+0x0c]` and its buffer is allocated 16:9 at record start, so a
per-frame poke reverts. Plan: prove the field, then find the record-start input.

1. Open gate on: select the open-gate preset (or run `toggle_opengate.py on` if the
   gated payload is installed). Re-latch.
2. `./ greenprobe.py chain` (via the venv) — confirms node selector 175 and shows
   the stale 1936x1090 pair in the node+0x5c sub-object.
3. `greenprobe.py watch 40` while starting a short record — confirms it recomputes.
4. Key experiment: in STANDBY run `greenprobe.py set 3032 2012`, THEN start a take,
   THEN `greenprobe.py chain`. If the monitor comes out 3:2, the field is read at
   record-start and we can hook the record-start build. If it reverts to 1936x1090
   and still greens, the allocation ignores this field and we trace the buffer
   allocator (MovSigProcess `0xC0428AE0` / YUV builder `0xC042A570`) live instead.
5. `greenprobe.py restore` after, then power-cycle to stock.

Report each result (what the monitor looked like, the `chain` before/after). That
tells me exactly where to place the record-start hook, which is the missing piece.

## D. High-fps sanity (optional)
Boot `AutoRun-fhd120.txt` alone (no shell, no open gate), record to SSD, then read
the .DNG FrameRate tag + count on a computer to see if it truly ran ~120.

## Recovery
Remove `AutoRun.txt` from the card and cold boot with the battery out ~10s.
If the shell wedges: unplug USB, `pkill -9 -f fpshd`, power-cycle the camera.
