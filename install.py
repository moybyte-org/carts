#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 Nikola Jovicic
"""Install a GPL cart for a Moybyte console into a carts folder.

    python3 install.py --list
    python3 install.py doom /media/me/SDCARD/moybyte/carts
    python3 install.py doom ./carts --index ./index.json --asset-dir ./downloads

Reads the index (a URL or a local file; by default the one this repository
serves on GitHub Pages), downloads the cart's release asset and every
external file the cart needs, checks each against the size and sha256 the
index gives, and writes the complete cart folder -- for Doom, doom.moy with
doom1.wad in it -- into the destination directory.

Each licence is printed before the file it covers is fetched, and an external
file (one this repository does not host, such as Doom's WAD) is fetched only
once you accept its licence: at the prompt, or with --yes.

Nothing is written into the destination until every file has been checked:
any size or hash mismatch stops the install and leaves the destination as it
was. An existing cart folder is replaced only with --force.

--asset-dir takes a release asset from a directory instead of downloading it
(it is still checked against the index); --cache keeps the release assets and
the external files' archives in a directory, by sha256, so installing to a
second card downloads them only once.

Python 3.8 or newer, standard library only.
"""

import argparse
import hashlib
import io
import json
import os
import shutil
import sys
import tarfile
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import zipfile

DEFAULT_INDEX = "https://moybyte-org.github.io/gpl-carts/index.json"
INDEX_VERSION = 1
USER_AGENT = "moybyte-gpl-carts-install/1"
RULE = "=" * 72


class InstallError(Exception):
    pass


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def is_url(ref):
    return urllib.parse.urlsplit(ref).scheme in ("http", "https")


def resolve(base, ref):
    """`ref` (a URL, or a path relative to the index) against the index's
    location `base` (a URL or a local path)."""
    if is_url(ref):
        return ref
    if is_url(base):
        return urllib.parse.urljoin(base, ref)
    if os.path.isabs(ref):
        return ref
    return os.path.join(os.path.dirname(os.path.abspath(base)), *ref.split("/"))


def download(url, limit):
    """The bytes at `url`, refusing more than `limit`."""
    print("  fetching %s" % url)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            chunks, got = [], 0
            while True:
                chunk = r.read(1 << 16)
                if not chunk:
                    break
                got += len(chunk)
                if got > limit:
                    raise InstallError("%s is larger than the index says (%d bytes)"
                                       % (url, limit))
                chunks.append(chunk)
            return b"".join(chunks)
    except urllib.error.HTTPError as exc:
        hint = ""
        if exc.code == 404 and "/releases/download/" in url:
            hint = " (the release may not be published yet)"
        raise InstallError("%s: HTTP %d%s" % (url, exc.code, hint))
    except (urllib.error.URLError, OSError) as exc:
        raise InstallError("%s: %s" % (url, getattr(exc, "reason", exc)))


def read_ref(base, ref, limit):
    """The bytes a reference names: a local file or a download."""
    where = resolve(base, ref)
    if is_url(where):
        return download(where, limit)
    try:
        with open(where, "rb") as f:
            return f.read(limit + 1)
    except OSError as exc:
        raise InstallError("%s: %s" % (where, exc))


def check(data, size, digest, what):
    if len(data) != size:
        raise InstallError("%s is %d bytes; the index says %d. Refusing it."
                           % (what, len(data), size))
    got = sha256(data)
    if got != digest:
        raise InstallError("%s hashes to %s; the index says %s. Refusing it."
                           % (what, got, digest))
    return data


def obtain(what, urls, size, digest, base, asset_dir=None, name=None, cache=None):
    """A file the index names by size and sha256: from `asset_dir`, the cache,
    or the first of `urls` that answers. Refused unless it matches."""
    if asset_dir and name:
        path = os.path.join(asset_dir, name)
        if not os.path.isfile(path):
            raise InstallError("%s is not in %s" % (name, asset_dir))
        print("  using %s" % path)
        with open(path, "rb") as f:
            return check(f.read(size + 1), size, digest, path)
    cached = os.path.join(cache, digest) if cache else None
    if cached and os.path.isfile(cached):
        print("  using the cached copy of %s" % what)
        with open(cached, "rb") as f:
            return check(f.read(size + 1), size, digest, cached)
    errors = []
    for url in urls:
        try:
            data = check(read_ref(base, url, size), size, digest, url)
        except InstallError as exc:
            errors.append(str(exc))
            continue
        if cached:
            os.makedirs(cache, exist_ok=True)
            with open(cached + ".part", "wb") as f:
                f.write(data)
            os.replace(cached + ".part", cached)
        return data
    raise InstallError("could not get %s:\n  %s" % (what, "\n  ".join(errors)))


