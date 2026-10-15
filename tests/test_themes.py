# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Nikola Jovicic
"""Theme packages ("runtime": "theme"), held to what scripts/validate.py and
scripts/build.py say of them: a good theme passes, builds into a zip that is
its folder byte for byte and verifies against itself; a theme that names
`inherits`, is too big, carries code, ships a font without its licence, or
whose name is not its id is refused.

    python3 -m unittest discover -s tests

Each case copies this repository into a temporary git repository, adds the
theme carts/dusk/ (a font, its licence text, the theme's own MIT licence),
breaks one thing, and runs the checks on the copy. Standard library only.
"""

import contextlib
import io
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import build  # noqa: E402
import validate  # noqa: E402

ID = "dusk"

# Words of the SIL Open Font License 1.1 the licence check reads; a real
# package carries the whole text.
OFL = """Copyright 2022-2024 The Tiny5 Project Authors

SIL OPEN FONT LICENSE Version 1.1 - 26 February 2007

PERMISSION & CONDITIONS
...
TERMINATION
...
DISCLAIMER
...
"""


def font(size_extra=0):
    """A one-glyph font file: MFNT, version 1, a 1x1 box, its size field its length."""
    body = bytes([1, 1, 1, 1, 1, 0x20, 1, 0])
    tail = bytes([1, 0, 0, 0]) + b"\x01" + b"\x00" * size_extra
    data = b"MFNT" + body + struct.pack("<I", 16 + len(tail)) + tail
    return data


