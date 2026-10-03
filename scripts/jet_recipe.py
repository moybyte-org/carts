# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Nikola Jovicic
"""The recipe both Jet carts run (each cart's recipe.py hands it its id).

A Jet cart's main.wasm is built by Moybyte's tools/jet_cart.py, at the
moybyte commit scripts/build.py pins: the flags, the units and, for the
teapot, Jet compiled twice into one module all have that one home, and
Moybyte's own tests and on-glass guards build its vendored copy of these
carts with the same tool. This stages a moybyte-shaped tree -- the pinned
build path, Jet and the film's code as moybyte vendors them, and this cart's
folder where jet_cart.py looks for it -- runs the build there, and writes the
release's cart: the folder's own files, flat, and main.wasm. The build maps
the stage's path out of the binary, so the module does not depend on where it
was built.

A cart folder here holds cart.json, recipe.py and README.md for this
repository, and everything else is the cart: its manifest, config, cover,
licences and data go into the release as they are, and its src/ is compiled.
"""

import importlib.util
import os
import shutil

# Where Jet and the film's code come from, as moybyte's ports/jet/jet_vendor.json
# records them at the pinned commit.
JET = {"url": "https://github.com/CubeCoders/Jet/tree/"
              "19018b52c04d92c615d1ade2db5eb7198c7af21c",
       "commit": "19018b52c04d92c615d1ade2db5eb7198c7af21c",
       "note": "as moybyte vendors it at the pinned commit (ports/jet/jet)"}
EXAMPLES = {"url": "https://github.com/CubeCoders/JetExamples/tree/"
                   "57b05a240f5bcaa02a18d62a1206ce7053dc09dc",
            "commit": "57b05a240f5bcaa02a18d62a1206ce7053dc09dc",
            "note": "the example the cart ports, and the film's code as moybyte "
                    "vendors it (ports/jet/examples)"}

# This repository's files in a cart folder, never the cart's.
REPO_FILES = ("cart.json", "recipe.py", "README.md")

# What a Jet cart's build takes from moybyte beyond scripts/build.py's
# MOYBYTE_FILES, at the same commit: the Jet carts' build (the flags, and Jet
# compiled twice into the teapot), the console's import table it checks a
# module against, and Jet and ESP 88's film code as moybyte vendors them from
# CubeCoders (its ports/jet/jet_vendor.json names the upstream commits, which
# JET and EXAMPLES repeat). MOYBYTE_SHA256 is their tree hash, as
# scripts/build.py computes one.
MOYBYTE_FILES = (
    "native/moycore/libmoy/moy_wasm.c",     # the console's import table jet_cart.py checks against
    "tools/jet_cart.py",                    # the Jet carts' build: flags, units, the teapot's two Jets
    "ports/jet/examples/LICENSE",
    "ports/jet/examples/esp32-neon-film/main/City.hpp",
    "ports/jet/examples/esp32-neon-film/main/Film.hpp",
    "ports/jet/examples/esp32-neon-film/main/RoadTrack.hpp",
    "ports/jet/examples/esp32-neon-film/main/Vehicle.hpp",
    "ports/jet/examples/esp32-neon-film/main/World.hpp",
    "ports/jet/examples/esp32-neon-film/main/firmware/JetConfig.hpp",
    "ports/jet/jet/LICENSE",
    "ports/jet/jet/src/BlendSpans.cpp",
    "ports/jet/jet/src/BlendSpans.hpp",
    "ports/jet/jet/src/Camera.cpp",
    "ports/jet/jet/src/Camera.hpp",
    "ports/jet/jet/src/DepthBuckets.hpp",
    "ports/jet/jet/src/EnvironmentMapping.hpp",
    "ports/jet/jet/src/FastMath.hpp",
    "ports/jet/jet/src/Light.cpp",
    "ports/jet/jet/src/Light.hpp",
    "ports/jet/jet/src/Material.cpp",
    "ports/jet/jet/src/Material.hpp",
    "ports/jet/jet/src/Math.hpp",
    "ports/jet/jet/src/ObjLoader.h",
    "ports/jet/jet/src/Object.cpp",
    "ports/jet/jet/src/Object.hpp",
    "ports/jet/jet/src/ParticleSystem.hpp",
    "ports/jet/jet/src/Picking.hpp",
    "ports/jet/jet/src/PostFX.cpp",
    "ports/jet/jet/src/PostFX.hpp",
    "ports/jet/jet/src/Primitives.cpp",
    "ports/jet/jet/src/Primitives.hpp",
    "ports/jet/jet/src/Renderer.cpp",
    "ports/jet/jet/src/Renderer.hpp",
    "ports/jet/jet/src/Scene.cpp",
    "ports/jet/jet/src/Scene.hpp",
    "ports/jet/jet/src/Shader.hpp",
    "ports/jet/jet/src/Specular.hpp",
    "ports/jet/jet/src/Sprite2D.cpp",
    "ports/jet/jet/src/Sprite2D.hpp",
    "ports/jet/jet/src/Texture.cpp",
    "ports/jet/jet/src/Texture.hpp",
    "ports/jet/jet/src/TextureSpans.hpp",
    "ports/jet/jet/src/TriangleSpans.hpp",
    "ports/jet/jet/src/TrigLUT.cpp",
    "ports/jet/jet/src/TrigLUT.hpp",
)
MOYBYTE_SHA256 = "ad8748588cea4d00f3a1e010f06e04914848dcb8881b0e777ee3f688c28496fc"
MOYBYTE_LICENCES = (
    ("MIT (Moybyte)", ("native/moycore/libmoy/moy_wasm.c", "tools/jet_cart.py"),
     ("moybyte/native/moycore/libmoy/moy_wasm.c is moy-spec's too.",)),
    ("MIT (CubeCoders Limited)", ("ports/jet/jet/", "ports/jet/examples/"),
     ("Jet and JetExamples' code; each folder carries its LICENSE.",)),
)

