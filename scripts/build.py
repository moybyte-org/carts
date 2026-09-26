#!/usr/bin/env python3
"""Build a cart from its recipe, reproducibly.

    python3 scripts/build.py doom               # -> build/dist/doom/
    python3 scripts/build.py doom --twice       # ...then again from scratch; every byte must match
    python3 scripts/build.py doom --moybyte ~/src/moybyte
    python3 scripts/build.py doom --verify-release doom.moy.zip   # no key needed

A cart is carts/<id>/: cart.json (what the index says about it),
manifest.json and config.json (copied into the cart byte for byte), recipe.py
(fetches the sources at pinned commits, stages, patches and links main.wasm),
and its licences. This script runs the recipe, compiles and signs one module
per chip with Moybyte's tools/wasm_module.py at MOYBYTE_COMMIT, writes
SOURCE.txt (every pin the build used), and packs the folder:

    build/out/<id>/<folder>/          the cart folder, without its external files
    build/dist/<id>/<folder>.zip      the release asset
    build/dist/<id>/SHA256SUMS        the asset's sha256
    build/dist/<id>/build.json        what scripts/make_index.py reads

Every download and every product stays under build/ (gitignored). No .wasm,
.aot, zip, WAD or key is ever committed.

THE MOYBYTE TOOLS are read at a pinned commit, never from a working tree. The
files in MOYBYTE_FILES come from a local moybyte checkout's object store
(`git show <commit>:<path>`, read-only; --moybyte, default ../moybyte) or,
when that checkout does not have the commit, from GitHub at that commit, and
are checked against MOYBYTE_TREE_SHA256 before anything imports them. The
compilers are the ones those tools pin by sha256 (the fork's wamrc release).

SIGNING. The modules are signed with the Moybyte OTA signing key, which
tools/wasm_module.py finds itself ($MOYBYTE_OTA_SIGNING_KEY, else
~/.moybyte-ota-signing-key.pem); this script never reads, prints or copies
it, and each module is checked against the public keys the firmware at
MOYBYTE_COMMIT trusts. RSA PKCS#1 v1.5 signatures are deterministic, so a
rebuild signed with the same key is byte-identical. Without the key,
--verify-release builds the modules unsigned and holds a released zip to
them: every file identical, each module this build's module plus a signature
the firmware trusts.

REPRODUCIBLE: the engine's __FILE__ strings are mapped to bare file names,
the zip's entries are sorted and STORED with a fixed time and mode, and no
pin floats, so the output depends on the inputs and not on where, when or by
whom it was built.
"""

import argparse
import hashlib
import importlib
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import urllib.request
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CARTS = os.path.join(ROOT, "carts")
BUILD = os.path.join(ROOT, "build")
CACHE = os.path.join(BUILD, "cache")
TOOLCHAIN = os.path.join(BUILD, "toolchain")

REPO = "moybyte-org/gpl-carts"
REPO_URL = "https://github.com/" + REPO

# Moybyte's compile-and-sign tools, at the commit this build takes them from.
# The tree hash is every file's path and sha256, sorted, one per line.
MOYBYTE_REPO = "moybyte-org/moybyte"
MOYBYTE_COMMIT = "e02e5d3df73a3e7ca274832b0bb9e7bdb8bbd353"
MOYBYTE_FILES = (
    "device/moy_ota.py",                    # the public keys a board trusts; verify_sig
    "native/moy_wasm/moy_wasm_key.h",       # each chip's compiler flags and the signature layout
    "native/moy_wasm/wamr_pin.h",           # the runtime fork a board built at this commit wants
    "native/moy_wasm/wamr_vendor.json",     # the same fork commit, as the tools read it
    "tools/ota_sign.py",
    "tools/wasm_cart.py",                   # aot_name: where a board looks for its module
    "tools/wasm_module.py",                 # compile, key and sign a module per chip
    "tools/wat.py",                         # imported by wasm_cart.py
)
MOYBYTE_TREE_SHA256 = "298d7f3a9699135bafc6266a0a8aaa07ecba424e1c4aa6178e677150cb5c4f7b"

# wasi-sdk 24: clang 18 and wasi-libc.
WASI_SDK_VERSION = "24.0"
WASI_SDK_URL = ("https://github.com/WebAssembly/wasi-sdk/releases/download/"
                "wasi-sdk-24/wasi-sdk-24.0-x86_64-linux.tar.gz")
WASI_SDK_SHA256 = "c6c38aab56e5de88adf6c1ebc9c3ae8da72f88ec2b656fb024eda8d4167a0bc5"