def show_licence(base, licence, what):
    """Print the licence text the index names for `what`, checked by sha256."""
    text = read_ref(base, licence["url"], licence["size"])
    check(text, licence["size"], licence["sha256"], "the licence for %s" % what)
    print(RULE)
    print("%s -- %s" % (what, licence.get("name") or licence.get("spdx")))
    print(RULE)
    print(text.decode("utf-8").rstrip())
    print(RULE)


def accept(what, assume_yes):
    if assume_yes:
        print("Accepted with --yes: %s is fetched under the licence above." % what)
        return
    if not sys.stdin.isatty():
        raise InstallError("%s is fetched only under the licence above; run "
                           "interactively to accept it, or pass --yes" % what)
    answer = input("Fetch %s under the licence above? [y/N] " % what).strip().lower()
    if answer not in ("y", "yes"):
        raise InstallError("not accepted: %s was not fetched" % what)


def plain_name(name):
    return (bool(name) and name not in (".", "..") and "/" not in name and "\\" not in name
            and "\0" not in name and not name.startswith("."))


def load_index(ref):
    data = read_ref(ref, ref, 1 << 22)
    try:
        index = json.loads(data.decode("utf-8"))
    except ValueError as exc:
        raise InstallError("%s is not JSON: %s" % (ref, exc))
    if not isinstance(index, dict) or index.get("version") != INDEX_VERSION:
        raise InstallError("%s is not a version %d index" % (ref, INDEX_VERSION))
    return index


def unpack(data, cart, asset, staging):
    """The asset's files, each checked against the index, into `staging`."""
    folder = cart["folder"]
    files = asset["files"]
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise InstallError("%s is not a zip: %s" % (asset["name"], exc))
    with z:
        names = set(z.namelist())
        want = {"%s/%s" % (folder, fn) for fn in files}
        if names != want:
            raise InstallError("%s holds %s; the index lists %s. Refusing it."
                               % (asset["name"], sorted(names - want) or "fewer files",
                                  sorted(want - names) or "fewer files"))
        for fn in sorted(files):
            meta = files[fn]
            if z.getinfo("%s/%s" % (folder, fn)).file_size != meta["size"]:
                raise InstallError("%s/%s is not the size the index says" % (folder, fn))
            body = check(z.read("%s/%s" % (folder, fn)), meta["size"], meta["sha256"],
                         "%s/%s" % (folder, fn))
            with open(os.path.join(staging, fn), "wb") as f:
                f.write(body)


def external(base, ext, staging, cache, assume_yes):
    """An external file: its licence, then its archive, then its member."""
    path = ext["path"]
    show_licence(base, ext["licence"], path)
    accept(path, assume_yes)
    arc = ext["archive"]
    data = obtain("the archive holding %s" % path, arc["urls"], arc["size"], arc["sha256"],
                  base, cache=cache)
    if arc["format"] != "tar.gz":
        raise InstallError("%s: archive format %r is not one this installer reads"
                           % (path, arc["format"]))
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        try:
            member = tar.getmember(arc["member"])
        except KeyError:
            raise InstallError("the archive has no %s" % arc["member"])
        if not member.isfile():
            raise InstallError("%s in the archive is not a file" % arc["member"])
        body = tar.extractfile(member).read()
    check(body, ext["size"], ext["sha256"], path)
    with open(os.path.join(staging, path), "wb") as f:
        f.write(body)
    print("  %s: %d bytes, sha256 %s" % (path, len(body), ext["sha256"]))


