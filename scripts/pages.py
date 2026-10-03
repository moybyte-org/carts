#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Nikola Jovicic
"""Put a checked copy of every release asset index.json names at its mirror,
and of every external file it names a mirror for, for the Pages site: the
step .github/workflows/pages.yml runs before it builds the site.

    python3 scripts/pages.py

A browser cannot read a GitHub release download, which sends no CORS header,
and can read this repository's Pages site, which answers every file with
`Access-Control-Allow-Origin: *`. So the index names a second copy of each
release asset, its "mirror" (moy-spec's cartindex.py says how the field
works), and a browser console fetches the cart from there. This writes those
copies into the checkout at releases/<tag>/<asset>, and the site build
publishes them with everything else. The release stays the canonical copy.

Each asset is downloaded from its release and held to the index's size and
sha256 before it is written; one that does not match is an error, and the
site is not deployed. An asset whose release is not published yet (a draft
answers 404 to everyone without write access) means the index is ahead of
its release: nothing is written, and the workflow deploys nothing, so the
site keeps the index and the mirrors it had until the release's own run.

An external file with a "mirror" (cart.json's, files/<id>/<path>) is one
whose licence lets this repository give it away -- Doom's shareware WAD, which
id's licence lets anyone copy and give to others, unmodified and for nothing.
It never enters git: this downloads its publisher's archive from the index's
urls, holds the archive to the index's size and sha256, takes the member out,
holds that to the file's own size and sha256, and writes it at its mirror,
with its licence text -- the index's, checked by sha256 -- beside it as
<mirror>.txt. The site serves it unmodified and free; nothing here sells it,
and no console comes with it. An archive no url answers for is an error, and
the site is not deployed, so it keeps the copy it had.

Writes ready=true or ready=false to $GITHUB_OUTPUT when there is one.
Python 3.8 or newer, standard library only.
"""

import io
import os
import sys
import tarfile
import urllib.error

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from validate import INDEX, ROOT, Report, fetch, load, sha256  # noqa: E402


def say_ready(ready):
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a") as f:
            f.write("ready=%s\n" % ("true" if ready else "false"))


def external_copy(cart, ext):
    """(mirror, the file's bytes, licence path, licence bytes) for an external
    file the index names a mirror for, each checked; raises ValueError."""
    mirror = ext.get("mirror")
    if not isinstance(mirror, str) or mirror != "files/%s/%s" % (cart.get("id"), ext.get("path")):
        raise ValueError("%s's %s: the mirror %r is not files/%s/%s"
                         % (cart.get("id"), ext.get("path"), mirror, cart.get("id"),
                            ext.get("path")))
    arc = ext["archive"]
    data, why = None, []
    for url in arc["urls"]:
        try:
            data = fetch(url, arc["size"])
        except (urllib.error.URLError, OSError) as exc:
            why.append("%s: %s" % (url, exc))
            continue
        if len(data) == arc["size"] and sha256(data) == arc["sha256"]:
            break
        why.append("%s is %d bytes, sha256 %s; the index says %d bytes, sha256 %s"
                   % (url, len(data), sha256(data), arc["size"], arc["sha256"]))
        data = None
    if data is None:
        raise ValueError("no archive of %s: %s" % (ext["path"], "; ".join(why)))
    if arc.get("format") != "tar.gz":
        raise ValueError("%s: archive format %r" % (ext["path"], arc.get("format")))
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        try:
            member = tar.getmember(arc["member"])
        except KeyError:
            raise ValueError("the archive has no %s" % arc["member"])
        if not member.isfile():
            raise ValueError("%s in the archive is not a file" % arc["member"])
        body = tar.extractfile(member).read()
    if len(body) != ext["size"] or sha256(body) != ext["sha256"]:
        raise ValueError("%s is %d bytes, sha256 %s; the index says %d bytes, sha256 %s"
                         % (arc["member"], len(body), sha256(body), ext["size"],
                            ext["sha256"]))
    lic = ext["licence"]
    with open(os.path.join(ROOT, *lic["url"].split("/")), "rb") as f:
        text = f.read()
    if len(text) != lic["size"] or sha256(text) != lic["sha256"]:
        raise ValueError("%s is not the licence text the index names" % lic["url"])
    return mirror, body, mirror + ".txt", text


def main():
    rep = Report()
    index = load(INDEX, rep)
    if not isinstance(index, dict):
        print("pages: %s" % "; ".join(rep.errors), file=sys.stderr)
        return 1
    waiting = []
    written = []
    for cart in index.get("carts", []):
        for asset in cart.get("assets", []):
            mirror = asset.get("mirror")
            if not isinstance(mirror, str) or not mirror.startswith("releases/") \
                    or ".." in mirror.split("/"):
                print("pages: %s's asset %s has no mirror under releases/; run "
                      "scripts/make_index.py" % (cart.get("id"), asset.get("name")),
                      file=sys.stderr)
                return 1
            try:
                data = fetch(asset["url"], asset["size"])
            except urllib.error.HTTPError as exc:
                if exc.code == 404:
                    waiting.append(asset["url"])
                    continue
                print("pages: %s: HTTP %d" % (asset["url"], exc.code), file=sys.stderr)
                return 1
            except (urllib.error.URLError, OSError) as exc:
                print("pages: %s: %s" % (asset["url"], exc), file=sys.stderr)
                return 1
            if len(data) != asset["size"] or sha256(data) != asset["sha256"]:
                print("pages: %s is %d bytes, sha256 %s; the index says %d bytes, "
                      "sha256 %s" % (asset["url"], len(data), sha256(data),
                                     asset["size"], asset["sha256"]), file=sys.stderr)
                return 1
            written.append((mirror, data))
        for ext in cart.get("external", []):
            if "mirror" not in ext:
                continue
            try:
                mirror, body, beside, text = external_copy(cart, ext)
            except (ValueError, OSError, tarfile.TarError) as exc:
                print("pages: %s" % exc, file=sys.stderr)
                return 1
            written += [(mirror, body), (beside, text)]
    if waiting:
        print("pages: not deploying -- the index names assets whose release is not "
              "published yet:\n  %s" % "\n  ".join(waiting))
        say_ready(False)
        return 0
    for mirror, data in written:
        path = os.path.join(ROOT, *mirror.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(data)
        print("pages: %s (%d bytes, sha256 %s, matches the index)"
              % (mirror, len(data), sha256(data)))
    say_ready(True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
