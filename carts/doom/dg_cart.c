/*
 * SPDX-License-Identifier: GPL-2.0-or-later
 * Copyright (C) 2026 Nikola Jovicic
 *
 * doomgeneric's platform half for Moybyte's compiled-cart tier: Doom as a
 * "runtime": "wasm" cart whose imports are the console's own, module "moy"
 * (moy-spec proposals/wasm-runtime.md). recipe.py stages it with doomgeneric
 * and builds the cart.
 *
 * The console keeps the time (time()), the input (btn, touch, key), the WAD
 * (the cart's own read), the saved games and settings (Doom's stdio, through
 * read and write: saves/doomsav<N>.dsg and default.cfg), the screen (blit,
 * with Doom's 256-entry palette) and the sound (snd, which dg_sound.c mixes
 * for). The pacing is Doom's own clock:
 * the manifest declares "fps": "free", so every loop frame runs _update, which
 * runs the game tics time() says are due, moves the sounds and tops up the
 * console's sample queue, and _draw, which renders and blits. Nothing here
 * sleeps or waits.
 *
 * pmem carries what a test reads back (the slots are laid out below): the
 * frame CRC at every CRC_EVERY-th gametic, the zone's low-water mark, the
 * heap's headroom, the sound's account (frames the console took, lumps read
 * for it, the queue depth aimed at, and where the queue ran dry), the last
 * saved game's size and its write's answer, and the text of a fatal I_Error.
 */
#include <errno.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <wasi/api.h>

#include "d_loop.h"
#include "d_main.h"
#include "doomgeneric.h"
#include "doomkeys.h"
#include "doomstat.h"
#include "f_wipe.h"
#include "i_timer.h"
#include "i_video.h"
#include "m_config.h"
#include "m_menu.h"
#include "s_sound.h"
#include "w_file.h"
#include "z_zone.h"

#define MOY(name) __attribute__((import_module("moy"), import_name(name)))
#define EXPORT(name) __attribute__((export_name(name)))

MOY("blit") void moy_blit(const uint8_t *frame, const uint8_t *pal);
MOY("read") int32_t moy_read(const char *name, int32_t name_len, int32_t offset,
                             void *dst, int32_t len);
MOY("write") int32_t moy_write(const char *path, int32_t path_len, const void *data,
                              int32_t len);
MOY("time") int32_t moy_time(void);
MOY("btn") int32_t moy_btn(int32_t b, int32_t player);
MOY("key") int32_t moy_key(int32_t code);
MOY("touch") int32_t moy_touch(int32_t *out);
MOY("cfg") int32_t moy_cfg(const char *key, int32_t key_len, char *dst, int32_t dst_len);
MOY("pmem") int32_t moy_pmem(int32_t slot, int32_t v, int32_t write);
MOY("quit") void moy_quit(void);

/* The canvas the manifest declares, and Doom's 320 x 200 letterboxed in it. */
#define W 320
#define H 240
#define TOP ((H - DOOMGENERIC_RESY) / 2)

/* pmem slots */
#define PM_CRC_LAST 0      /* the last CRC index recorded */
#define PM_CRC 1           /* 1..CRC_SLOTS: the frame CRC at gametic k * CRC_EVERY */
#define CRC_EVERY 500
#define CRC_SLOTS 199
#define PM_ERROR 200       /* 200..231: a fatal I_Error's text, 4 bytes a slot */
#define PM_ERROR_SLOTS 32
#define PM_ZONE_MB 240     /* the zone this run asked for */
#define PM_ZONE_LOW 241    /* the zone's lowest free bytes seen, in KB */
#define PM_HEAP_FREE 242   /* linear memory the heap could still hand out after _init, in KB */
#define PM_GAMETIC 243
#define PM_MAP 244         /* episode * 10 + map */
#define PM_SND_FRAMES 245  /* sample frames the console has taken from the pump */
#define PM_SND_READS 246   /* lumps read from the card for sound */
#define PM_SND_TARGET 247  /* the depth the pump keeps the console's queue at, in frames */
#define PM_SND_DRY 248     /* 248..251: frames the console's queue ran dry for, in play,
                              in the melt and the frame before it, on other screens, at
                              a level load (DG_Phase) */
#define PM_SAVE_SIZE 252   /* the last saved game's bytes */
#define PM_SAVE_ANSWER 253 /* its close: 0 kept, else write's answer (-2 also: no wbuf) */
#define PM_WRITES 254      /* `write` calls this run */

extern boolean menuactive;
extern int messageToPrint;
extern boolean messageNeedsInput;
extern boolean screenvisible;
extern int fuzzpos;
extern struct color colors[256];

