#!/usr/bin/env python3
"""Write index.json: every cart this repository offers, and how to install it.

    python3 scripts/make_index.py          # after scripts/build.py <id>

For each carts/<id>/, the entry is cart.json (name, version, release tag,
licence, external files) and manifest.json (runtime, memory), plus what
scripts/build.py recorded in build/dist/<id>/build.json: the cart asset's
size and sha256 and every file in it, the source bundle's size and sha256,
and the pins the build used. A cart with
no build.json keeps the entry index.json already has, so rebuilding one cart
leaves the others as they are.

Paths inside this repository (the licence texts) are written relative to
index.json, which is how GitHub Pages serves them beside it; everything else
is an absolute URL. install.py reads this file; scripts/validate.py holds it
to the repository and to the releases.
"""

import hashlib
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CARTS = os.path.join(ROOT, "carts")
INDEX = os.path.join(ROOT, "index.json")
DIST = os.path.join(ROOT, "build", "dist")
REPO_URL = "https://github.com/moybyte-org/gpl-carts"

SPDX_NAMES = {"GPL-2.0-or-later": "GNU General Public License v2.0 or later"}


def load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def repo_file(cart_id, rel, name):
    """A licence reference: a file in carts/<id>/, by URL, size and sha256."""
    path = os.path.join(CARTS, cart_id, *rel.split("/"))
    with open(path, "rb") as f:
        data = f.read()
    return {"name": name, "url": "carts/%s/%s" % (cart_id, rel), "size": len(data),
            "sha256": hashlib.sha256(data).hexdigest()}


def entry(cart_id, built):
    meta = load(os.path.join(CARTS, cart_id, "cart.json"))
    manifest = load(os.path.join(CARTS, cart_id, "manifest.json"))
    licence = repo_file(cart_id, meta["licence"]["file"],
                        SPDX_NAMES.get(meta["licence"]["spdx"], meta["licence"]["spdx"]))
    licence = dict({"spdx": meta["licence"]["spdx"]}, **licence)
    asset = built["asset"]
    release = "%s/releases/tag/%s" % (REPO_URL, meta["release"])
    return {
        "id": meta["id"],
        "name": meta["name"],
        "version": meta["version"],
        "folder": meta["folder"],
        "runtime": manifest["runtime"],
        "memory": manifest["memory"],
        "chips": meta["chips"],
        "licence": licence,
        "source": built["source"],
        "release": release,
        "assets": [{
            "name": asset["name"],
            "url": "%s/releases/download/%s/%s" % (REPO_URL, meta["release"], asset["name"]),
            "size": asset["size"],
            "sha256": asset["sha256"],
            "files": built["files"],
        }],
        "source_asset": {
            "name": built["source_asset"]["name"],
            "url": "%s/releases/download/%s/%s" % (REPO_URL, meta["release"],
                                                   built["source_asset"]["name"]),
            "size": built["source_asset"]["size"],
            "sha256": built["source_asset"]["sha256"],
        },
        "external": [{
            "path": ext["path"],
            "size": ext["size"],
            "sha256": ext["sha256"],
            "licence": repo_file(cart_id, ext["licence"]["file"], ext["licence"]["name"]),
            "archive": ext["archive"],
        } for ext in meta.get("external", [])],
        "build": {"commit": built["commit"], "keys": built["keys"], "pins": built["pins"]},
    }


def main():
    old = {}
    if os.path.isfile(INDEX):
        old = {c["id"]: c for c in load(INDEX).get("carts", [])}
    carts = []
    for cart_id in sorted(os.listdir(CARTS)):
        if not os.path.isfile(os.path.join(CARTS, cart_id, "cart.json")):
            continue
        built = os.path.join(DIST, cart_id, "build.json")
        if os.path.isfile(built):
            carts.append(entry(cart_id, load(built)))
            print("%s: from %s" % (cart_id, os.path.relpath(built, ROOT)))
        elif cart_id in old:
            carts.append(old[cart_id])
            print("%s: kept (not built here)" % cart_id)
        else:
            print("%s: not built and not in the index; left out" % cart_id)
    index = {
        "version": 1,
        "name": "Moybyte GPL carts",
        "home": REPO_URL,
        "carts": carts,
    }
    with open(INDEX, "w", encoding="utf-8", newline="\n") as f:
        json.dump(index, f, indent=2)
        f.write("\n")
    print("wrote %s: %d cart(s)" % (os.path.relpath(INDEX, ROOT), len(carts)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