def install(index, base, cart_id, dest, asset_dir=None, cache=None, force=False,
            assume_yes=False):
    carts = {c["id"]: c for c in index.get("carts", [])}
    if cart_id not in carts:
        raise InstallError("no cart %r in the index (it has: %s)"
                           % (cart_id, ", ".join(sorted(carts)) or "none"))
    cart = carts[cart_id]
    folder = cart["folder"]
    listed = [(fn, meta) for asset in cart["assets"] for fn, meta in asset["files"].items()]
    listed += [(ext["path"], {"size": ext["size"], "sha256": ext["sha256"]})
               for ext in cart.get("external", [])]
    expected = dict(listed)
    if len(expected) != len(listed):
        raise InstallError("the index lists a file of %s twice" % folder)
    for name in [folder] + list(expected):
        if not plain_name(name):
            raise InstallError("the index names %r, which this installer will not write"
                               % name)
    if not os.path.isdir(dest):
        raise InstallError("%s is not a directory" % dest)
    target = os.path.join(dest, folder)
    if os.path.exists(target) and not force:
        raise InstallError("%s already exists; pass --force to replace it" % target)

    print("%s %s -> %s" % (cart["name"], cart["version"], target))
    staging = tempfile.mkdtemp(prefix=".%s." % folder, dir=dest)
    umask = os.umask(0)
    os.umask(umask)
    os.chmod(staging, 0o777 & ~umask)
    try:
        show_licence(base, cart["licence"], folder)
        print("  source: %s" % cart["source"])
        if cart.get("source_asset"):
            print("  complete source as one file: %s" % cart["source_asset"]["url"])
        for asset in cart["assets"]:
            data = obtain(asset["name"], [asset["url"]], asset["size"], asset["sha256"],
                          base, asset_dir=asset_dir, name=asset["name"], cache=cache)
            unpack(data, cart, asset, staging)
            print("  %s: %d bytes, sha256 %s" % (asset["name"], asset["size"],
                                                 asset["sha256"]))
        for ext in cart.get("external", []):
            external(base, ext, staging, cache, assume_yes)
        got = sorted(os.listdir(staging))
        if got != sorted(expected):
            raise InstallError("the folder holds %s, not %s" % (got, sorted(expected)))
        for fn, meta in expected.items():
            with open(os.path.join(staging, fn), "rb") as f:
                check(f.read(), meta["size"], meta["sha256"], "the written %s" % fn)
        if os.path.exists(target):
            shutil.rmtree(target)
        os.rename(staging, target)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    print("Installed %s (%d files, every one checked)." % (target, len(expected)))
    return target


def list_carts(index):
    for cart in index.get("carts", []):
        print("%-10s %-20s version %-4s %-8s %s" % (
            cart["id"], cart["name"], cart["version"], cart["runtime"],
            cart["licence"].get("spdx", "")))
        for ext in cart.get("external", []):
            print("%-10s   needs %s (fetched from %s)" % (
                "", ext["path"], urllib.parse.urlsplit(ext["archive"]["urls"][0]).netloc))
    if not index.get("carts"):
        print("(the index lists no carts)")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("cart", nargs="?", help="the cart's id (see --list)")
    ap.add_argument("dest", nargs="?",
                    help="the carts folder to install into, e.g. an SD card's moybyte/carts")
    ap.add_argument("--index", default=DEFAULT_INDEX,
                    help="the index: a URL or a local file (default %s)" % DEFAULT_INDEX)
    ap.add_argument("--list", action="store_true", help="list the index's carts")
    ap.add_argument("--asset-dir", help="take release assets from this directory")
    ap.add_argument("--cache", help="keep release assets and external archives here, by sha256")
    ap.add_argument("--force", action="store_true", help="replace an installed cart")
    ap.add_argument("--yes", action="store_true",
                    help="accept the licences of external files without asking")
    args = ap.parse_args(argv)
    try:
        index = load_index(args.index)
        if args.list:
            list_carts(index)
            return 0
        if not args.cart or not args.dest:
            ap.error("name a cart and a destination folder (or pass --list)")
        install(index, args.index, args.cart, args.dest, asset_dir=args.asset_dir,
                cache=args.cache, force=args.force, assume_yes=args.yes)
    except InstallError as exc:
        print("install: %s" % exc, file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("install: interrupted; nothing was written", file=sys.stderr)
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