void D_Display(void);
void M_StartMessage(char *string, void *routine, boolean input);
void DG_SoundPump(void);
void DG_SoundLoad(void);
void __wasm_call_ctors(void);
extern uint32_t dg_sound_frames, dg_sound_reads, dg_sound_target, dg_sound_dry[4];

static uint8_t frame[W * H];
static uint8_t pal[768];

pixel_t *DG_ScreenMemory(void)
{
    return frame + TOP * W;
}

/* ---- the host's side of doomgeneric -------------------------------------- */

void DG_Init(void) {}
void DG_DrawFrame(void) {}          /* the frame is already in DG_ScreenBuffer */
void DG_SleepMs(uint32_t ms) { (void)ms; }
void DG_SetWindowTitle(const char *title) { (void)title; }

uint32_t DG_GetTicksMs(void)
{
    return (uint32_t)moy_time();
}

/* Key events for doomgeneric's I_GetEvent, built by _update from the
 * console's input. */
static unsigned short events[64];
static unsigned ev_head, ev_tail;

static void post(int pressed, unsigned char key)
{
    if (ev_tail - ev_head < sizeof(events) / sizeof(events[0]))
        events[ev_tail++ % 64] = (unsigned short)((pressed << 8) | key);
}

int DG_GetKey(int *pressed, unsigned char *key)
{
    if (ev_head == ev_tail)
        return 0;
    unsigned short e = events[ev_head++ % 64];
    *pressed = e >> 8;
    *key = e & 0xff;
    return 1;
}

/* ---- the WAD: the cart's own file, through `read` ------------------------ */

static const char WAD[] = "doom1.wad";

static wad_file_t *W_Cart_OpenFile(char *path);
static void W_Cart_CloseFile(wad_file_t *wad);
static size_t W_Cart_Read(wad_file_t *wad, unsigned int offset, void *buffer, size_t len);

static wad_file_class_t cart_wad = { W_Cart_OpenFile, W_Cart_CloseFile, W_Cart_Read };

static wad_file_t *W_Cart_OpenFile(char *path)
{
    if (strcmp(path, WAD) != 0)
        return NULL;
    int32_t size = moy_read(WAD, sizeof(WAD) - 1, 0, 0, 0);
    if (size <= 0)
        return NULL;
    wad_file_t *w = Z_Malloc(sizeof(*w), PU_STATIC, 0);
    w->file_class = &cart_wad;
    w->mapped = NULL;
    w->length = (unsigned int)size;
    return w;
}

static void W_Cart_CloseFile(wad_file_t *wad)
{
    Z_Free(wad);
}

/* The sound is pumped before every read, and a long read comes in pieces
 * with the sound pumped between them, so a level load or a burst of new
 * graphics does not leave the console's sample queue to run dry. */
#define READ_PIECE (32 * 1024)

static size_t W_Cart_Read(wad_file_t *wad, unsigned int offset, void *buffer, size_t len)
{
    size_t done = 0;
    (void)wad;
    DG_SoundPump();
    while (done < len) {
        size_t n = len - done > READ_PIECE ? READ_PIECE : len - done;
        int32_t got = moy_read(WAD, sizeof(WAD) - 1, (int32_t)(offset + done),
                               (uint8_t *)buffer + done, (int32_t)n);
        if (got <= 0)
            break;
        done += (size_t)got;
        if ((size_t)got < n)
            break;
        if (done < len)
            DG_SoundPump();
    }
    return done;
}

wad_file_t *W_OpenFile(char *path)
{
    return cart_wad.OpenFile(path);
}

void W_CloseFile(wad_file_t *wad)
{
    wad->file_class->CloseFile(wad);
}

size_t W_Read(wad_file_t *wad, unsigned int offset, void *buffer, size_t len)
{
    return wad->file_class->Read(wad, offset, buffer, len);
}

/* ---- the wipe, one step a frame ------------------------------------------ */

/* The screen melt. Doom runs it as a loop inside D_Display that waits on the
 * clock while no game tic runs; here D_Display begins it, every later
 * D_Display takes one step for the tics of time() that have passed since the
 * last, and _update runs no game tic until it is done -- the game then catches
 * up, as Doom's own loop does after a wipe. */
static int wiping, wipe_time;

/* What the game is doing, for dg_sound.c's account of the sound queue
 * running dry: 3 a level load, 1 the screen melt and the frame drawn before
 * it, 2 another screen than a level, 0 play. */
