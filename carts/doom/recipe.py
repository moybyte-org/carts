# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 Nikola Jovicic
"""Doom as a compiled Moybyte cart: the recipe scripts/build.py runs.

Fetches doomgeneric at a pinned commit (checked by the sha256 of its tree,
because GitHub builds the tarball on request and does not promise its bytes),
stages its engine with dg_cart.c, patches the seams below, and links
main.wasm with wasi-sdk on the console's "moy" imports and nothing else. It
writes the cart folder's main.wasm, manifest.json, config.json and
LICENSES.txt; scripts/build.py adds the signed per-chip modules and
SOURCE.txt.

The WAD is not an input and never an output. LICENSES.txt carries id's
licence as licenses/doom1.wad.txt has it, which this recipe checks against
Debian's doom-wad-shareware packaging (its debian/copyright) by sha256.

The memory the manifest declares is derived, never guessed: the module's
stack (STACK_KB) and static data, the zone config.json names (Doom's -mb, in
whole MiB), and the heap Doom mallocs outside the zone (HEAP_KB), rounded up
to whole 64 KiB pages. A manifest that disagrees is a failed build.
"""

import hashlib
import io
import json
import math
import os
import shutil
import subprocess
import tarfile

# doomgeneric at the commit Moybyte's wasm tier was measured with. The pin is
# the sha256 of the TREE: every file's path and sha256, sorted, one per line.
DOOMGENERIC_COMMIT = "dcb7a8dbc7a16ce3dda29382ac9aae9d77d21284"
DOOMGENERIC_URL = "https://codeload.github.com/ozkl/doomgeneric/tar.gz/" + DOOMGENERIC_COMMIT
DOOMGENERIC_TREE_SHA256 = "57e10d1c9c5fbd4312b1d3d88a54d9007f5490ed0fb172fd5cc0ff8ac6396098"

# Debian's packaging of the shareware WAD, whose copyright file carries id's
# licence text.
DEBIAN_PACKAGING = ("https://deb.debian.org/debian/pool/non-free/d/doom-wad-shareware/"
                    "doom-wad-shareware_1.9.fixed-5.debian.tar.xz",
                    "57ee8d82c6533475a7001297dca396a0645b96f37801c1cf0741e014c352b3b3")

STACK_KB = 64
HEAP_KB = 64
PAGE = 65536

# The engine's object list is doomgeneric's own Makefile's, less its platform
# halves and the stdio WAD class: dg_cart.c carries both.
OBJS = """dummy am_map doomdef doomstat dstrings d_event d_items d_iwad d_loop
d_main d_mode d_net f_finale f_wipe g_game hu_lib hu_stuff info i_cdmus
i_endoom i_joystick i_scale i_sound i_system i_timer memio m_argv m_bbox
m_cheat m_config m_controls m_fixed m_menu m_misc m_random p_ceilng p_doors
p_enemy p_floor p_inter p_lights p_map p_maputl p_mobj p_plats p_pspr
p_saveg p_setup p_sight p_spec p_switch p_telept p_tick p_user r_bsp r_data
r_draw r_main r_plane r_segs r_sky r_things sha1 sounds statdump st_lib
st_stuff s_sound tables v_video wi_stuff w_checksum w_main w_wad z_zone
i_input i_video doomgeneric dg_cart""".split()

CFLAGS = ["--target=wasm32-wasi", "-O2", "-DNORMALUNIX", "-DLINUX", "-DSNDSERV",
          "-D_DEFAULT_SOURCE", "-DDOOMGENERIC_RESX=320", "-DDOOMGENERIC_RESY=200",
          "-DCMAP256", "-Wno-everything", "-fno-strict-aliasing"]

