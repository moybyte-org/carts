# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Nikola Jovicic
"""scripts/validate.py's licence check, held to the cases it exists for: a
cart whose licence file is missing, or disagrees with its SPDX identifier,
fails, and so does a cart that takes source from another.

    python3 -m unittest discover -s tests

Each case copies this repository's carts/, LICENSE and tooling into a
temporary folder, breaks one thing, and runs check_licences on the copy with
the index as it stands. Standard library only.
"""

import json
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import validate  # noqa: E402


class Licences(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.root = os.path.join(self.tmp, "repo")
        os.makedirs(self.root)
        ignore = shutil.ignore_patterns("__pycache__")
        for name in ("carts", "scripts", "tests"):
            shutil.copytree(os.path.join(ROOT, name), os.path.join(self.root, name),
                            ignore=ignore)
        for name in ("LICENSE", "install.py", "index.json"):
            shutil.copy(os.path.join(ROOT, name), self.root)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def path(self, *parts):
        return os.path.join(self.root, *parts)

    def index(self):
        with open(self.path("index.json"), encoding="utf-8") as f:
            return json.load(f)

    def errors(self, index=None):
        rep = validate.Report()
        validate.check_licences(self.root, self.index() if index is None else index, rep)
        return rep.errors

    def edit_json(self, rel, change):
        with open(self.path(*rel.split("/")), encoding="utf-8") as f:
            data = json.load(f)
        change(data)
        with open(self.path(*rel.split("/")), "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    def assertFails(self, needle, index=None):
        errs = self.errors(index)
        self.assertTrue(any(needle in e for e in errs),
                        "no error mentions %r; got %r" % (needle, errs))

    def test_the_repository_passes(self):
        self.assertEqual(self.errors(), [])

    def test_a_missing_licence_file_fails(self):
        os.remove(self.path("carts", "teapot", "LICENSES.txt"))
        self.assertFails("carts/teapot: its licence file LICENSES.txt is missing")

    def test_doom_without_its_licence_file_fails(self):
        os.remove(self.path("carts", "doom", "NOTICE"))
        self.assertFails("carts/doom: its licence file NOTICE is missing")

    def test_a_licence_file_outside_the_folder_fails(self):
        self.edit_json("carts/doom/cart.json",
                       lambda d: d["licence"].update(file="../../LICENSE"))
        self.assertFails("is outside its folder")

    def test_an_mit_text_under_a_gpl_identifier_fails(self):
        self.edit_json("carts/teapot/cart.json",
                       lambda d: d["licence"].update(spdx="GPL-2.0-or-later"))
        errs = self.errors()
        self.assertTrue(any("does not name GPL-2.0-or-later" in e for e in errs), errs)
        self.assertTrue(any("is not GPL-2.0-or-later's licence" in e for e in errs), errs)
        self.assertTrue(any("needs its full text" in e for e in errs), errs)

    def test_a_gpl_notice_under_mit_fails(self):
        self.edit_json("carts/doom/cart.json", lambda d: d["licence"].update(spdx="MIT"))
        self.assertFails("carts/doom/NOTICE is not MIT's licence")
        self.assertFails("carts/doom/cover.py says it is GPL-2.0-or-later, and the cart is MIT")

    def test_a_gpl_cart_without_the_gpl_text_fails(self):
        os.remove(self.path("carts", "doom", "LICENSE"))
        self.assertFails("carts/doom: GPL-2.0-or-later needs its full text")

    def test_an_index_that_disagrees_with_the_cart_fails(self):
        index = self.index()
        for cart in index["carts"]:
            if cart["id"] == "esp88":
                cart["licence"]["spdx"] = "GPL-2.0-or-later"
        self.assertFails("index.json esp88: the licence is 'GPL-2.0-or-later'", index)

    def test_a_licence_validate_does_not_know_fails(self):
        self.edit_json("carts/esp88/cart.json",
                       lambda d: d["licence"].update(spdx="CC-BY-NC-4.0"))
        self.assertFails("does not know the words of CC-BY-NC-4.0")

    def test_a_file_under_another_licence_needs_a_word_in_the_notice(self):
        with open(self.path("carts", "doom", "extra.c"), "w") as f:
            f.write("/* SPDX-License-Identifier: MIT */\nint x;\n")
        self.assertFails("carts/doom/extra.c says it is MIT")

    def test_a_gpl_file_copied_into_an_mit_cart_fails(self):
        shutil.copy(self.path("carts", "doom", "dg_sound.c"),
                    self.path("carts", "teapot", "src", "dg_sound.c"))
        errs = self.errors()
        self.assertTrue(any("is a copy of carts/doom/dg_sound.c" in e for e in errs), errs)
        self.assertTrue(any("carts/teapot/src/dg_sound.c says it is GPL-2.0-or-later" in e
                            for e in errs), errs)

    def test_a_recipe_reaching_into_another_cart_fails(self):
        with open(self.path("carts", "esp88", "recipe.py"), "a") as f:
            f.write('\nSHARED = "../doom/dg_cart.c"\n')
        self.assertFails("carts/esp88/recipe.py reaches into carts/doom/")

    def test_tooling_that_is_not_mit_fails(self):
        with open(self.path("scripts", "helper.py"), "w") as f:
            f.write("# SPDX-License-Identifier: GPL-2.0-or-later\n")
        self.assertFails("scripts/helper.py is the tooling's, which is MIT")

    def test_a_root_licence_that_is_not_mit_fails(self):
        shutil.copy(self.path("carts", "doom", "LICENSE"), self.path("LICENSE"))
        self.assertFails("LICENSE is not the MIT licence")


if __name__ == "__main__":
    unittest.main()