int DG_Phase(void)
{
    if (gameaction != ga_nothing || (gamestate == GS_LEVEL && leveltime == 0))
        return 3;
    if (wiping || gamestate != wipegamestate)
        return 1;
    return gamestate == GS_LEVEL ? 0 : 2;
}

static void wipe_step(int tics)
{
    if (wipe_ScreenWipe(wipe_Melt, 0, 0, SCREENWIDTH, SCREENHEIGHT, tics))
        wiping = 0;
    I_UpdateNoBlit();
    M_Drawer();
    I_FinishUpdate();
}

void DG_WipeBegin(void)
{
    wiping = 1;
    wipe_time = I_GetTime();
    wipe_step(1);
}

int DG_WipeStep(void)
{
    if (!wiping)
        return 0;
    int now = I_GetTime();
    if (now > wipe_time) {
        int tics = now - wipe_time;
        wipe_time = now;
        wipe_step(tics);
    }
    return 1;
}

/* ---- the palette and the frame CRC --------------------------------------- */

static void palette(void)
{
    for (int i = 0; i < 256; i++) {
        pal[3 * i] = colors[i].r;
        pal[3 * i + 1] = colors[i].g;
        pal[3 * i + 2] = colors[i].b;
    }
}

static uint32_t crc32(const uint8_t *p, size_t n, uint32_t crc)
{
    crc = ~crc;
    while (n--) {
        crc ^= *p++;
        for (int k = 0; k < 8; k++)
            crc = (crc >> 1) ^ (0xEDB88320u & (0u - (crc & 1u)));
    }
    return ~crc;
}

static int zone_low_kb = -1;

/* After every game tic (d_loop.c calls it): at each CRC_EVERY-th gametic,
 * render the frame of that tic and record its CRC -- Doom's picture at a
 * gametic is the same on every host, whenever the host happens to draw. */
void DG_AfterTic(void)
{
    if (gametic % 35 == 0) {
        int kb = Z_FreeMemory() / 1024;
        if (zone_low_kb < 0 || kb < zone_low_kb) {
            zone_low_kb = kb;
            moy_pmem(PM_ZONE_LOW, kb, 1);
        }
        moy_pmem(PM_GAMETIC, gametic, 1);
        moy_pmem(PM_MAP, gameepisode * 10 + gamemap, 1);
        moy_pmem(PM_SND_FRAMES, (int32_t)dg_sound_frames, 1);
        moy_pmem(PM_SND_READS, (int32_t)dg_sound_reads, 1);
        moy_pmem(PM_SND_TARGET, (int32_t)dg_sound_target, 1);
        for (int k = 0; k < 4; k++)
            moy_pmem(PM_SND_DRY + k, (int32_t)dg_sound_dry[k], 1);
    }
    if (gametic % CRC_EVERY || gametic / CRC_EVERY > CRC_SLOTS || !screenvisible)
        return;
    /* A whole frame drawn inside a tic, so the sound is pumped either side
     * of it.
     * The spectre's fuzz walks a table one drawn pixel at a time across
     * frames, so its phase is the count of frames drawn before this one --
     * which is the host's cadence, not the game's. This frame starts it at 0. */
    int fuzz = fuzzpos;
    fuzzpos = 0;
    DG_SoundPump();
    D_Display();
    fuzzpos = fuzz;
    palette();
    uint32_t crc = crc32(I_VideoBuffer, SCREENWIDTH * SCREENHEIGHT, 0);
    crc = crc32(pal, sizeof(pal), crc);
    moy_pmem(PM_CRC + gametic / CRC_EVERY - 1, (int32_t)crc, 1);
    moy_pmem(PM_CRC_LAST, gametic / CRC_EVERY, 1);
    DG_SoundPump();
}

/* ---- input ---------------------------------------------------------------- */

/* SPEC.md 7.3's buttons, in the import's order. */
enum { B_LEFT, B_RIGHT, B_UP, B_DOWN, B_A, B_B, B_RUN, N_BUTTONS };

/* The key a button presses in play, and in a menu. */
static unsigned char key_for(int b)
{
    switch (b) {
    case B_LEFT: return KEY_LEFTARROW;
    case B_RIGHT: return KEY_RIGHTARROW;
    case B_UP: return KEY_UPARROW;
    case B_DOWN: return KEY_DOWNARROW;
    case B_A:
        if (messageToPrint && messageNeedsInput) return 'y';
        return menuactive ? KEY_ENTER : KEY_FIRE;
    case B_B:
        if (messageToPrint && messageNeedsInput) return 'n';
        return menuactive ? KEY_BACKSPACE : KEY_USE;
    default: return KEY_ESCAPE;
    }
}

