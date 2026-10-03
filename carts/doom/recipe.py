# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 Nikola Jovicic
"""Doom as a compiled Moybyte cart: the recipe scripts/build.py runs.

Fetches doomgeneric at a pinned commit (checked by the sha256 of its tree,
because GitHub builds the tarball on request and does not promise its bytes),
stages its engine with dg_cart.c and dg_sound.c, patches the seams below, and
links main.wasm with wasi-sdk on the console's "moy" imports and nothing else. It
writes the cart folder's main.wasm, manifest.json, config.json, LICENSES.txt
and cover.png (cover.py draws it); scripts/build.py adds the signed per-chip
modules and SOURCE.txt.

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
# halves and the stdio WAD class: dg_cart.c carries both, and dg_sound.c the
# sound module.
OBJS = """dummy am_map doomdef doomstat dstrings d_event d_items d_iwad d_loop
d_main d_mode d_net f_finale f_wipe g_game hu_lib hu_stuff info i_cdmus
i_endoom i_joystick i_scale i_sound i_system i_timer memio m_argv m_bbox
m_cheat m_config m_controls m_fixed m_menu m_misc m_random p_ceilng p_doors
p_enemy p_floor p_inter p_lights p_map p_maputl p_mobj p_plats p_pspr
p_saveg p_setup p_sight p_spec p_switch p_telept p_tick p_user r_bsp r_data
r_draw r_main r_plane r_segs r_sky r_things sha1 sounds statdump st_lib
st_stuff s_sound tables v_video wi_stuff w_checksum w_main w_wad z_zone
i_input i_video doomgeneric dg_cart dg_sound""".split()

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
    # The sound module is dg_sound.c's, with no SDL_mixer behind it. Music
    # stays off: no music module is compiled in.
    ("i_sound.c",
     "static sound_module_t *sound_modules[] = \n{\n    #ifdef FEATURE_SOUND\n"
     "    &DG_sound_module,\n    #endif\n    NULL,\n};",
     "extern sound_module_t DG_sound_module;\n\n"
     "static sound_module_t *sound_modules[] = \n{\n    &DG_sound_module,\n    NULL,\n};"),
    # Z_Malloc scans from the rover once around to the block before it, so a
    # run of free and purgeable blocks that starts just behind the rover is
    # never found: the wipe's second screen found no room beside 100 KB of
    # such a run once the sound effects' lumps shared the 1 MiB zone. Before
    # it fails, the search goes over the whole zone from its first block.
    ("z_zone.c",
     "    rover = base;\n    start = base->prev;\n\t\n    do\n    {\n"
     "        if (rover == start)\n        {\n"
     "            // scanned all the way around the list\n"
     "            I_Error (\"Z_Malloc: failed on allocation of %i bytes\", size);\n"
     "        }\n",
     "    rover = base;\n    start = base->prev;\n    int whole = 0;\n\t\n    do\n    {\n"
     "        if (rover == start && !whole)\n        {\n"
     "            whole = 1;\n"
     "            base = rover = mainzone->blocklist.next;\n"
     "            start = &mainzone->blocklist;\n"
     "            continue;\n"
     "        }\n"
     "        if (rover == start)\n        {\n"
     "            // scanned all the way around the list\n"
     "            I_Error (\"Z_Malloc: failed on allocation of %i bytes\", size);\n"
     "        }\n"),
    # The melt reads its two screens row by row where they lie, rather than
    # transposing each through a third 64 KB block first: in a 1 MiB zone,
    # the sound effects' lumps leave room for two screens and not three.
    ("f_wipe.c",
     "    // makes this wipe faster (in theory)\n    // to have stuff in column-major format\n"
     "    wipe_shittyColMajorXform((short*)wipe_scr_start, width/2, height);\n"
     "    wipe_shittyColMajorXform((short*)wipe_scr_end, width/2, height);\n",
     ""),
    ("f_wipe.c",
     "\t\ts = &((short *)wipe_scr_end)[i*height+y[i]];\n"
     "\t\td = &((short *)wipe_scr)[y[i]*width+i];\n"
     "\t\tidx = 0;\n"
     "\t\tfor (j=dy;j;j--)\n"
     "\t\t{\n"
     "\t\t    d[idx] = *(s++);\n"
     "\t\t    idx += width;\n"
     "\t\t}\n"
     "\t\ty[i] += dy;\n"
     "\t\ts = &((short *)wipe_scr_start)[i*height];\n"
     "\t\td = &((short *)wipe_scr)[y[i]*width+i];\n"
     "\t\tidx = 0;\n"
     "\t\tfor (j=height-y[i];j;j--)\n"
     "\t\t{\n"
     "\t\t    d[idx] = *(s++);\n"
     "\t\t    idx += width;\n"
     "\t\t}\n",
     "\t\ts = &((short *)wipe_scr_end)[y[i]*width+i];\n"
     "\t\td = &((short *)wipe_scr)[y[i]*width+i];\n"
     "\t\tidx = 0;\n"
     "\t\tfor (j=dy;j;j--)\n"
     "\t\t{\n"
     "\t\t    d[idx] = s[idx];\n"
     "\t\t    idx += width;\n"
     "\t\t}\n"
     "\t\ty[i] += dy;\n"
     "\t\ts = &((short *)wipe_scr_start)[i];\n"
     "\t\td = &((short *)wipe_scr)[y[i]*width+i];\n"
     "\t\tidx = 0;\n"
     "\t\tfor (j=height-y[i];j;j--)\n"
     "\t\t{\n"
     "\t\t    d[idx] = s[idx];\n"
     "\t\t    idx += width;\n"
     "\t\t}\n"),
    # I_Error runs the exit functions, and a function called through a
    # pointer of another type is a trap in wasm: G_CheckDemoStatus returns a
    # boolean, so an error during a demo trapped before it could be reported.
    ("d_main.c",
     "    I_AtExit((atexit_func_t) G_CheckDemoStatus, true);",
     "    I_AtExit(D_CheckDemoStatusAtExit, true);"),
    ("d_main.c",
     "void D_DoomMain (void)\n{",
     "static void D_CheckDemoStatusAtExit(void)\n{\n    G_CheckDemoStatus();\n}\n\n"
     "void D_DoomMain (void)\n{"),
    # The saved games are saves/doomsav<N>.dsg in the cart's own namespace,
    # which the manifest declares writable (default.cfg is _init's -config).
    ("d_main.c",
     "        savegamedir = M_GetSaveGameDir(D_SaveGameIWADName(gamemission));\n",
     "        savegamedir = \"saves/\";\n"),
    # The settings are kept in default.cfg (_init's -config), which
    # doomgeneric compiles out: its reading and writing come back, less the
    # key bindings, which are the cart's (dg_cart.c presses Doom's own key
    # codes, which a config file's scan codes cannot carry), and less the
    # extra file, whose settings have no menu.
    ("m_config.c",
     "static void SaveDefaultCollection(default_collection_t *collection)\n{\n#if ORIGCODE\n",
     "static void SaveDefaultCollection(default_collection_t *collection)\n{\n"),
    ("m_config.c",
     "    fclose (f);\n#endif\n}\n\n// Parses integer values",
     "    fclose (f);\n}\n\n// Parses integer values"),
    ("m_config.c",
     "static void LoadDefaultCollection(default_collection_t *collection)\n{\n#if ORIGCODE\n",
     "static void LoadDefaultCollection(default_collection_t *collection)\n{\n"),
    ("m_config.c",
     "    fclose (f);\n#endif\n}\n\n// Set the default filenames",
     "    fclose (f);\n}\n\n// Set the default filenames"),
    ("m_config.c",
     "        if (!defaults[i].bound)\n        {\n            continue;\n        }\n",
     "        if (!defaults[i].bound || defaults[i].type == DEFAULT_KEY)\n        {\n"
     "            continue;\n        }\n"),
    ("m_config.c",
     "        if (def == NULL || !def->bound)\n",
     "        if (def == NULL || !def->bound || def->type == DEFAULT_KEY)\n"),
    ("m_config.c",
     "    SaveDefaultCollection(&doom_defaults);\n    SaveDefaultCollection(&extra_defaults);\n",
     "    SaveDefaultCollection(&doom_defaults);\n"),
    # A save goes to its slot's file at once: the file's close is one atomic
    # `write` (dg_cart.c), so the temporary file and the rename it takes are
    # gone, and a save that could not be kept, or that outgrew vanilla's
    # limit, says so in a message box and the game plays on.
    ("g_game.c",
     "void G_DoSaveGame (void) \n{ \n",
     "void DG_SaveFailed(void);\n\nvoid G_DoSaveGame (void) \n{ \n"),
    ("g_game.c",
     "    save_stream = fopen(temp_savegame_file, \"wb\");\n",
     "    save_stream = fopen(savegame_file, \"wb\");\n"),
    ("g_game.c",
     "        I_Error (\"Savegame buffer overrun\");\n",
     "        savegame_error = true;\n"),
    ("g_game.c",
     "    fclose(save_stream);\n\n    if (recovery_savegame_file != NULL)",
     "    if (fclose(save_stream) != 0)\n    {\n        savegame_error = true;\n    }\n\n"
     "    if (recovery_savegame_file != NULL)"),
    ("g_game.c",
     "    // Now rename the temporary savegame file to the actual savegame\n"
     "    // file, overwriting the old savegame if there was one there.\n\n"
     "    remove(savegame_file);\n    rename(temp_savegame_file, savegame_file);\n",
     ""),
    ("g_game.c",
     "    players[consoleplayer].message = DEH_String(GGSAVED);\n",
     "    if (savegame_error)\n    {\n        DG_SaveFailed();\n    }\n    else\n    {\n"
     "        players[consoleplayer].message = DEH_String(GGSAVED);\n    }\n"),
    # A board may have no keyboard to name a save with, and Doom saves no
    # slot without a name: an empty slot, or one named after a map, takes the
    # current map's name, which A (Enter) confirms.
    ("m_menu.c",
     "    if (!strcmp(savegamestrings[choice], EMPTYSTRING))\n"
     "\tsavegamestrings[choice][0] = 0;\n",
     "    if (!strcmp(savegamestrings[choice], EMPTYSTRING)\n"
     "     || M_IsMapName(savegamestrings[choice]))\n    {\n"
     "        if (gamemode == commercial)\n"
     "            M_snprintf(savegamestrings[choice], SAVESTRINGSIZE, \"MAP%02d\", gamemap);\n"
     "        else\n"
     "            M_snprintf(savegamestrings[choice], SAVESTRINGSIZE, \"E%dM%d\",\n"
     "                       gameepisode, gamemap);\n    }\n"),
    ("m_menu.c",
     "void M_SaveSelect(int choice)\n{",
     "static boolean M_IsMapName(char *s)\n{\n"
     "    return (s[0] == 'E' && isdigit(s[1]) && s[2] == 'M' && isdigit(s[3]) && !s[4])\n"
     "        || (!strncmp(s, \"MAP\", 3) && isdigit(s[3]) && isdigit(s[4]) && !s[5]);\n}\n\n"
     "void M_SaveSelect(int choice)\n{"),
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
    # Doom draws straight into the cart's frame: I_VideoBuffer IS
    # DG_ScreenBuffer, so I_FinishUpdate has nothing to copy.
    ("i_video.c",
     "\tI_VideoBuffer = (byte*)Z_Malloc (SCREENWIDTH * SCREENHEIGHT, PU_STATIC, NULL);"
     "  // For DOOM to draw on\n",
     "\tI_VideoBuffer = (byte*)DG_ScreenBuffer;\n"),
    ("i_video.c", "\tZ_Free (I_VideoBuffer);\n", ""),
    ("i_video.c",
     "    y = SCREENHEIGHT;\n",
     "    y = I_VideoBuffer == (byte*)DG_ScreenBuffer ? 0 : SCREENHEIGHT;\n"),
    # The column and span drawers take their texture and colormap into locals
    # once a call: a store through the frame may alias any global, so read
    # through the globals they were loaded again for every pixel.
    ("r_draw.c",
     "    // Inner loop that does the actual texture mapping,\n    //  e.g. a DDA-lile scaling.\n"
     "    // This is as fast as it gets.\n    do \n    {\n"
     "\t// Re-map color indices from wall texture column\n"
     "\t//  using a lighting/special effects LUT.\n"
     "\t*dest = dc_colormap[dc_source[(frac>>FRACBITS)&127]];\n",
     "    const byte *source = dc_source, *colormap = dc_colormap;\n"
     "    do \n    {\n"
     "\t*dest = colormap[source[(frac>>FRACBITS)&127]];\n"),
    ("r_draw.c",
     "    dest = ylookup[ds_y] + columnofs[ds_x1];\n\n"
     "    // We do not check for zero spans here?\n    count = ds_x2 - ds_x1;\n\n"
     "    do\n    {\n\t// Calculate current texture index in u,v.\n"
     "        ytemp = (position >> 4) & 0x0fc0;\n        xtemp = (position >> 26);\n"
     "        spot = xtemp | ytemp;\n\n\t// Lookup pixel from flat texture tile,\n"
     "\t//  re-index using light/colormap.\n\t*dest++ = ds_colormap[ds_source[spot]];\n",
     "    dest = ylookup[ds_y] + columnofs[ds_x1];\n"
     "    const byte *source = ds_source, *colormap = ds_colormap;\n\n"
     "    // We do not check for zero spans here?\n    count = ds_x2 - ds_x1;\n\n"
     "    do\n    {\n\t// Calculate current texture index in u,v.\n"
     "        ytemp = (position >> 4) & 0x0fc0;\n        xtemp = (position >> 26);\n"
     "        spot = xtemp | ytemp;\n\n\t// Lookup pixel from flat texture tile,\n"
     "\t//  re-index using light/colormap.\n\t*dest++ = colormap[source[spot]];\n"),
)

# What each patched file says about its change (GPL-2.0 section 2a): the
# date of the change, and what it is. The notice goes at the end of the
# file, so no line number moves.
CHANGES = {
    "m_misc.c": ("2026-09-26", "M_FileExists answers true for doom1.wad, the cart's own file"),
    "i_sound.c": ("2026-09-29", "the sound module is dg_sound.c's DG_sound_module, compiled "
                  "in without FEATURE_SOUND"),
    "doomgeneric.c": ("2026-09-26", "DG_ScreenBuffer is the cart's frame (DG_ScreenMemory)"),
    "doomgeneric.h": ("2026-09-26", "declares DG_ScreenMemory"),
    "d_loop.c": ("2026-09-26", "TryRunTics returns when no tic is due instead of sleeping, "
                 "and calls DG_AfterTic after every game tic"),
    "d_main.c": ("2026-09-26", "D_Display begins the screen melt and steps it once a frame "
                 "(DG_WipeBegin, DG_WipeStep) instead of looping on the clock; 2026-09-29: "
                 "G_CheckDemoStatus runs at exit through a wrapper of the exit functions' type; "
                 "2026-10-03: the saved games' folder is saves/"),
    "g_game.c": ("2026-10-03", "G_DoSaveGame writes the slot's file directly, whose close "
                 "is the cart's atomic write, and a save that could not be kept or outgrew "
                 "vanilla's limit calls DG_SaveFailed instead of reporting \"game saved.\" "
                 "or ending the game"),
    "m_config.c": ("2026-10-03", "the default collection is read and written again, less "
                   "its key bindings, and the extra collection is not written"),
    "m_menu.c": ("2026-10-03", "M_SaveSelect names an empty slot, or one named after a map, "
                 "after the current map"),
    "z_zone.c": ("2026-09-29", "Z_Malloc searches the whole zone from its first block before "
                 "it fails"),
    "f_wipe.c": ("2026-09-29", "the melt reads its screens row-major where they lie instead "
                 "of transposing them through a third screen-sized block"),
    "i_video.c": ("2026-09-28", "I_VideoBuffer is DG_ScreenBuffer, the cart's frame, so "
                  "I_FinishUpdate copies nothing"),
    "r_draw.c": ("2026-09-28", "R_DrawColumn and R_DrawSpan read their texture and colormap "
                 "pointers once a call"),
}

CART_NOTICE = """\
Doom for Moybyte consoles.