class Themes(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.root = os.path.join(self.tmp, "repo")
        os.makedirs(self.root)
        ignore = shutil.ignore_patterns("__pycache__")
        for name in ("carts", "scripts", "tests"):
            shutil.copytree(os.path.join(ROOT, name), os.path.join(self.root, name),
                            ignore=ignore)
        for name in ("LICENSE", "install.py", "index.json", ".gitignore"):
            shutil.copy(os.path.join(ROOT, name), self.root)
        with open(os.path.join(ROOT, "LICENSE"), encoding="utf-8") as f:
            mit = f.read()
        self.write("manifest.json", json.dumps({
            "format": "moy-1", "title": "Dusk", "runtime": "theme", "main": "theme.json",
            "moybyte": {"type": "theme"}}, indent=2) + "\n")
        self.write("cart.json", json.dumps({
            "id": ID, "name": "Dusk", "version": 1, "release": "dusk-v1",
            "folder": "dusk.moy", "licence": {"spdx": "MIT", "file": "LICENSES.txt"}},
            indent=2) + "\n")
        self.write("theme.json", json.dumps({
            "format": 1, "name": ID, "fonts": {"ui": "tiny5.fnt"},
            "colors": {"dark": {"panel": 62}}}))
        self.write("tiny5.fnt", font())
        self.write("tiny5.txt", OFL)
        self.write("LICENSES.txt", "Dusk is MIT, below; tiny5.fnt is the Tiny5 font, "
                   "OFL-1.1, its licence in tiny5.txt.\n\n" + mit)
        self.saved = {(m, k): getattr(m, k) for m, k in (
            (validate, "ROOT"), (validate, "CARTS"), (validate, "INDEX"),
            (build, "ROOT"), (build, "CARTS"), (build, "BUILD"))}
        validate.ROOT = build.ROOT = self.root
        validate.CARTS = build.CARTS = self.path("carts")
        validate.INDEX = self.path("index.json")
        build.BUILD = self.path("build")

    def tearDown(self):
        for (m, k), v in self.saved.items():
            setattr(m, k, v)
        shutil.rmtree(self.tmp)

    def path(self, *parts):
        return os.path.join(self.root, *parts)

    def write(self, fn, data):
        os.makedirs(self.path("carts", ID), exist_ok=True)
        mode = "wb" if isinstance(data, bytes) else "w"
        with open(self.path("carts", ID, fn), mode) as f:
            f.write(data)

    def edit(self, fn, change):
        with open(self.path("carts", ID, fn), encoding="utf-8") as f:
            data = json.load(f)
        change(data)
        self.write(fn, json.dumps(data))

    def git(self, *args):
        return subprocess.run(["git", "-C", self.root, "-c", "user.name=t", "-c",
                               "user.email=t@t", "-c", "commit.gpgsign=false"] + list(args),
                              capture_output=True, text=True, check=True).stdout.strip()

    def commit(self):
        self.git("init", "-q")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "a theme")
        return self.git("rev-parse", "HEAD")

    def index(self):
        with open(self.path("index.json"), encoding="utf-8") as f:
            return json.load(f)

    def errors(self, index=None):
        index = self.index() if index is None else index
        rep = validate.Report()
        validate.check_schema(index, rep)
        validate.check_carts(index, rep)
        validate.check_licences(self.root, index, rep)
        return rep.errors

    def assertFails(self, needle, index=None):
        errs = self.errors(index)
        self.assertTrue(any(needle in e for e in errs),
                        "no error mentions %r; got %r" % (needle, errs))

    def built_entry(self):
        """Build the theme and write its entry as moy's index maker (moy-spec's
        cartindex.index_entry) does from build.json."""
        commit = self.commit()
        with contextlib.redirect_stdout(io.StringIO()):
            info = build.build(ID, self.path("build"), None, commit)
        with open(self.path("carts", ID, "LICENSES.txt"), "rb") as f:
            lic = f.read()
        asset = info["asset"]
        url = "%s/releases/download/dusk-v1/" % validate.REPO_URL
        entry = {
            "id": ID, "name": "Dusk", "version": 1, "folder": "dusk.moy",
            "runtime": "theme", "memory": None, "chips": [],
            "licence": {"spdx": "MIT", "name": "MIT", "url": "carts/dusk/LICENSES.txt",
                        "size": len(lic), "sha256": validate.sha256(lic)},
            "source": info["source"],
            "release": "%s/releases/tag/dusk-v1" % validate.REPO_URL,
            "assets": [{"name": asset["name"], "url": url + asset["name"],
                        "size": asset["size"], "sha256": asset["sha256"],
                        "files": info["files"],
                        "mirror": "releases/dusk-v1/" + asset["name"]}],
            "external": [],
            "build": {"commit": commit, "keys": {}, "modules": {}, "pins": {}},
        }
        index = self.index()
        index["carts"].append(entry)
        return index, entry, info

    # -- a good theme -------------------------------------------------------------

    def test_a_good_theme_passes(self):
        self.assertEqual(self.errors(), [])

    def test_it_builds_into_its_folder_and_its_index_entry_passes(self):
        index, entry, info = self.built_entry()
        self.assertNotIn("source_asset", info)
        self.assertEqual(sorted(info["files"]), ["LICENSES.txt", "manifest.json",
                                                 "theme.json", "tiny5.fnt", "tiny5.txt"])
        zpath = self.path("build", "dist", ID, "dusk.moy.zip")
        with zipfile.ZipFile(zpath) as z:
            for n in z.namelist():
                with open(self.path("carts", ID, n.split("/", 1)[1]), "rb") as f:
                    self.assertEqual(z.read(n), f.read(), n)
        self.assertEqual(self.errors(index), [])
        rep = validate.Report()
        self.assertTrue(validate.check_theme_source(entry, entry["assets"][0], ID, rep),
                        rep.errors)

    def test_a_release_zip_verifies_and_a_changed_one_does_not(self):
        self.built_entry()
        zpath = self.path("build", "dist", ID, "dusk.moy.zip")
        kept = os.path.join(self.tmp, "dusk.moy.zip")
        shutil.copy(zpath, kept)
        commit = self.git("rev-parse", "HEAD")
        with contextlib.redirect_stdout(io.StringIO()):
            build.verify_release(ID, [kept], None, commit)
        with zipfile.ZipFile(kept) as z:
            files = {n: z.read(n) for n in z.namelist()}
        files["dusk.moy/theme.json"] = files["dusk.moy/theme.json"].replace(b"62", b"63")
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as z:
            for n in sorted(files):
                z.writestr(zipfile.ZipInfo(n, build.ZIP_TIME), files[n])
        with open(kept, "wb") as f:
            f.write(buf.getvalue())
        with self.assertRaisesRegex(build.BuildError, "theme.json differ"), \
                contextlib.redirect_stdout(io.StringIO()):
            build.verify_release(ID, [kept], None, commit)

    def test_an_index_entry_with_a_source_bundle_or_chips_fails(self):
        index, entry, _info = self.built_entry()
        entry["source_asset"] = {"name": "dusk-v1-source.tar.gz", "url": "https://x",
                                 "size": 1, "sha256": "0" * 64}
        self.assertFails("a theme has no source bundle", index)
        del entry["source_asset"]
        entry["chips"] = ["esp32s3"]
        self.assertFails("a theme runs on no chip", index)

    def test_a_zip_that_is_not_the_build_commits_folder_fails(self):
        _index, entry, _info = self.built_entry()
        files = entry["assets"][0]["files"]
        files["theme.json"] = dict(files["theme.json"], sha256="0" * 64)
        rep = validate.Report()
        self.assertFalse(validate.check_theme_source(entry, entry["assets"][0], ID, rep))
        self.assertTrue(any("theme.json is not the build commit's" in e for e in rep.errors))

    def test_a_theme_with_its_sheets_passes(self):
        self.write("parts.moygfx", "0123456789abcdef" * 8 + "\n\n" + "F0\r\n")
        self.write("icons.moygfx", "7" * 128 + "\n")
        self.edit("theme.json", lambda d: d.update(sprites="parts.moygfx",
                                                   icons="icons.moygfx"))
        self.assertEqual(self.errors(), [])

    # -- refused ------------------------------------------------------------------

    def test_a_sheet_the_console_will_not_read_is_refused(self):
        for bad in ("0123g\n", "0" * 129 + "\n", "1\n" * 257, "0 1\n"):
            self.write("parts.moygfx", bad)
            self.assertFails("carts/dusk/parts.moygfx: not a sheet a console loads")

    def test_a_sheet_theme_json_names_must_be_there_and_fit(self):
        self.edit("theme.json", lambda d: d.update(sprites="parts.moygfx"))
        self.assertFails("its sprites names parts.moygfx, which is not in the folder")
        self.edit("theme.json", lambda d: d.update(sprites="parts.png"))
        self.assertFails("its sprites names no .moygfx a console reads")
        self.write("icons.moygfx", "1\n" * 65)
        self.edit("theme.json", lambda d: d.update(sprites=None, icons="icons.moygfx"))
        self.assertFails("carts/dusk/icons.moygfx: an icon sheet holds at most 64 rows")

    def test_inherits_is_refused(self):
        self.edit("theme.json", lambda d: d.update(inherits="berry"))
        self.assertFails("is standalone and names no inherits")

    def test_an_oversize_theme_json_is_refused(self):
        self.edit("theme.json", lambda d: d.update(pad="x" * validate.THEME_BYTES))
        self.assertFails("a console refuses one over 4096")

    def test_an_oversize_font_is_refused(self):
        self.write("tiny5.fnt", font(validate.FONT_BYTES))
        self.assertFails("carts/dusk/tiny5.fnt: not a font a console loads")

    def test_a_code_file_is_refused(self):
        self.write("main.lua", "print(1)\n")
        self.assertFails("carts/dusk/main.lua: a theme holds data only, and this is code")

    def test_any_other_file_is_refused(self):
        self.write("notes.md", "hi\n")
        self.assertFails("carts/dusk/notes.md: a theme holds theme.json")

    def test_a_font_without_its_licence_is_refused(self):
        os.remove(self.path("carts", ID, "tiny5.txt"))
        self.assertFails("carts/dusk/tiny5.fnt ships without its licence")

    def test_a_font_licence_validate_does_not_know_is_refused(self):
        self.write("tiny5.txt", "Some licence.\n")
        self.assertFails("carts/dusk/tiny5.txt is no licence text validate.py knows")

    def test_a_font_the_themes_licence_does_not_name_is_refused(self):
        with open(self.path("LICENSE"), encoding="utf-8") as f:
            self.write("LICENSES.txt", "Dusk is MIT.\n\n" + f.read())
        self.assertFails("carts/dusk/tiny5.fnt is OFL-1.1, and the cart is MIT")

    def test_a_name_that_is_not_the_id_is_refused(self):
        self.edit("theme.json", lambda d: d.update(name="dawn"))
        self.assertFails("its name is 'dawn'; a theme's name is its id, 'dusk'")

    def test_a_folder_that_is_not_the_id_is_refused(self):
        self.edit("cart.json", lambda d: d.update(folder="nikola.dusk.moy"))
        self.assertFails("a theme's folder is dusk.moy")

    def test_a_seeds_name_is_refused(self):
        self.edit("cart.json", lambda d: d.update(id="berry", folder="berry.moy"))
        self.edit("theme.json", lambda d: d.update(name="berry"))
        os.rename(self.path("carts", ID), self.path("carts", "berry"))
        self.assertFails("and no seed's")

    def test_a_manifest_that_is_not_a_themes_is_refused(self):
        self.edit("manifest.json", lambda d: d.update(main="main.lua"))
        self.assertFails("a theme's main is theme.json")
        self.edit("manifest.json", lambda d: (d.update(main="theme.json"), d.pop("moybyte")))
        self.assertFails("a theme says \"moybyte\": {\"type\": \"theme\"}")

    def test_chips_or_memory_in_cart_json_are_refused(self):
        self.edit("cart.json", lambda d: d.update(chips=["esp32s3"]))
        self.assertFails("carts/dusk/cart.json: a theme has no chips")

    def test_a_refused_theme_does_not_build(self):
        self.edit("theme.json", lambda d: d.update(inherits="berry"))
        commit = self.commit()
        with self.assertRaisesRegex(build.BuildError, "not a theme a console loads"):
            build.build(ID, self.path("build"), None, commit)


if __name__ == "__main__":
    unittest.main()