/* The touch screen as a 3 x 3 pad over the whole canvas, for boards with
 * no buttons: the top row walks forward (turning at the corners), the middle
 * row turns and fires, the bottom row uses, walks back and opens the menu. */
static unsigned touch_buttons(void)
{
    int32_t t[4];
    if (!moy_touch(t) || !t[3])
        return 0;
    int col = t[0] < W / 4 ? 0 : t[0] >= W - W / 4 ? 2 : 1;
    int row = t[1] < H / 3 ? 0 : t[1] >= H - H / 3 ? 2 : 1;
    static const unsigned PAD[3][3] = {
        { 1u << B_UP | 1u << B_LEFT, 1u << B_UP, 1u << B_UP | 1u << B_RIGHT },
        { 1u << B_LEFT, 1u << B_A, 1u << B_RIGHT },
        { 1u << B_B, 1u << B_DOWN, 1u << B_RUN },
    };
    return PAD[row][col];
}

/* Keyboard extras, where there is a keyboard: strafing on Doom's own keys. */
static const struct { int32_t ascii; unsigned char key; } EXTRA[] = {
    { ',', KEY_STRAFE_L }, { '.', KEY_STRAFE_R },
};

static unsigned char held_key[N_BUTTONS];
static unsigned char held_extra[sizeof(EXTRA) / sizeof(EXTRA[0])];

static void input(void)
{
    unsigned down = touch_buttons();
    for (int b = 0; b < N_BUTTONS; b++)
        if (moy_btn(b, 0))
            down |= 1u << b;
    for (int b = 0; b < N_BUTTONS; b++) {
        int on = (down >> b) & 1;
        if (on && !held_key[b]) {
            held_key[b] = key_for(b);
            post(1, held_key[b]);
        } else if (!on && held_key[b]) {
            post(0, held_key[b]);
            held_key[b] = 0;
        }
    }
    for (unsigned i = 0; i < sizeof(EXTRA) / sizeof(EXTRA[0]); i++) {
        int on = moy_key(EXTRA[i].ascii) != 0;
        if (on != held_extra[i]) {
            post(on, EXTRA[i].key);
            held_extra[i] = (unsigned char)on;
        }
    }
}

/* ---- stdout, stderr and exit, with no WASI ------------------------------- */

/* wasi-libc's stdio and exit reach the host through these, and a compiled
 * cart imports nothing but "moy": stdout and stderr keep their last line for
 * a fatal I_Error, and exit ends the cart -- quit() for a clean exit, a trap
 * for an error, with the error's text in pmem. */
static char err_line[PM_ERROR_SLOTS * 4], err_last[PM_ERROR_SLOTS * 4];
static unsigned err_n, err_last_n;

typedef struct { uint32_t buf, len; } iovec_t;

static int32_t file_write(int32_t fd, int32_t iovs, int32_t n, int32_t nwritten);

int32_t __imported_wasi_snapshot_preview1_fd_write(int32_t fd, int32_t iovs, int32_t n,
                                                    int32_t nwritten)
{
    if (fd > 2)
        return file_write(fd, iovs, n, nwritten);
    const iovec_t *v = (const iovec_t *)(uintptr_t)iovs;
    uint32_t total = 0;
    for (int32_t i = 0; i < n; i++) {
        const char *p = (const char *)(uintptr_t)v[i].buf;
        for (uint32_t j = 0; fd == 2 && j < v[i].len; j++) {
            if (p[j] != '\n') {
                if (err_n < sizeof(err_line))
                    err_line[err_n++] = p[j];
            } else if (err_n) {
                memcpy(err_last, err_line, err_n);
                err_last_n = err_n;
                err_n = 0;
            }
        }
        total += v[i].len;
    }
    *(uint32_t *)(uintptr_t)nwritten = total;
    return 0;
}

/* The last line written to stderr -- I_Error's message -- into pmem. */
static void report_error(void)
{
    const char *t = err_n ? err_line : err_last;
    unsigned n = err_n ? err_n : err_last_n;
    for (int s = 0; s < PM_ERROR_SLOTS; s++) {
        int32_t word = 0;
        for (int b = 0; b < 4; b++) {
            unsigned at = (unsigned)s * 4 + (unsigned)b;
            word |= (int32_t)(at < n ? (uint8_t)t[at] : 0) << (8 * b);
        }
        moy_pmem(PM_ERROR + s, word, 1);
    }
}