# Where the engine meets the cart: each (file, the upstream text, what it
# becomes). An anchor that moved is a failed build, never a silent one.
PATCHES = (
    # The IWAD search asks the filesystem whether doom1.wad exists; the cart's
    # folder owns it, and `read` is how it is reached.
    ("m_misc.c",
     "boolean M_FileExists(char *filename)\n{\n    FILE *fstream;\n",
     "boolean M_FileExists(char *filename)\n{\n    FILE *fstream;\n\n"
     "    if (!strcmp(filename, \"doom1.wad\"))\n    {\n        return true;\n    }\n"),
    # Doom draws straight into the cart's 320 x 240 frame, letterboxed.
    ("doomgeneric.c",
     "DG_ScreenBuffer = malloc(DOOMGENERIC_RESX * DOOMGENERIC_RESY * 4);",
     "DG_ScreenBuffer = DG_ScreenMemory();"),
    ("doomgeneric.h",
     "void doomgeneric_Create(int argc, char **argv);",
     "void doomgeneric_Create(int argc, char **argv);\npixel_t *DG_ScreenMemory(void);"),
    # No tic due is a return, never a wait: the cart waits by returning.
    ("d_loop.c",
     "\t    return;\n\t}\n\n        I_Sleep(1);\n    }",
     "\t    return;\n\t}\n\n        return;\n    }"),
    # The frame CRC and the zone's low-water mark, after every game tic.
    ("d_loop.c",
     "\t    gametic++;\n",
     "\t    gametic++;\n\t    DG_AfterTic();\n"),
    ("d_loop.c",
     "void TryRunTics (void)\n{",
     "void DG_AfterTic(void);\n\nvoid TryRunTics (void)\n{"),
    # The screen melt: a step a frame, never a loop waiting on the clock.
    ("d_main.c",
     "    if (nodrawers)\n    \treturn;                    // for comparative timing / profiling\n",
     "    if (nodrawers)\n    \treturn;                    // for comparative timing / profiling\n"
     "    if (DG_WipeStep())\n    \treturn;\n"),
    ("d_main.c",
     "    wipestart = I_GetTime () - 1;\n\n    do\n    {\n\tdo\n\t{\n"
     "\t    nowtime = I_GetTime ();\n\t    tics = nowtime - wipestart;\n"
     "            I_Sleep(1);\n\t} while (tics <= 0);\n        \n"
     "\twipestart = nowtime;\n\tdone = wipe_ScreenWipe(wipe_Melt\n"
     "\t\t\t       , 0, 0, SCREENWIDTH, SCREENHEIGHT, tics);\n"
     "\tI_UpdateNoBlit ();\n\tM_Drawer ();                            "
     "// menu is drawn even on top of wipes\n"
     "\tI_FinishUpdate ();                      // page flip or blit buffer\n"
     "    } while (!done);\n",
     "    DG_WipeBegin();\n"),
    ("d_main.c",
     "void D_Display (void)\n{",
     "void DG_WipeBegin(void);\nint DG_WipeStep(void);\n\nvoid D_Display (void)\n{"),
)

# What each patched file says about its change (GPL-2.0 section 2a). The
# notice goes at the end of the file, so no line number moves.
CHANGED = "2026-09-26"
CHANGES = {
    "m_misc.c": "M_FileExists answers true for doom1.wad, the cart's own file",
    "doomgeneric.c": "DG_ScreenBuffer is the cart's frame (DG_ScreenMemory)",
    "doomgeneric.h": "declares DG_ScreenMemory",
    "d_loop.c": "TryRunTics returns when no tic is due instead of sleeping, and "
                "calls DG_AfterTic after every game tic",
    "d_main.c": "D_Display begins the screen melt and steps it once a frame "
                "(DG_WipeBegin, DG_WipeStep) instead of looping on the clock",
}

CART_NOTICE = """\
Doom for Moybyte consoles.

The engine is doomgeneric -- id Software's DOOM source release by way of
Chocolate Doom, in ozkl's portable fork -- under the GNU General Public
License, version 2 or (at your option) any later version. The platform glue,
dg_cart.c, is under the same licence. The complete corresponding source, and
the recipe that builds this cart from it, is named in SOURCE.txt.

doom1.wad is not part of the download this cart comes in. The installer
fetches it from Debian's archive and checks it by sha256. It is id Software's
shareware DOOM v1.9, under id's Limited Use Software License, which is at the
end of this file: it may be copied and given to others, unmodified; nobody
may charge or receive anything for it; no modification or derivative works.
"""

GPL_NOTICE = """\
doomgeneric's LICENSE, as fetched at the pinned commit:
"""