ZIP_TIME = (1980, 1, 1, 0, 0, 0)


class BuildError(RuntimeError):
    pass


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def tree_sha256(files):
    """The sha256 of a set of files: every "path sha256", sorted, one per line."""
    return sha256("\n".join(sorted("%s %s" % (p, sha256(d)) for p, d in files.items())).encode())


def _download(url):
    req = urllib.request.Request(url, headers={"User-Agent": "moybyte-gpl-carts-build"})
    with urllib.request.urlopen(req, timeout=300) as r:
        return r.read()


def fetch(url, pin, name):
    """The bytes at `url`, cached as build/cache/`name`. With a `pin` they are
    refused unless they hash to it; without one the caller checks them (a
    GitHub source tarball, whose bytes GitHub does not promise)."""
    path = os.path.join(CACHE, name)
    if not os.path.isfile(path):
        os.makedirs(CACHE, exist_ok=True)
        print("fetching %s" % url)
        data = _download(url)
        if pin and sha256(data) != pin:
            raise BuildError("%s hashes to %s; the pin is %s" % (url, sha256(data), pin))
        with open(path + ".part", "wb") as f:
            f.write(data)
        os.replace(path + ".part", path)
    with open(path, "rb") as f:
        data = f.read()
    if pin and sha256(data) != pin:
        raise BuildError("%s hashes to %s; the pin is %s (delete it to refetch)"
                         % (path, sha256(data), pin))
    return data


def git(*args):
    return subprocess.run(["git", "-C", ROOT] + list(args), capture_output=True,
                          text=True, check=True).stdout.strip()


def source_commit(allow_dirty):
    """This repository's commit, which SOURCE.txt names. A tree with changes is
    refused: the cart would name a commit it was not built from."""
    commit = git("rev-parse", "HEAD")
    if git("status", "--porcelain"):
        if not allow_dirty:
            raise BuildError("the working tree has changes; commit them (the cart names "
                             "the commit it was built from) or pass --allow-dirty")
        return commit + "+dirty"
    return commit


# -- Moybyte's tools --------------------------------------------------------------


def _moybyte_file(checkout, path):
    if checkout:
        r = subprocess.run(["git", "-C", checkout, "show", "%s:%s" % (MOYBYTE_COMMIT, path)],
                           capture_output=True)
        if r.returncode == 0:
            return r.stdout
    return _download("https://raw.githubusercontent.com/%s/%s/%s"
                     % (MOYBYTE_REPO, MOYBYTE_COMMIT, path))


def moybyte_tools(checkout):
    """(wasm_module, wasm_cart) imported from MOYBYTE_FILES at MOYBYTE_COMMIT,
    written under build/moybyte/<commit> after their tree hash checks."""
    root = os.path.join(BUILD, "moybyte", MOYBYTE_COMMIT[:12])
    have = {}
    for path in MOYBYTE_FILES:
        p = os.path.join(root, path)
        if os.path.isfile(p):
            with open(p, "rb") as f:
                have[path] = f.read()
    if len(have) != len(MOYBYTE_FILES) or tree_sha256(have) != MOYBYTE_TREE_SHA256:
        have = {path: _moybyte_file(checkout, path) for path in MOYBYTE_FILES}
        got = tree_sha256(have)
        if got != MOYBYTE_TREE_SHA256:
            raise BuildError("moybyte %s: the tools hash to %s; the pin is %s"
                             % (MOYBYTE_COMMIT[:12], got, MOYBYTE_TREE_SHA256))
        if os.path.isdir(root):
            shutil.rmtree(root)
        for path, data in have.items():
            p = os.path.join(root, path)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "wb") as f:
                f.write(data)
    for var in ("MOYBYTE_WAMRC_XTENSA", "MOYBYTE_WAMRC_RISCV32"):
        if os.environ.get(var):
            raise BuildError("$%s is set: a release is built with the pinned compilers only"
                             % var)
    if root not in sys.path:
        sys.path.insert(0, root)
    wasm_module = importlib.import_module("tools.wasm_module")
    wasm_cart = importlib.import_module("tools.wasm_cart")
    if os.path.dirname(os.path.abspath(wasm_module.__file__)) != os.path.join(root, "tools"):
        raise BuildError("tools.wasm_module was imported from %s, not the pinned copy"
                         % wasm_module.__file__)
    # The pinned compilers are cached once for every commit; the tool still
    # refuses one whose sha256 is not its pin.
    wasm_module.DIST = os.path.join(TOOLCHAIN, "wamrc")
    pin_h = have["native/moy_wasm/wamr_pin.h"].decode()
    if '"%s"' % wasm_module.fork_commit() not in pin_h:
        raise BuildError("moybyte %s: wamr_pin.h and wamr_vendor.json name different forks"
                         % MOYBYTE_COMMIT[:12])
    return wasm_module, wasm_cart