_Noreturn void __imported_wasi_snapshot_preview1_proc_exit(int32_t code)
{
    if (code == 0)
        moy_quit();
    report_error();
    __builtin_trap();
}

/* i_system.c shells out to show a crash on a desktop; here there is no shell. */
int system(const char *command)
{
    (void)command;
    return -1;
}

/* The heap ends where the linear memory ends: the manifest's memory is both
 * its initial and its maximum size, so it never grows, and malloc's only
 * other call, sbrk, answers from the linker's __heap_end rather than with
 * memory.size and memory.grow. A module with neither lets the compiler load
 * the memory's base and bound once per function instead of after every store
 * and call, and read Doom's globals at their constant addresses unchecked. */
extern unsigned char __heap_end;

void *sbrk(intptr_t increment)
{
    if (increment == 0)
        return &__heap_end;
    if (increment < 0 || increment % 65536)
        abort();
    errno = ENOMEM;
    return (void *)-1;
}

/* ---- the cart's files: Doom's stdio, through read and write -------------- */

/* Doom's stdio reaches its files through wasi-libc, and wasi-libc reaches them
 * through these WASI calls. fd 3 is the cart's own namespace, preopened as
 * ".", so a relative path arrives at path_open as the name `read` and `write`
 * take (SPEC.md 16.6, 16.12).
 *
 * A file opened for reading reads through `read` at its offset, so a written
 * copy answers before the cart's own file, and a name `read` answers 0 bytes
 * for is absent. A file opened for writing starts empty, is built in wbuf, and
 * its close writes it whole with one `write`: a save is atomic and kept when
 * fclose returns, and fclose fails when the write did. A close that would
 * write what the file already holds writes nothing. Which paths take a write
 * is manifest.json's "writable"; the console answers -1 for any other.
 *
 * wbuf holds one file at a time and takes no memory of its own. The settings
 * fit in WBUF_HEAP bytes of the heap; a longer file -- a saved game -- moves
 * into dg_sound.c's lump cache, lent whole (DG_SoundLend) until the close,
 * as vanilla Doom built its saves in a spare screen: neither the heap nor the
 * zone, which a level fills, has a block that size to spare on every map.
 * A file is at most vanilla's ceiling on a save, g_game.c's SAVEGAMESIZE,
 * which G_DoSaveGame holds a save to as well: a longer one fails at its
 * close as one there is no room for, and is not written. */
#define PREOPEN 3
#define FD0 4              /* the first open file's fd */
#define NFILES 4
#define NAME_MAX_ 64       /* SPEC.md 16.12's longest writable path */
#define WBUF_HEAP (16 * 1024)
#define WBUF_MAX 0x2c000   /* g_game.c's SAVEGAMESIZE */
#define SAVES "saves/"

static struct {
    char name[NAME_MAX_ + 1];  /* "" while the slot is free */
    int32_t len;
    uint8_t writing;
    int32_t failed;            /* the answer a close gives without writing */
    uint32_t pos, size;
} cf[NFILES];
uint8_t *DG_SoundLend(uint32_t *size);
void DG_SoundReturn(void);

static uint8_t *wbuf;
static uint32_t wbuf_cap;
static int wbuf_lent;              /* wbuf is dg_sound.c's cache */
static int wbuf_busy;
static int32_t save_answer;
static int32_t writes;

static int file_of(int32_t fd)
{
    int i = fd - FD0;
    return i >= 0 && i < NFILES && cf[i].name[0] ? i : -1;
}

static __wasi_errno_t errno_of(int32_t answer)
{
    return answer == 0 ? 0 : answer == -1 ? __WASI_ERRNO_ACCES
         : answer == -2 ? __WASI_ERRNO_NOSPC : __WASI_ERRNO_IO;
}

/* wbuf, holding `need` bytes and the `have` it holds now; 0 when there is
 * no room for them. */
static int wbuf_grow(uint32_t need, uint32_t have)
{
    if (need <= wbuf_cap)
        return 1;
    if (need > WBUF_MAX || wbuf_lent)
        return 0;
    if (need <= WBUF_HEAP) {
        uint8_t *p = realloc(wbuf, WBUF_HEAP);
        if (p) {
            wbuf = p;
            wbuf_cap = WBUF_HEAP;
            return 1;
        }
    }
    uint32_t size = 0;
    uint8_t *p = DG_SoundLend(&size);
    if (!p)
        return 0;
    memcpy(p, wbuf, have);
    free(wbuf);
    wbuf = p;
    wbuf_cap = size < WBUF_MAX ? size : WBUF_MAX;
    wbuf_lent = 1;
    return need <= wbuf_cap;
}

