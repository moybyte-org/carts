# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Nikola Jovicic
"""Lua carts ("runtime": "lua"), held to what scripts/validate.py and
scripts/build.py say of them: a good one passes, builds into a zip that is
its folder byte for byte and verifies against itself; one whose main or
service is missing, which carries other code or a folder, or whose folder is
not its id is refused.

    python3 -m unittest discover -s tests

Each case copies this repository into a temporary git repository, adds the
Lua cart carts/chat/ (a window, its service, its MIT licence), breaks one
thing, and runs the checks on the copy. Standard library only.
"""

import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import build  # noqa: E402
import validate  # noqa: E402

ID = "chat"


class Lua(unittest.TestCase):

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
            "format": "moy-1", "title": "Chat", "id": ID, "runtime": "lua",
            "main": "main.lua", "moybyte": {"type": "app", "permissions": ["prefs"],
                                             "entries": {"service": "service.lua"}}},
            indent=2) + "\n")
        self.write("cart.json", json.dumps({
            "id": ID, "name": "Chat", "version": 1, "release": "chat-v1",
            "folder": "chat.moy", "licence": {"spdx": "MIT", "file": "LICENSE.txt"}},
            indent=2) + "\n")
        self.write("main.lua", "function _draw()\n  ui.page(\"Chat\")\nend\n")
        self.write("service.lua", "function moy_start()\nend\n")
        self.write("LICENSE.txt", mit)
        self.write("README.md", "# Chat\n")
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
        with open(self.path("carts", ID, fn), "w") as f:
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
        self.git("commit", "-q", "-m", "a Lua cart")
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
        """Build the cart and write its entry as moy's index maker (moy-spec's
        cartindex.index_entry) does from build.json."""
        commit = self.commit()
        with contextlib.redirect_stdout(io.StringIO()):
            info = build.build(ID, self.path("build"), None, commit)
        with open(self.path("carts", ID, "LICENSE.txt"), "rb") as f:
            lic = f.read()
        asset = info["asset"]
        url = "%s/releases/download/chat-v1/" % validate.REPO_URL
        entry = {
            "id": ID, "name": "Chat", "version": 1, "folder": "chat.moy",
            "runtime": "lua", "memory": None, "chips": [],
            "licence": {"spdx": "MIT", "name": "MIT", "url": "carts/chat/LICENSE.txt",
                        "size": len(lic), "sha256": validate.sha256(lic)},
            "source": info["source"],
            "release": "%s/releases/tag/chat-v1" % validate.REPO_URL,
            "assets": [{"name": asset["name"], "url": url + asset["name"],
                        "size": asset["size"], "sha256": asset["sha256"],
                        "files": info["files"],
                        "mirror": "releases/chat-v1/" + asset["name"]}],
            "external": [],
            "build": {"commit": commit, "keys": {}, "modules": {}, "pins": {}},
        }
        index = self.index()
        index["carts"].append(entry)
        return index, entry, info

    def test_build_and_validate_name_the_same_folder_carts(self):
        self.assertEqual(build.FOLDER_RUNTIMES, validate.FOLDER_RUNTIMES)

    def test_a_good_lua_cart_passes(self):
        self.assertEqual(self.errors(), [])

    def test_it_builds_into_its_folder_and_its_index_entry_passes(self):
        index, entry, info = self.built_entry()
        self.assertNotIn("source_asset", info)
        self.assertEqual(sorted(info["files"]), ["LICENSE.txt", "README.md", "main.lua",
                                                 "manifest.json", "service.lua"])
        zpath = self.path("build", "dist", ID, "chat.moy.zip")
        with zipfile.ZipFile(zpath) as z:
            for n in z.namelist():
                with open(self.path("carts", ID, n.split("/", 1)[1]), "rb") as f:
                    self.assertEqual(z.read(n), f.read(), n)
        self.assertEqual(self.errors(index), [])
        rep = validate.Report()
        self.assertTrue(validate.check_folder_source(entry, entry["assets"][0], ID, rep),
                        rep.errors)
        kept = os.path.join(self.tmp, "chat.moy.zip")
        shutil.copy(zpath, kept)
        with contextlib.redirect_stdout(io.StringIO()):
            build.verify_release(ID, [kept], None, entry["build"]["commit"])

    def test_an_index_entry_with_a_source_bundle_or_chips_fails(self):
        index, entry, _info = self.built_entry()
        entry["source_asset"] = {"name": "chat-v1-source.tar.gz", "url": "https://x",
                                 "size": 1, "sha256": "0" * 64}
        self.assertFails("a lua cart has no source bundle", index)
        del entry["source_asset"]
        entry["chips"] = ["esp32s3"]
        self.assertFails("a lua cart is compiled for no chip", index)

    # -- refused ------------------------------------------------------------------

    def test_a_missing_main_or_service_is_refused(self):
        os.remove(self.path("carts", ID, "service.lua"))
        self.assertFails("carts/chat: its service, service.lua, is missing")
        self.edit("manifest.json", lambda d: d.update(main="main.py"))
        self.assertFails("main must name a .lua in the cart's folder")

    def test_other_code_or_a_folder_is_refused(self):
        self.write("helper.py", "print(1)\n")
        self.assertFails("carts/chat/helper.py: a Lua cart holds its scripts")
        os.remove(self.path("carts", ID, "helper.py"))
        os.makedirs(self.path("carts", ID, "lib"))
        self.assertFails("carts/chat/lib: a Lua cart's folder is flat")

    def test_a_folder_that_is_not_the_id_is_refused(self):
        self.edit("cart.json", lambda d: d.update(folder="moybyte.chat.moy"))
        self.assertFails("a Lua cart's folder is chat.moy")

    def test_chips_or_memory_are_refused(self):
        self.edit("cart.json", lambda d: d.update(chips=["esp32s3"]))
        self.assertFails("carts/chat/cart.json: a Lua cart has no chips")
        self.edit("manifest.json", lambda d: d.update(memory=4))
        self.assertFails("a Lua cart plans no memory")

    def test_a_refused_lua_cart_does_not_build(self):
        os.remove(self.path("carts", ID, "service.lua"))
        commit = self.commit()
        with self.assertRaisesRegex(build.BuildError, "not a lua cart a console loads"):
            build.build(ID, self.path("build"), None, commit)


if __name__ == "__main__":
    unittest.main()