def doomgeneric(ctx):
    """{path: bytes} of doomgeneric at the pinned commit, its tree checked."""
    data = ctx.fetch(DOOMGENERIC_URL, None, "doomgeneric-%s.tar.gz" % DOOMGENERIC_COMMIT[:12])
    files = {}
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        for m in tar.getmembers():
            if m.isfile():
                files[m.name.split("/", 1)[1]] = tar.extractfile(m).read()
    lines = sorted("%s %s" % (p, _sha256(d)) for p, d in files.items())
    got = _sha256("\n".join(lines).encode())
    if got != DOOMGENERIC_TREE_SHA256:
        raise ctx.error("doomgeneric %s: the tree hashes to %s; the pin is %s (delete "
                        "build/cache/doomgeneric-*.tar.gz to refetch)"
                        % (DOOMGENERIC_COMMIT[:12], got, DOOMGENERIC_TREE_SHA256))
    return files


def id_licence(ctx):
    """id's licence text: licenses/doom1.wad.txt, checked against Debian's."""
    packaging = ctx.fetch(DEBIAN_PACKAGING[0], DEBIAN_PACKAGING[1],
                          "doom-wad-shareware.debian.tar.xz")
    with tarfile.open(fileobj=io.BytesIO(packaging), mode="r:xz") as tar:
        text = tar.extractfile("debian/copyright").read().decode()
    excerpt = text[text.index("Files: doom1.wad"):text.index("Files: debian/*")].rstrip() + "\n"
    ours = ctx.read(os.path.join("licenses", "doom1.wad.txt")).decode()
    if ours != excerpt:
        raise ctx.error("licenses/doom1.wad.txt is not the text Debian's debian/copyright "
                        "carries for doom1.wad")
    return ours


def _sha256(data):
    return hashlib.sha256(data).hexdigest()


def stage(ctx, sources):
    """doomgeneric's engine and dg_cart.c in the stage, patched."""
    for path, data in sources.items():
        if path.startswith("doomgeneric/") and path.count("/") == 1 \
                and path.endswith((".c", ".h")):
            with open(os.path.join(ctx.stage, path.split("/")[1]), "wb") as f:
                f.write(data)
    shutil.copy(os.path.join(ctx.cart_dir, "dg_cart.c"), ctx.stage)
    for name, old, new in PATCHES:
        p = os.path.join(ctx.stage, name)
        with open(p, encoding="latin-1") as f:
            text = f.read()
        if text.count(old) != 1:
            raise ctx.error("%s: the patch anchor %r moved" % (name, old[:40]))
        with open(p, "w", encoding="latin-1", newline="") as f:
            f.write(text.replace(old, new, 1))
    if set(CHANGES) != {name for name, _old, _new in PATCHES}:
        raise ctx.error("CHANGES must describe every patched file")
    for name, what in sorted(CHANGES.items()):
        with open(os.path.join(ctx.stage, name), "a", encoding="latin-1", newline="") as f:
            f.write("\n/* Changed for Moybyte's compiled-cart tier by gpl-carts'\n"
                    " * carts/doom/recipe.py, %s: %s. */\n" % (CHANGED, what))


def link(ctx, memory):
    """main.wasm at `memory` bytes of linear memory; its bytes. The stack goes
    first, so an overflow leaves linear memory and traps rather than running
    into the data. __FILE__ (z_zone.h's Z_ChangeTag names its caller) is the
    bare file name, so the build does not depend on where it ran."""
    out = os.path.join(ctx.stage, "main.wasm")
    cmd = ([os.path.join(ctx.wasi_sdk, "bin", "clang")] + CFLAGS
           + ["-ffile-prefix-map=%s/=%s" % (ctx.stage, ctx.file_prefix),
              "-nostartfiles", "-Wl,--no-entry", "-Wl,--strip-all",
              "-Wl,--initial-memory=%d" % memory, "-Wl,--max-memory=%d" % memory,
              "-Wl,--stack-first", "-Wl,-z,stack-size=%d" % (STACK_KB * 1024),
              "-Wl,--export=__heap_base", "-o", out]
           + [os.path.join(ctx.stage, o + ".c") for o in OBJS])
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise ctx.error("clang failed:\n%s%s" % (r.stdout, r.stderr))
    with open(out, "rb") as f:
        return f.read()