static void wbuf_release(void)
{
    if (wbuf_lent)
        DG_SoundReturn();
    else
        free(wbuf);
    wbuf = NULL;
    wbuf_cap = 0;
    wbuf_lent = 0;
    wbuf_busy = 0;
}

/* `write`, unless the file already holds exactly these bytes. */
static int32_t keep(const char *name, int32_t len, const uint8_t *data, uint32_t n)
{
    if (n && moy_read(name, len, 0, 0, 0) == (int32_t)n) {
        uint8_t chunk[512];
        uint32_t at = 0;
        while (at < n) {
            int32_t take = n - at < sizeof(chunk) ? (int32_t)(n - at) : (int32_t)sizeof(chunk);
            if (moy_read(name, len, (int32_t)at, chunk, take) != take
                    || memcmp(chunk, data + at, (size_t)take))
                break;
            at += (uint32_t)take;
        }
        if (at == n)
            return 0;
    }
    writes++;
    moy_pmem(PM_WRITES, writes, 1);
    return moy_write(name, len, data, (int32_t)n);
}

int32_t __imported_wasi_snapshot_preview1_fd_prestat_get(int32_t fd, int32_t out)
{
    if (fd != PREOPEN)
        return __WASI_ERRNO_BADF;
    __wasi_prestat_t *p = (__wasi_prestat_t *)(uintptr_t)out;
    p->tag = __WASI_PREOPENTYPE_DIR;
    p->u.dir.pr_name_len = 1;
    return 0;
}

int32_t __imported_wasi_snapshot_preview1_fd_prestat_dir_name(int32_t fd, int32_t p, int32_t n)
{
    if (fd != PREOPEN)
        return __WASI_ERRNO_BADF;
    if (n < 1)
        return __WASI_ERRNO_NAMETOOLONG;
    *(char *)(uintptr_t)p = '.';
    return 0;
}

int32_t __imported_wasi_snapshot_preview1_fd_fdstat_get(int32_t fd, int32_t out)
{
    if (fd != PREOPEN && file_of(fd) < 0)
        return __WASI_ERRNO_BADF;
    __wasi_fdstat_t *st = (__wasi_fdstat_t *)(uintptr_t)out;
    memset(st, 0, sizeof(*st));
    st->fs_filetype = fd == PREOPEN ? __WASI_FILETYPE_DIRECTORY : __WASI_FILETYPE_REGULAR_FILE;
    st->fs_rights_base = st->fs_rights_inheriting = ~(__wasi_rights_t)0;
    return 0;
}

int32_t __imported_wasi_snapshot_preview1_path_open(int32_t fd, int32_t df, int32_t path,
                                                     int32_t plen, int32_t of, int64_t rb,
                                                     int64_t ri, int32_t ff, int32_t out)
{
    (void)df; (void)ri; (void)ff;
    const char *name = (const char *)(uintptr_t)path;
    int writing = (rb & __WASI_RIGHTS_FD_WRITE) != 0;
    int i = 0;
    if (fd != PREOPEN)
        return __WASI_ERRNO_BADF;
    if (plen <= 0 || memchr(name, 0, (size_t)plen))
        return __WASI_ERRNO_NOENT;
    if (plen > NAME_MAX_)
        return __WASI_ERRNO_NAMETOOLONG;
    if (of & __WASI_OFLAGS_DIRECTORY)
        return __WASI_ERRNO_NOTDIR;
    while (i < NFILES && cf[i].name[0])
        i++;
    if (i == NFILES)
        return __WASI_ERRNO_NFILE;
    uint32_t size = 0;
    if (writing) {
        /* A file is written whole: opened for writing, it starts empty. */
        if (!(of & __WASI_OFLAGS_TRUNC))
            return __WASI_ERRNO_NOTSUP;
        if (wbuf_busy)
            return __WASI_ERRNO_NFILE;
        wbuf_busy = 1;
    } else {
        int32_t n = moy_read(name, plen, 0, 0, 0);
        if (n <= 0)
            return __WASI_ERRNO_NOENT;
        size = (uint32_t)n;
    }
    memcpy(cf[i].name, name, (size_t)plen);
    cf[i].name[plen] = 0;
    cf[i].len = plen;
    cf[i].writing = (uint8_t)writing;
    cf[i].failed = 0;
    cf[i].pos = 0;
    cf[i].size = size;
    *(uint32_t *)(uintptr_t)out = (uint32_t)(FD0 + i);
    return 0;
}

