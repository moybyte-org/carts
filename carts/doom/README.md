# carts/doom -- Doom for Moybyte consoles

id Software's DOOM, in ozkl's portable
[doomgeneric](https://github.com/ozkl/doomgeneric), as a compiled
(`"runtime": "wasm"`) Moybyte cart on the console's own imports. Licence:
GPL-2.0-or-later ([NOTICE](NOTICE)). The shareware `doom1.wad` is never hosted
here; `install.py` fetches it from Debian ([id's licence](licenses/doom1.wad.txt)).

| file | what |
|---|---|
| `cart.json` | what `index.json` says about the cart: its release tag, chips, licence, and the WAD as an external file (Debian's tarball, the member, both sha256) |
| `manifest.json`, `config.json` | copied into the cart byte for byte; `config.json`'s `zone` is Doom's `-mb` in whole MiB and `args` more of Doom's own flags (`-warp 1 3`), read at `_init` |
| `dg_cart.c` | the glue: doomgeneric's platform half on the `"moy"` imports |
| `recipe.py` | fetches doomgeneric at a pinned commit, stages it with the glue, patches the seams, links `main.wasm`; `scripts/build.py` runs it |
| `NOTICE`, `licenses/` | the cart's licence statement, and id's licence text for the WAD |

**The glue.** The console keeps the clock (`time`), the input (`btn`, `touch`,
`key`), the WAD (the cart's own `read`) and the screen (`blit`, with Doom's
256-entry palette; the 320 x 200 frame letterboxed in the 320 x 240 canvas).
`_update` turns input into key events and runs the game tics `time()` says are
due; `_draw` renders and blits. Nothing sleeps or waits. wasi-libc's calls
land in the glue: stderr keeps a fatal `I_Error`'s line (written to `pmem`),
`exit(0)` is `quit()`, any other exit traps, and there are no files but the
WAD. `pmem` also carries the frame CRC at every 500th gametic, which is how
Moybyte's tests hold a board's frames to the host's.

**The seams `recipe.py` patches**, each anchored so a moved anchor fails the
build, and each patched file carrying a notice of the change at its end: the
IWAD search answers for `doom1.wad`; `DG_ScreenBuffer` is the cart's frame;
the wait in `TryRunTics` is a return; each game tic calls `DG_AfterTic`;
`D_Display` begins the screen melt and steps it once a frame.

**The memory** the manifest declares is derived and checked by every build:
the module's 64 KB stack and its static data, the 1 MiB zone, and 64 KB for
Doom's other allocations, rounded up to 64 KiB pages -- 26.