# This repository's files outside the cart's folder that its build imports.
TOOLING = ("scripts/jet_recipe.py",)


def build(ctx, folder):
    root = os.path.join(ctx.stage, "moybyte")
    shutil.copytree(ctx.moybyte, root)
    spec = importlib.util.spec_from_file_location(
        "jet_cart_%s" % ctx.cart_id, os.path.join(root, "tools", "jet_cart.py"))
    jet_cart = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(jet_cart)
    if ctx.cart_id not in jet_cart.CARTS:
        raise ctx.error("moybyte's jet_cart.py builds %s, not %r"
                        % (", ".join(sorted(jet_cart.CARTS)), ctx.cart_id))
    staged = jet_cart.cart_spec(ctx.cart_id).folder
    shutil.copytree(ctx.cart_dir, staged,
                    ignore=shutil.ignore_patterns("__pycache__", *REPO_FILES))
    try:
        wasm = jet_cart.compile_wasm(sdk=ctx.wasi_sdk, cart=ctx.cart_id)
    except jet_cart.BuildError as exc:
        raise ctx.error(str(exc))

    main = jet_cart.manifest(ctx.cart_id).get("main", "main.wasm")
    for name in sorted(os.listdir(ctx.cart_dir)):
        path = os.path.join(ctx.cart_dir, name)
        if os.path.isfile(path) and name not in REPO_FILES:
            shutil.copyfile(path, os.path.join(folder, name))
    with open(os.path.join(folder, main), "wb") as f:
        f.write(wasm)
    folder_name = os.path.basename(staged)
    return {"wasm": wasm, "pins": {"jet": JET, "jetexamples": EXAMPLES},
            "rebuild": {"lines": ["cp -r carts/%s moybyte/ports/jet/%s" % (ctx.cart_id, folder_name),
                                  "WASI_SDK_PATH=$WASI_SDK python3 moybyte/tools/jet_cart.py out "
                                  "--cart %s" % ctx.cart_id],
                        "wasm": "out/%s/%s" % (folder_name, main)}}