int32_t __imported_wasi_snapshot_preview1_fd_read(int32_t fd, int32_t iovs, int32_t n,
                                                   int32_t nread)
{
    const iovec_t *v = (const iovec_t *)(uintptr_t)iovs;
    int i = file_of(fd);
    uint32_t total = 0;
    *(uint32_t *)(uintptr_t)nread = 0;
    if (i < 0 || cf[i].writing)
        return __WASI_ERRNO_BADF;
    for (int32_t k = 0; k < n; k++) {
        if (!v[k].len)
            continue;
        int32_t got = moy_read(cf[i].name, cf[i].len, (int32_t)cf[i].pos,
                               (void *)(uintptr_t)v[k].buf, (int32_t)v[k].len);
        if (got <= 0)
            break;
        cf[i].pos += (uint32_t)got;
        total += (uint32_t)got;
        if ((uint32_t)got < v[k].len)
            break;
    }
    *(uint32_t *)(uintptr_t)nread = total;
    return 0;
}

static int32_t file_write(int32_t fd, int32_t iovs, int32_t n, int32_t nwritten)
{
    const iovec_t *v = (const iovec_t *)(uintptr_t)iovs;
    int i = file_of(fd);
    uint32_t total = 0;
    *(uint32_t *)(uintptr_t)nwritten = 0;
    if (i < 0 || !cf[i].writing)
        return __WASI_ERRNO_BADF;
    for (int32_t k = 0; k < n; k++) {
        if (cf[i].failed || !wbuf_grow(cf[i].pos + v[k].len, cf[i].size)) {
            cf[i].failed = -2;
            *(uint32_t *)(uintptr_t)nwritten = total;
            return __WASI_ERRNO_NOSPC;
        }
        memcpy(wbuf + cf[i].pos, (const void *)(uintptr_t)v[k].buf, v[k].len);
        cf[i].pos += v[k].len;
        if (cf[i].pos > cf[i].size)
            cf[i].size = cf[i].pos;
        total += v[k].len;
    }
    *(uint32_t *)(uintptr_t)nwritten = total;
    return 0;
}

int32_t __imported_wasi_snapshot_preview1_fd_seek(int32_t fd, int64_t off, int32_t whence,
                                                   int32_t out)
{
    int i = file_of(fd);
    if (i < 0)
        return __WASI_ERRNO_BADF;
    int64_t at = (whence == __WASI_WHENCE_SET ? 0 : whence == __WASI_WHENCE_CUR ? cf[i].pos
                  : cf[i].size) + off;
    if (whence > __WASI_WHENCE_END || at < 0 || at > cf[i].size)
        return __WASI_ERRNO_INVAL;
    cf[i].pos = (uint32_t)at;
    *(uint64_t *)(uintptr_t)out = (uint64_t)at;
    return 0;
}

int32_t __imported_wasi_snapshot_preview1_fd_close(int32_t fd)
{
    int i = file_of(fd);
    int32_t answer = 0;
    if (i < 0)
        return __WASI_ERRNO_BADF;
    if (cf[i].writing) {
        answer = cf[i].failed ? cf[i].failed : keep(cf[i].name, cf[i].len, wbuf, cf[i].size);
        wbuf_release();
        if (!strncmp(cf[i].name, SAVES, sizeof(SAVES) - 1)) {
            save_answer = answer;
            moy_pmem(PM_SAVE_SIZE, (int32_t)cf[i].size, 1);
            moy_pmem(PM_SAVE_ANSWER, answer, 1);
        }
    }
    cf[i].name[0] = 0;
    return errno_of(answer);
}

/* g_game.c's G_DoSaveGame, when its fclose failed: Doom's own message box,
 * which pauses the game until a key, in place of "game saved.". */
void DG_SaveFailed(void)
{
    M_StartMessage(save_answer == -2 ? "couldn't save the game:\nthere is no room for it.\n\n"
                                       "press a key."
                   : save_answer == -3 ? "couldn't save the game:\nthe console's storage "
                                         "failed.\n\npress a key."
                   : "couldn't save the game.\n\npress a key.", NULL, false);
}

/* Doom deletes no file, and makes, removes and renames no folder or file
 * here; no environment, and no clock but time(). */
