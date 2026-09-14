# Documentation

Grouped by the part of the camera it is about. Start with the area you care
about; each folder has its own index.

| Folder | What is in it |
|---|---|
| [menu/](menu/) | The card's menu, and the camera's own native menu system |
| [modes/](modes/) | Sensor modes, timing, what recorded and what refused |
| [overlay/](overlay/) | Reading the live image and drawing on the screen |
| [display/](display/) | The green preview, and the playback darkness experiment |
| [research/](research/) | Open leads, firmware notes, dated raw observations |

Two files sit at this level because they cut across everything:

- [status.md](status.md) - the current state of play, and how sure each claim is
- [test-runbook.md](test-runbook.md) - the live-camera procedure, in order

## How claims are written here

Every non-obvious statement says how it is known. **Measured** means it was
reproduced on the camera or read out of the firmware image. **Seen once** means
exactly that, and is treated as something to be careful around rather than a
fact. Anything else is a lead. Failures are kept next to successes, because the
failure signature is usually what identifies the next problem.
