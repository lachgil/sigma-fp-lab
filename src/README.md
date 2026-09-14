# Camera-side code

ARM assembly that runs on the fp itself. None of it is flashed: the loader
copies it into RAM at boot, or the USB shell writes it in while the camera runs.

| File | What it is |
|---|---|
| `menu.S` | The on-camera menu: key hook, the options, and the mode swaps behind them |
| `rowpatch_gated.S` | The record-time geometry hook, gated on the selector |
| `hist_overlay.S` | Reads the live grayscale feed and draws a histogram on screen |

Each file's header comment carries the addresses it depends on and why. The
things that will freeze this camera if you get them wrong (register alignment,
saving `lr`, arming with the wrong branch, trusting a dropped `mem set`, sharing
scratch space) are written up in fpSup's `fp-usb-shell` notes; they were all
paid for at least once.

Built by the scripts in [`tools/`](../tools/), which own the addresses these
blobs land at.