def wasi_sdk():
    """The pinned wasi-sdk, fetched and unpacked under build/toolchain."""
    home = os.path.join(TOOLCHAIN, "wasi-sdk-" + WASI_SDK_VERSION)
    if os.path.isfile(os.path.join(home, "bin", "clang")):
        return home
    data = fetch(WASI_SDK_URL, WASI_SDK_SHA256, os.path.basename(WASI_SDK_URL))
    tmp = home + ".part"
    if os.path.isdir(tmp):
        shutil.rmtree(tmp)
    os.makedirs(tmp)
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        top = tar.getmembers()[0].name.split("/")[0]
        kwargs = {"filter": "tar"} if hasattr(tarfile, "tar_filter") else {}
        tar.extractall(tmp, **kwargs)
    os.replace(os.path.join(tmp, top), home)
    shutil.rmtree(tmp)
    return home


# -- the build -----------------------------------------------------------------------


class Context:
    """What a recipe is handed: where to stage, the toolchain, the fetcher."""

    def __init__(self, cart_id, cart_dir, stage, sdk, wasm_module, file_prefix):
        self.cart_id = cart_id
        self.cart_dir = cart_dir
        self.stage = stage
        self.wasi_sdk = sdk
        self.wasm_module = wasm_module
        self.file_prefix = file_prefix
        self.error = BuildError

    def fetch(self, url, pin, name):
        return fetch(url, pin, name)

    def read(self, name):
        with open(os.path.join(self.cart_dir, name), "rb") as f:
            return f.read()


