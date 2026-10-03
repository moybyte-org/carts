# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Nikola Jovicic
"""The recipe for this cart: scripts/jet_recipe.py, which builds it with
Moybyte's tools/jet_cart.py at the moybyte commit scripts/build.py pins, and
names what of moybyte and of this repository's tooling that build takes."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "scripts"))

import jet_recipe  # noqa: E402

MOYBYTE_FILES = jet_recipe.MOYBYTE_FILES
MOYBYTE_SHA256 = jet_recipe.MOYBYTE_SHA256
MOYBYTE_LICENCES = jet_recipe.MOYBYTE_LICENCES
TOOLING = jet_recipe.TOOLING


def build(ctx, folder):
    return jet_recipe.build(ctx, folder)
