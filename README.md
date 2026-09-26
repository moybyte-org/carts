# Moybyte GPL carts

Carts for [Moybyte](https://github.com/moybyte-org/moybyte) consoles whose
source is under the GNU General Public License, each built reproducibly from
that source and offered as a download with everything needed to rebuild it.

They are kept apart from the firmware on purpose. Moybyte's firmware and its
own carts are under their own licence; these carts are GPL, so they live in
their own repository, with their own licence and their own releases. They
are **free** -- free of charge and free software -- and **not tied to any
purchase**: nobody sells them, no board or account is needed to download
them, and nothing here is **ever preloaded** on a board. If you want one, you
install it yourself.

| cart | runtime | memory | licence | needs |
|---|---|---|---|---|
| [Doom](carts/doom/) | `wasm` | 26 pages (1.6 MiB) | GPL-2.0-or-later | `doom1.wad`, which the installer fetches from Debian |

`index.json` lists every cart, its release download, its complete source as
one file, and the external files it needs, each with its size and sha256. GitHub Pages serves it at
<https://moybyte-org.github.io/gpl-carts/index.json>.

## Installing

You need Python 3.8 or newer; nothing else.

```bash
python3 install.py --list
python3 install.py doom /media/you/SDCARD/moybyte/carts
```

The installer reads the index, prints the cart's licence, downloads the cart
from its release, then prints the licence of each external file -- for Doom,
id Software's licence for `doom1.wad` -- and asks before fetching it (`--yes`
accepts without asking). It checks every file against the sha256 the index
gives and refuses on any mismatch. Nothing is written into the destination
until every check has passed; the result is one complete cart folder,
`doom.moy`, with `doom1.wad` in it. `--force` replaces an installed copy, and
`--cache DIR` keeps the cart and the WAD's archive, so installing to a second
card downloads them only once.

Where to put it: a Moybyte board reads carts from its SD card's
`moybyte/carts` folder. On a board whose carts live in internal flash (the
Waveshare ESP32-P4 7B), install into a folder on your computer and copy the
cart across with Moybyte's `tools/push_cart.py`.

Without the installer: download `doom.moy.zip` from the
[release](https://github.com/moybyte-org/gpl-carts/releases), check it
against `SHA256SUMS`, and unzip it into the carts folder. Then take
`doom-wad-shareware-1.9.fixed/doom1.wad` out of Debian's
[`doom-wad-shareware_1.9.fixed.orig.tar.gz`](https://deb.debian.org/debian/pool/non-free/d/doom-wad-shareware/doom-wad-shareware_1.9.fixed.orig.tar.gz),
check its sha256 against the one in `index.json`, and put it in `doom.moy/`.

## What Doom needs

- **A Moybyte console with the compiled-cart tier** (`"runtime": "wasm"`) on
  an ESP32-S3 or ESP32-P4. The cart carries one compiled module per chip,
  signed with the key Moybyte signs its firmware updates with. Each module is
  keyed to the runtime it was compiled for: a board whose firmware carries a
  different runtime build refuses the module. `index.json` names the runtime
  commit under `build.pins["wamr-fork"]`.
- **Memory**: 26 pages of linear memory (1.6 MiB) plus the module's code,
  about 1 MB, in PSRAM. A board that cannot fit it shows a notice when you
  launch it instead of starting the game. On 2026-09-26 the Waveshare
  ESP32-P4 7B ran it from its launcher; the board-by-board fit is tracked in
  [moybyte#158](https://github.com/moybyte-org/moybyte/issues/158).
- **Storage**: about 2.5 MB for the cart and 4.2 MB for `doom1.wad`.
- **Controls**: the d-pad walks and turns, `a` fires (and selects in a menu),
  `b` uses (and backs out of a menu), `run` opens the menu. On a board with
  no buttons, the touch screen is a 3 x 3 pad: the top row walks forward, the
  middle row turns and, in the centre, fires, and the bottom row uses, walks
  back and opens the menu. On a keyboard, `,` and `.` strafe. The game is
  silent.

## The WAD, in plain words

Doom's engine is free software; its game data -- the levels, pictures and
sounds -- is not. The shareware episode, `doom1.wad`, belongs to id Software
and comes under id's Limited Use Software License
([full text](carts/doom/licenses/doom1.wad.txt)). In plain words:

- you may make copies and give them to other people;
- nobody may charge for it or receive anything in return for it;
- nobody may modify it or make derivative works from it.

That is why this repository never hosts the WAD, not in the tree and not in a
release. The installer fetches it straight from Debian's archive, which has
carried it for many years, checks it by sha256, and shows you id's licence
before it downloads anything.

## Building a cart

```bash
python3 scripts/build.py doom --twice      # build twice; every byte must match
python3 scripts/make_index.py              # index.json from the build
```

The build runs on Linux x86-64 with Python 3.8+ and fetches everything it
needs by pinned hash into `build/` (gitignored): the engine at a pinned
commit, wasi-sdk 24, and the per-chip compilers. The tools that compile, key
and sign the modules come from the
[moybyte](https://github.com/moybyte-org/moybyte) repository at the commit
pinned in `scripts/build.py`, read from a local checkout's history or from
GitHub, never from a working tree; they are MIT. Moybyte's
`device/moy_ota.py`, which is not, is read only to check the signatures
after the build. Signing the modules needs Moybyte's update-signing key and
the `cryptography` package, so only the maintainers make release builds.

**Every release carries its complete source as one file**, beside the cart:
`doom-v1-source.tar.gz` holds every file the build compiled, with the recipe's
patches applied and a notice of each change at the end of each patched file,
the upstream licence, `carts/doom/`, `scripts/build.py`, `LICENSE`, and
Moybyte's module build path under `moybyte/` (MIT, plus two generated files
naming the runtime the modules are keyed for). Its `README.txt` says which
files are GPL, which MIT and which data, and gives the commands that make
`main.wasm` and each per-chip module, up to its signature, from the bundle
alone -- so the source stays available even if an upstream repository goes
away. Its bytes are deterministic: sorted entries, a fixed time, owner and
mode, and no name or time in the gzip header.

Anyone can check that a release is built from this source, without the key:

```bash
git checkout <the commit SOURCE.txt names>
python3 scripts/build.py doom --verify-release doom.moy.zip doom-v1-source.tar.gz
```

That builds the modules unsigned and requires every file in the cart to be
identical, each module to be this build's module plus a signature the
firmware trusts, and the source bundle's tar to be identical. Each built cart
carries `SOURCE.txt`, which names the source bundle and every pin its build
used: this repository's commit, the engine's commit and tree hash, the
moybyte tools' commit, wasi-sdk, the compilers and the runtime commit.

`scripts/validate.py` is what CI runs on every push and pull request. It builds
nothing and needs no secret: it checks `index.json`'s schema, every manifest,
that each hosted asset in the index matches its release byte for byte, that
each source bundle holds this repository's files at the build commit and
exactly the moybyte files the build pinned, and that no WAD or key has ever
been committed.

## Licences

This repository's own licence is the GNU General Public License, version 2
([LICENSE](LICENSE)). **Each cart folder states its own licence** -- in
`carts/<id>/NOTICE`, with the texts it refers to beside it -- and a built cart
carries its licences in `LICENSES.txt`. Doom is GPL-2.0-or-later, and its WAD
is id Software's, under the terms above. A source bundle also carries
Moybyte's MIT module tools, with the MIT text, under `moybyte/`.
