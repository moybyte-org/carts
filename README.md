# Moybyte carts

Carts for [Moybyte](https://github.com/moybyte-org/moybyte) consoles, compiled
("runtime": "wasm") carts built by recipe. They're free, and never come
preinstalled on a console.

**Each cart is under the licence in its own folder**, `carts/<id>/`, which
its index entry names too. The carts here are under different licences, side
by side: don't assume one cart's licence from another's. The tooling around
them -- `scripts/`, `install.py`, `tests/` and the workflows -- is MIT
([LICENSE](LICENSE)). CI holds every cart to its own licence file and keeps
source from passing between carts.

## Install

With [moy](https://github.com/moybyte-org/moy-spec/releases/tag/player-latest):

```
moy install --index https://moybyte-org.github.io/carts/index.json --list
moy install --index https://moybyte-org.github.io/carts/index.json doom /path/to/sdcard/moybyte/carts
```

From a checkout of this repository, `python3 install.py doom /path/to/sdcard/moybyte/carts`
does the same (it runs moy's installer with this index). The installer
downloads the cart, checks every file against its hash, and writes a ready
cart folder. If a cart needs extra files, it shows you their licence and asks
before downloading them. For a board without an SD card, install to a folder
on your computer and copy the cart over with Moybyte's `tools/push_cart.py`.

A Moybyte console installs from the same index with its Get Carts app. A
browser cannot read a GitHub release download (it sends no CORS header), so
the index also names a **mirror** of each release asset on this repository's
Pages site, `releases/<tag>/<asset>`, which a browser console reads instead.
The release stays the canonical copy.

## Carts

| | cart | licence | notes |
|---|---|---|---|
| ![Doom](carts/doom/cover.png) | [Doom](carts/doom/) | GPL-2.0-or-later ([NOTICE](carts/doom/NOTICE)) | The shareware episode, with saved games. Needs about 3.3 MB of free PSRAM, so some boards run it and others show a notice. |
| ![ESP 88](carts/esp88/cover.png) | [ESP 88](carts/esp88/) | MIT ([LICENSES.txt](carts/esp88/LICENSES.txt)) | A two-minute neon city film in twelve cuts, played in a loop. Needs about 2.7 MB, so a board that cannot fit it shows a notice. |
| ![Jet Teapot](carts/teapot/cover.png) | [Jet Teapot](carts/teapot/) | MIT ([LICENSES.txt](carts/teapot/LICENSES.txt)) | The Utah teapot, lit and depth-buffered, with the camera yours. Runs on every console board. |

Each picture is the cart's `cover.png`, the one a console's store and shelf
show (moy-spec's SPEC.md 3.6). The index points at it on this site, so a new
cover shows without a new release; the next release carries it in the cart too.
Doom's is lettering drawn by [`carts/doom/cover.py`](carts/doom/cover.py), not
a frame of the game: every frame shows the WAD's art, which is id Software's.

## Doom's game data

Doom plays id Software's shareware `doom1.wad` (DOOM v1.9), under id's
[licence](carts/doom/licenses/doom1.wad.txt): anyone may copy it and give it
to others, unmodified, and nobody may charge or receive anything for it. So
this repository's site hosts it **free, unmodified, beside that licence**:
`files/doom/doom1.wad`, with the licence at `files/doom/doom1.wad.txt`. It is
never preloaded on a console, never seeded into one, and never sold.

The WAD never enters git (`scripts/validate.py` checks the whole history) and
is not in any release. The Pages deploy downloads Debian's
`doom-wad-shareware` archive, checks it, takes the WAD out and checks that,
and publishes it with the licence beside it (`scripts/pages.py`). Doom's
index entry names both the copy here (its `mirror`) and Debian's archive: an
installer takes the file from here, and from Debian's archive when it cannot.

## Credits

Doom is id Software's. This port is built on ozkl's
[doomgeneric](https://github.com/ozkl/doomgeneric).

Jet, JetExamples and the scenes the Jet carts port are by
[CubeCoders](https://github.com/CubeCoders/Jet) (PhonicUK).

## Source and licences

A cart is built by `scripts/build.py`, one build for every cart: it runs the
cart's `recipe.py` (Doom's fetches doomgeneric at a pinned commit and patches
it; the Jet carts' run Moybyte's `tools/jet_cart.py` at a pinned moybyte
commit, through `scripts/jet_recipe.py`), compiles a module per chip and packs
the release: the cart, and its complete source as one archive, which says
whose each part is. Moybyte keeps a stamped copy of the Jet carts' folders for
its own tests and on-glass guards (its `ports/jet/`); this repository is where
the carts change.

```
python3 scripts/build.py doom --unsigned -o ~/doom-build
python3 install.py doom /path/to/sdcard/moybyte/carts --build ~/doom-build
python3 scripts/build.py doom --verify-release doom.moy.zip doom-v4-source.tar.gz
```

`--verify-release` rebuilds the cart from the checkout it runs in, so check a
release from the commit it was built from (the index's `build.commit`).

Only maintainers can sign a release. A cart you built yourself is unsigned, so
turn on Unknown sources in the console's Settings to run it.

The Pages site -- `index.json`, the covers, the licence texts and the
mirrors -- is deployed by `.github/workflows/pages.yml` once `validate` has
passed on the default branch or on a published release. `scripts/pages.py`
downloads each release asset the index names and checks it against the
index's size and sha256 before the site is built, and publishes Doom's WAD as
above; an index ahead of its release leaves the site as it was until the
release is published. `scripts/make_index.py` writes the index, mirrors
included.