def imports(ctx, wasm):
    """[(module, name)] a wasm binary imports."""
    wm = ctx.wasm_module
    out = []
    for sid, _name, body in wm.sections(wasm):
        if sid != 2:
            continue
        n, i = wm._read_leb(body, 0)
        for _ in range(n):
            ml, i = wm._read_leb(body, i)
            mod = body[i:i + ml].decode()
            i += ml
            nl, i = wm._read_leb(body, i)
            name = body[i:i + nl].decode()
            i += nl
            kind = body[i]
            i += 1
            if kind != 0:
                raise ctx.error("imports %s.%s as something other than a function"
                                % (mod, name))
            _t, i = wm._read_leb(body, i)
            out.append((mod, name))
    return out


def heap_base(ctx, wasm):
    """The exported __heap_base global's value: where static data and the
    stack end."""
    wm = ctx.wasm_module
    idx = None
    for sid, _name, body in wm.sections(wasm):
        if sid == 7:
            n, i = wm._read_leb(body, 0)
            for _ in range(n):
                nl, i = wm._read_leb(body, i)
                name = body[i:i + nl].decode()
                i += nl
                kind = body[i]
                gi, i = wm._read_leb(body, i + 1)
                if kind == 3 and name == "__heap_base":
                    idx = gi
    for sid, _name, body in wm.sections(wasm):
        if sid == 6:
            n, i = wm._read_leb(body, 0)
            for g in range(n):
                i += 2                           # valtype, mutability
                if body[i] != 0x41:
                    raise ctx.error("a global not initialised by i32.const")
                v, i = wm._read_leb(body, i + 1)
                i += 1                           # end
                if g == idx:
                    return v
    raise ctx.error("no __heap_base export")


def build(ctx, folder):
    """Write the cart folder's main.wasm, manifest.json, config.json and
    LICENSES.txt into `folder`. Returns {"wasm": bytes, "pins": {...}}."""
    manifest_bytes = ctx.read("manifest.json")
    config_bytes = ctx.read("config.json")
    manifest = json.loads(manifest_bytes)
    zone_mb = int(json.loads(config_bytes)["zone"])
    sources = doomgeneric(ctx)
    licence = id_licence(ctx)

    stage(ctx, sources)
    base = heap_base(ctx, link(ctx, 16 << 20))
    memory = math.ceil((base + (zone_mb << 20) + HEAP_KB * 1024) / PAGE) * PAGE
    if manifest.get("memory") != memory // PAGE:
        raise ctx.error("manifest.json declares %r pages; static data and stack (%d KB), "
                        "the %d MiB zone and a %d KB heap need %d"
                        % (manifest.get("memory"), base // 1024, zone_mb, HEAP_KB,
                           memory // PAGE))
    wasm = link(ctx, memory)
    foreign = ["%s.%s" % mn for mn in imports(ctx, wasm) if mn[0] != "moy"]
    if foreign:
        raise ctx.error("main.wasm imports outside \"moy\": %s" % ", ".join(foreign))

    with open(os.path.join(folder, manifest.get("main", "main.wasm")), "wb") as f:
        f.write(wasm)
    with open(os.path.join(folder, "manifest.json"), "wb") as f:
        f.write(manifest_bytes)
    with open(os.path.join(folder, "config.json"), "wb") as f:
        f.write(config_bytes)
    with open(os.path.join(folder, "LICENSES.txt"), "w", encoding="utf-8", newline="\n") as f:
        f.write(CART_NOTICE + "\n" + GPL_NOTICE + "\n" + sources["LICENSE"].decode("latin-1")
                + "\n" + licence)
    print("doom: memory %d pages (static data and stack %d KB, zone %d MiB, heap %d KB)"
          % (memory // PAGE, base // 1024, zone_mb, HEAP_KB))
    return {
        "wasm": wasm,
        "pins": {
            "doomgeneric": {
                "url": "https://github.com/ozkl/doomgeneric/tree/" + DOOMGENERIC_COMMIT,
                "commit": DOOMGENERIC_COMMIT,
                "tree_sha256": DOOMGENERIC_TREE_SHA256,
            },
        },
    }