int32_t __imported_wasi_snapshot_preview1_path_unlink_file(int32_t fd, int32_t p, int32_t n)
{ (void)fd; (void)p; (void)n; return __WASI_ERRNO_NOSYS; }
int32_t __imported_wasi_snapshot_preview1_fd_fdstat_set_flags(int32_t fd, int32_t flags)
{ (void)fd; (void)flags; return __WASI_ERRNO_BADF; }
int32_t __imported_wasi_snapshot_preview1_path_create_directory(int32_t fd, int32_t p, int32_t n)
{ (void)fd; (void)p; (void)n; return __WASI_ERRNO_NOSYS; }
int32_t __imported_wasi_snapshot_preview1_path_remove_directory(int32_t fd, int32_t p, int32_t n)
{ (void)fd; (void)p; (void)n; return __WASI_ERRNO_NOSYS; }
int32_t __imported_wasi_snapshot_preview1_path_rename(int32_t fd, int32_t p, int32_t n,
                                                       int32_t nfd, int32_t np, int32_t nn)
{ (void)fd; (void)p; (void)n; (void)nfd; (void)np; (void)nn; return __WASI_ERRNO_NOSYS; }
int32_t __imported_wasi_snapshot_preview1_environ_sizes_get(int32_t count, int32_t size)
{ *(uint32_t *)(uintptr_t)count = 0; *(uint32_t *)(uintptr_t)size = 0; return 0; }
int32_t __imported_wasi_snapshot_preview1_environ_get(int32_t env, int32_t buf)
{ (void)env; (void)buf; return 0; }
int32_t __imported_wasi_snapshot_preview1_clock_time_get(int32_t id, int64_t precision,
                                                          int32_t out)
{
    (void)id; (void)precision;
    *(uint64_t *)(uintptr_t)out = (uint64_t)(uint32_t)moy_time() * 1000000ull;
    return 0;
}

/* ---- the hooks ------------------------------------------------------------ */

/* The zone the recipe sized the cart's memory for, which config.json names
 * too: what a console that reads no config.json runs with. */
#define STR_(x) #x
#define STR(x) STR_(x)
static char cfg_zone[8] = STR(DG_ZONE_MB);
static char cfg_args[96];
static char *argv[24];

static int cfg(const char *key, char *dst, int cap)
{
    int32_t n = moy_cfg(key, (int32_t)strlen(key), dst, cap - 1);
    if (n < 0 || n >= cap)
        return 0;
    dst[n] = 0;
    return 1;
}

/* Called through volatile pointers: a malloc whose block is freed unused is
 * one the compiler may assume succeeded, and the probe would read nothing. */
static void *(*volatile probe_alloc)(size_t) = malloc;
static void (*volatile probe_free)(void *) = free;

static int32_t heap_headroom_kb(void)
{
    /* The largest block the heap still hands out, to 16 KB. */
    uint32_t lo = 0, hi = 64u << 20;
    while (hi - lo > 16384) {
        uint32_t mid = lo + (hi - lo) / 2;
        void *p = probe_alloc(mid);
        if (p) {
            probe_free(p);
            lo = mid;
        } else {
            hi = mid;
        }
    }
    return (int32_t)(lo / 1024);
}

EXPORT("_init") void cart_init(void)
{
    __wasm_call_ctors();
    int argc = 0;
    argv[argc++] = "doom";
    argv[argc++] = "-iwad";
    argv[argc++] = (char *)WAD;
    argv[argc++] = "-config";
    argv[argc++] = "default.cfg";
    cfg("zone", cfg_zone, sizeof(cfg_zone));
    argv[argc++] = "-mb";
    argv[argc++] = cfg_zone;
    /* "args" in config.json: more of Doom's own flags, e.g. "-warp 1 3". */
    if (cfg("args", cfg_args, sizeof(cfg_args))) {
        for (char *p = strtok(cfg_args, " "); p && argc < 23; p = strtok(NULL, " "))
            argv[argc++] = p;
    }
    argv[argc] = NULL;
    for (int s = 0; s <= PM_WRITES; s++)
        moy_pmem(s, 0, 1);
    moy_pmem(PM_ZONE_MB, atoi(cfg_zone), 1);
    doomgeneric_Create(argc, argv);
    moy_pmem(PM_HEAP_FREE, heap_headroom_kb(), 1);
}

/* The settings are kept when the menu closes, as well as when Doom quits,
 * so a cart the console ends keeps them too; a close that changed nothing
 * writes nothing. */
static boolean menu_was;

EXPORT("_update") void cart_update(float dt)
{
    (void)dt;
    input();
    if (!wiping)
        TryRunTics();
    if (menu_was && !menuactive)
        M_SaveDefaults();
    menu_was = menuactive;
    S_UpdateSounds(players[consoleplayer].mo);
    DG_SoundPump();
}

EXPORT("_draw") void cart_draw(void)
{
    if (screenvisible)
        D_Display();
    palette();
    moy_blit(frame, pal);
    DG_SoundLoad();
}