The engine is doomgeneric -- id Software's DOOM source release by way of
Chocolate Doom, in ozkl's portable fork -- under the GNU General Public
License, version 2 or (at your option) any later version. The platform glue,
dg_cart.c and dg_sound.c, is under the same licence. The complete corresponding source, and
the recipe that builds this cart from it, is named in SOURCE.txt.

doom1.wad is not part of the download this cart comes in. The installer
fetches it, unmodified, from the carts repository's site, which gives it away
beside id's licence, or else from Debian's archive, and checks it by sha256.
It is id Software's shareware DOOM v1.9, under id's Limited Use Software
License, which is at the end of this file: it may be copied and given to
others, unmodified; nobody may charge or receive anything for it; no
modification or derivative works.
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
    """doomgeneric's engine, dg_cart.c and dg_sound.c in the stage, patched."""
    for path, data in sources.items():
        if path.startswith("doomgeneric/") and path.count("/") == 1 \
                and path.endswith((".c", ".h")):
            with open(os.path.join(ctx.stage, path.split("/")[1]), "wb") as f:
                f.write(data)
    for glue in ("dg_cart.c", "dg_sound.c"):
        shutil.copy(os.path.join(ctx.cart_dir, glue), ctx.stage)
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
    for name, (date, what) in sorted(CHANGES.items()):
        with open(os.path.join(ctx.stage, name), "a", encoding="latin-1", newline="") as f:
            f.write("\n/* Changed for Moybyte's compiled-cart tier by moybyte-org/carts'\n"
                    " * carts/doom/recipe.py, %s: %s. */\n" % (date, what))


