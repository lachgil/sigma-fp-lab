# Host tools

Everything here runs on your computer, not the camera. Nothing in this folder
needs a camera unless it says so. Run them from the repository root:

```sh
.venv/bin/python tools/<script>.py
```

## Building cards

| Script | What it does |
|---|---|
| `build_combined_card.py` | The full menu card. `--debug` keeps the USB shell in |
| `build_gated_autorun.py` | The selector-gated record hook alone |
| `build_modeswap_autorun.py` | Repoint a picker slot to any of the 70 sensor modes |
| `build_highfps_autorun.py` | Picker-only high-framerate experiments |
| `build_greenfix_autorun.py`, `build_opengate_greenfix_autorun.py` | The standby display fix, alone or with open gate |
| `publish_card.sh` | Build, run the offline checks, copy to `FP_CARD_DEST` |

## Checking work offline

| Script | What it does |
|---|---|
| `emulate_hook.py` | Runs the real geometry hook in an ARM emulator |
| `emulate_menu.py` | Runs the packaged menu binary the same way |
| `frame_access_probe.py` | Exercises the firmware's own image-access code paths |
| `inspect_firmware.py` | Extracts MAIN from your own firmware copy |
| `fwmap.py`, `af_dis.py` | Cross-references and disassembly |
| `inspect_preview.py`, `regiondiff.py` | Read saved captures, compare regions |

## Talking to a live camera

These need the fpSup USB shell (a debug card) and its daemon running. They
change camera state, so read what they do first.

| Script | What it does |
|---|---|
| `hist_deploy.py` | Places and runs the histogram overlay. See [docs/overlay/](../docs/overlay/) |
| `toggle_opengate.py` | Host-side control of an installed open-gate payload |
| `greenprobe.py` | Follows the live geometry chain while the camera runs |
| `menu_text.py`, `menu_row.py` | Bounded edits to live menu text and rows |
| `memread.py`, `snapshot.py`, `sigma_test.py` | Older diagnostics. Host pointer walks can race; their output is not an atomic capture |

Offline tests for these live in [`tests/`](../tests/):

```sh
.venv/bin/python -m unittest discover -s tests
```