def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load_recipe(cart_id):
    path = os.path.join(CARTS, cart_id, "recipe.py")
    spec = importlib.util.spec_from_file_location("recipe_" + cart_id, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def source_text(meta, commit, pins):
    lines = [
        "%s for Moybyte consoles: where every byte of this cart comes from." % meta["name"],
        "",
        "This cart was built by scripts/build.py from:",
        "",
        "  gpl-carts     %s/tree/%s/carts/%s" % (REPO_URL, commit, meta["id"]),
        "                commit %s" % commit,
    ]
    for key in sorted(pins):
        pin = pins[key]
        lines.append("  %-13s %s" % (key, pin["url"]))
        for field in ("commit", "tree_sha256", "sha256", "version", "note"):
            if field in pin:
                lines.append("                %s %s" % (field.replace("_", " "), pin[field]))
    lines += [
        "",
        "To rebuild it, byte for byte:",
        "",
        "  git clone %s && cd gpl-carts" % REPO_URL,
        "  git checkout %s" % commit,
        "  python3 scripts/build.py %s" % meta["id"],
        "",
        "The per-chip modules are signed with the Moybyte OTA key, which a rebuild",
        "needs to make a cart a board loads. Without it, this checks a release",
        "against the source -- every file identical, each module this build's plus",
        "a signature the firmware trusts:",
        "",
        "  python3 scripts/build.py %s --verify-release %s.zip" % (meta["id"], meta["folder"]),
        "",
    ]
    return "\n".join(lines)


def pack(folder, name, out):
    """`folder`'s files as `name`/<file> in a STORED zip at `out`, sorted, with
    a fixed time and mode."""
    with zipfile.ZipFile(out, "w", zipfile.ZIP_STORED) as z:
        for fn in sorted(os.listdir(folder)):
            with open(os.path.join(folder, fn), "rb") as f:
                data = f.read()
            info = zipfile.ZipInfo("%s/%s" % (name, fn), ZIP_TIME)
            info.compress_type = zipfile.ZIP_STORED
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            z.writestr(info, data)


def build(cart_id, out_root, checkout, commit, file_prefix="", signed=True):
    """Build carts/`cart_id` into `out_root`/out and `out_root`/dist. Returns
    build.json's content. `signed=False` leaves the modules' signatures off
    (a cart a board refuses; what --verify-release compares)."""
    cart_dir = os.path.join(CARTS, cart_id)
    meta = load_json(os.path.join(cart_dir, "cart.json"))
    manifest = load_json(os.path.join(cart_dir, "manifest.json"))
    if meta["id"] != cart_id:
        raise BuildError("carts/%s/cart.json says its id is %r" % (cart_id, meta["id"]))
    wasm_module, wasm_cart = moybyte_tools(checkout)
    sdk = wasi_sdk()
    recipe = load_recipe(cart_id)

    stage = os.path.join(BUILD, "stage", cart_id)
    if os.path.isdir(stage):
        shutil.rmtree(stage)
    os.makedirs(stage)
    folder = os.path.join(out_root, "out", cart_id, meta["folder"])
    if os.path.isdir(folder):
        shutil.rmtree(folder)
    os.makedirs(folder)

    ctx = Context(cart_id, cart_dir, stage, sdk, wasm_module, file_prefix)
    made = recipe.build(ctx, folder)
    wasm = made["wasm"]
    main = manifest.get("main", "main.wasm")
    with open(os.path.join(folder, main), "rb") as f:
        if f.read() != wasm:
            raise BuildError("the recipe's %s is not the wasm it returned" % main)

    keys = {}
    for chip in meta["chips"]:
        path = os.path.join(folder, wasm_cart.aot_name(main, chip))
        wasm_module.build(wasm, chip, path, signed=signed)
        with open(path, "rb") as f:
            why = wasm_module.verify(f.read(), chip) if signed else None
        if why is not None:
            raise BuildError("%s: a board built at moybyte %s refuses it: %s"
                             % (os.path.basename(path), MOYBYTE_COMMIT[:12], why))
        keys[chip] = wasm_module.key_tail(chip)

    compilers = wasm_module.COMPILERS
    pins = dict(made["pins"])
    pins["moybyte"] = {
        "url": "https://github.com/%s/tree/%s" % (MOYBYTE_REPO, MOYBYTE_COMMIT),
        "commit": MOYBYTE_COMMIT, "tree_sha256": MOYBYTE_TREE_SHA256,
        "note": "tools/wasm_module.py and the files in scripts/build.py's MOYBYTE_FILES",
    }
    pins["wasi-sdk"] = {"url": WASI_SDK_URL, "sha256": WASI_SDK_SHA256,
                        "version": WASI_SDK_VERSION}
    for target in sorted(compilers):
        pins["wamrc-" + target] = {"url": compilers[target]["url"],
                                   "sha256": compilers[target]["sha256"]}
    pins["wamr-fork"] = {
        "url": "https://github.com/moybyte-org/wasm-micro-runtime/tree/%s"
               % wasm_module.fork_commit(),
        "commit": wasm_module.fork_commit(),
        "note": "the runtime the modules are keyed for; a board built for another refuses them",
    }
    with open(os.path.join(folder, "SOURCE.txt"), "w", encoding="utf-8", newline="\n") as f:
        f.write(source_text(meta, commit, pins))

    names = sorted(os.listdir(folder))
    for fn in names:
        if fn.lower().endswith(".wad") or fn in [e["path"] for e in meta.get("external", [])]:
            raise BuildError("%s is an external file and must never be in the cart asset" % fn)
    files = {}
    for fn in names:
        with open(os.path.join(folder, fn), "rb") as f:
            data = f.read()
        files[fn] = {"size": len(data), "sha256": sha256(data)}
    for fn in ("manifest.json", "config.json"):
        if fn in files and files[fn]["sha256"] != sha256(ctx.read(fn)):
            raise BuildError("the cart's %s is not carts/%s/%s byte for byte" % (fn, cart_id, fn))

    dist = os.path.join(out_root, "dist", cart_id)
    if os.path.isdir(dist):
        shutil.rmtree(dist)
    os.makedirs(dist)
    asset = meta["folder"] + ".zip"
    pack(folder, meta["folder"], os.path.join(dist, asset))
    with open(os.path.join(dist, asset), "rb") as f:
        blob = f.read()
    with open(os.path.join(dist, "SHA256SUMS"), "w", newline="\n") as f:
        f.write("%s  %s\n" % (sha256(blob), asset))
    info = {
        "id": cart_id,
        "commit": commit,
        "source": "%s/tree/%s/carts/%s" % (REPO_URL, commit, cart_id),
        "asset": {"name": asset, "size": len(blob), "sha256": sha256(blob)},
        "files": files,
        "memory": manifest.get("memory"),
        "keys": keys,
        "pins": pins,
    }
    with open(os.path.join(dist, "build.json"), "w", newline="\n") as f:
        json.dump(info, f, indent=2, sort_keys=True)
        f.write("\n")
    print("%s: %s, %d bytes, sha256 %s" % (cart_id, asset, len(blob), sha256(blob)))
    for fn in names:
        print("  %-22s %9d  %s" % (fn, files[fn]["size"], files[fn]["sha256"]))
    return info


def compare(a, b):
    """[relative paths whose bytes differ, or which only one tree has]."""
    def walk(top):
        out = {}
        for d, _dirs, fns in os.walk(top):
            for fn in fns:
                p = os.path.join(d, fn)
                out[os.path.relpath(p, top)] = p
        return out
    wa, wb = walk(a), walk(b)
    diff = sorted(set(wa) ^ set(wb))
    for rel in sorted(set(wa) & set(wb)):
        with open(wa[rel], "rb") as fa, open(wb[rel], "rb") as fb:
            if fa.read() != fb.read():
                diff.append(rel)
    return diff


def verify_release(cart_id, asset, checkout, commit):
    """Build `cart_id` unsigned and hold the released `asset` (its zip) to it:
    every file byte for byte, and each module as this build's module plus a
    signature the firmware at MOYBYTE_COMMIT trusts. Needs no signing key."""
    info = build(cart_id, os.path.join(BUILD, "verify-release"), checkout, commit,
                 signed=False)
    wasm_module, _wasm_cart = moybyte_tools(checkout)
    meta = load_json(os.path.join(CARTS, cart_id, "cart.json"))
    folder = os.path.join(BUILD, "verify-release", "out", cart_id, meta["folder"])
    with zipfile.ZipFile(asset) as z:
        released = {n.split("/", 1)[1]: z.read(n) for n in z.namelist()}
    if sorted(released) != sorted(info["files"]):
        raise BuildError("the release holds %s; this build makes %s"
                         % (sorted(released), sorted(info["files"])))
    for fn in sorted(released):
        with open(os.path.join(folder, fn), "rb") as f:
            ours = f.read()
        data = released[fn]
        if fn.endswith(".aot"):
            chip = fn.split(".")[-2]
            module, sig = wasm_module.split(data)
            if module != ours:
                raise BuildError("%s: the released module is not this build's" % fn)
            why = wasm_module.verify(data, chip)
            if not sig or why is not None:
                raise BuildError("%s: the module is this build's, but its signature is "
                                 "refused: %s" % (fn, why or "none"))
            print("  %-22s this build's module, plus a trusted %d-byte signature"
                  % (fn, len(sig)))
        elif data != ours:
            raise BuildError("%s: the released file is not this build's" % fn)
        else:
            print("  %-22s identical" % fn)
    print("%s: %s corresponds to this source" % (cart_id, os.path.basename(asset)))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("cart", help="the cart's id: a directory under carts/")
    ap.add_argument("--moybyte", default=os.path.join(os.path.dirname(ROOT), "moybyte"),
                    help="a local moybyte checkout to read the pinned tools from "
                    "(read-only; default ../moybyte; GitHub when it lacks the commit)")
    ap.add_argument("--twice", action="store_true",
                    help="build a second time from scratch and require identical bytes")
    ap.add_argument("--out", default=BUILD, help="where out/ and dist/ go (default build/)")
    ap.add_argument("--allow-dirty", action="store_true",
                    help="build from a tree with changes (the cart says so)")
    ap.add_argument("--unsigned", action="store_true",
                    help="leave the modules unsigned (no key needed; a board refuses them)")
    ap.add_argument("--verify-release", metavar="ZIP",
                    help="build unsigned and check that a released zip is this source's, "
                    "its modules signed with a key the firmware trusts")
    ap.add_argument("--file-prefix", default="", help=argparse.SUPPRESS)
    args = ap.parse_args(argv)
    checkout = args.moybyte if os.path.isdir(os.path.join(args.moybyte, ".git")) else None
    try:
        commit = source_commit(args.allow_dirty)
        if args.verify_release:
            verify_release(args.cart, args.verify_release, checkout, commit)
            return 0
        build(args.cart, args.out, checkout, commit, args.file_prefix, not args.unsigned)
        if args.twice:
            again = os.path.join(BUILD, "again")
            build(args.cart, again, checkout, commit, args.file_prefix, not args.unsigned)
            diff = compare(os.path.join(args.out, "out", args.cart),
                           os.path.join(again, "out", args.cart))
            diff += compare(os.path.join(args.out, "dist", args.cart),
                            os.path.join(again, "dist", args.cart))
            if diff:
                raise BuildError("the second build differs: %s" % ", ".join(diff))
            print("%s: the second build is byte-identical" % args.cart)
    except (BuildError, subprocess.CalledProcessError) as exc:
        print("build: %s" % exc, file=sys.stderr)
        return 2
    except Exception as exc:  # the moybyte tools raise their own ToolError
        if type(exc).__name__ != "ToolError":
            raise
        print("build: %s" % exc, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