def link_flags(memory):
    """The linker's half of the command line, for `memory` bytes of linear
    memory. The stack goes first, so an overflow leaves linear memory and traps
    rather than running into the data."""
    return ["-nostartfiles", "-Wl,--no-entry", "-Wl,--strip-all",
            "-Wl,--initial-memory=%d" % memory, "-Wl,--max-memory=%d" % memory,
            "-Wl,--stack-first", "-Wl,-z,stack-size=%d" % (STACK_KB * 1024),
            "-Wl,--export=__heap_base"]


def cflags(zone_mb):
    """The compiler's flags, with the zone config.json names as the one Doom
    falls back to where a console reads no config.json."""
    return CFLAGS + ["-DDG_ZONE_MB=%d" % zone_mb]


def link(ctx, memory, zone_mb):
    """main.wasm at `memory` bytes of linear memory; its bytes. __FILE__
    (z_zone.h's Z_ChangeTag names its caller) is the bare file name, so the
    build does not depend on where it ran."""
    out = os.path.join(ctx.stage, "main.wasm")
    cmd = ([os.path.join(ctx.wasi_sdk, "bin", "clang")] + cflags(zone_mb)
           + ["-ffile-prefix-map=%s/=%s" % (ctx.stage, ctx.file_prefix)]
           + link_flags(memory) + ["-o", out]
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
    """Write the cart folder's main.wasm, manifest.json, config.json,
    LICENSES.txt and cover.png into `folder`. Returns {"wasm": bytes, "pins": {...},
    "sources": {path: bytes}, "rebuild": {...}}: the pins SOURCE.txt names,
    the files the source bundle carries beside carts/doom -- every file the
    build staged, patched as clang saw it, and doomgeneric's LICENSE -- and
    the lines that compile them into doomgeneric/main.wasm, with $WASI_SDK
    standing for wasi-sdk's directory."""
    manifest_bytes = ctx.read("manifest.json")
    config_bytes = ctx.read("config.json")
    manifest = json.loads(manifest_bytes)
    zone_mb = int(json.loads(config_bytes)["zone"])
    sources = doomgeneric(ctx)
    licence = id_licence(ctx)

    stage(ctx, sources)
    base = heap_base(ctx, link(ctx, 16 << 20, zone_mb))
    memory = math.ceil((base + (zone_mb << 20) + HEAP_KB * 1024) / PAGE) * PAGE
    if manifest.get("memory") != memory // PAGE:
        raise ctx.error("manifest.json declares %r pages; static data and stack (%d KB), "
                        "the %d MiB zone and a %d KB heap need %d"
                        % (manifest.get("memory"), base // 1024, zone_mb, HEAP_KB,
                           memory // PAGE))
    wasm = link(ctx, memory, zone_mb)
    foreign = ["%s.%s" % mn for mn in imports(ctx, wasm) if mn[0] != "moy"]
    if foreign:
        raise ctx.error("main.wasm imports outside \"moy\": %s" % ", ".join(foreign))

    with open(os.path.join(folder, manifest.get("main", "main.wasm")), "wb") as f:
        f.write(wasm)
    with open(os.path.join(folder, "manifest.json"), "wb") as f:
        f.write(manifest_bytes)
    with open(os.path.join(folder, "config.json"), "wb") as f:
        f.write(config_bytes)
    with open(os.path.join(folder, "cover.png"), "wb") as f:
        f.write(ctx.read("cover.png"))
    with open(os.path.join(folder, "LICENSES.txt"), "w", encoding="utf-8", newline="\n") as f:
        f.write(CART_NOTICE + "\n" + GPL_NOTICE + "\n" + sources["LICENSE"].decode("latin-1")
                + "\n" + licence)
    print("doom: memory %d pages (static data and stack %d KB, zone %d MiB, heap %d KB)"
          % (memory // PAGE, base // 1024, zone_mb, HEAP_KB))
    staged = {}
    for fn in sorted(os.listdir(ctx.stage)):
        if fn.endswith((".c", ".h")):
            with open(os.path.join(ctx.stage, fn), "rb") as f:
                staged["doomgeneric/" + fn] = f.read()
    staged["doomgeneric/LICENSE"] = sources["LICENSE"]
    return {
        "wasm": wasm,
        "sources": staged,
        "rebuild": {
            "lines": ["cd doomgeneric",
                      ['"$WASI_SDK/bin/clang"'] + cflags(zone_mb) + link_flags(memory)
                      + ["-o", "main.wasm"] + [o + ".c" for o in OBJS],
                      "cd .."],
            "wasm": "doomgeneric/main.wasm",
        },
        "pins": {
            "doomgeneric": {
                "url": "https://github.com/ozkl/doomgeneric/tree/" + DOOMGENERIC_COMMIT,
                "commit": DOOMGENERIC_COMMIT,
                "tree_sha256": DOOMGENERIC_TREE_SHA256,
            },
        },
    }
