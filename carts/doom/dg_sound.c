/*
 * SPDX-License-Identifier: GPL-2.0-or-later
 * Copyright (C) 2026 Nikola Jovicic
 *
 * Doom's sound effects for Moybyte's compiled-cart tier: doomgeneric's sound
 * module, mixing the effects that play into the console's sample stream --
 * `snd`, 22,050 Hz mono with a queue of 2,048 frames (moy-spec
 * proposals/wasm-runtime.md). dg_cart.c calls DG_SoundPump once a frame and
 * before every WAD read, and between the pieces of a long one.
 *
 * The pump keeps the console's queue at a target depth, not full: the queue
 * is the delay between a sound starting and the speaker playing it, so it
 * holds only what covers the longest recent gap between two pumps, plus a
 * block the console's mixer takes at once. A slow frame raises the target at
 * once, and it eases back over about ten seconds: the frame loop's slow
 * frames recur, and a target that forgot them ran the queue dry in play.
 *
 * An effect is its WAD lump: DMX's 8-bit unsigned samples, at 11,025 Hz for
 * all but one of the shareware's. The lumps live in a cache of their own,
 * never in Doom's zone: the zone's allocator purges every cached block it
 * walks past, so effects kept there were read from the card again and again,
 * along with the graphics they pushed out. The cache is a ring: a lump that
 * misses is read in at the head, overwriting the oldest, and a lump that hits
 * in the older half is copied to the head, so the effects in use stay. A
 * lump that misses is read at the end of _draw (DG_SoundLoad), not in the
 * game tic that started it: a read early in a frame waits for the previous
 * frame's flush to the panel, which shares the card's bus, and the console
 * waits with it; by the end of _draw that flush is done. Such a sound starts a
 * frame late. A channel whose samples are overwritten stops. Doom's stereo
 * separation folds away: the console is mono, and the two sides of a Doom
 * channel always average to its volume. There is no music.
 */
#include <stdint.h>
#include <string.h>

#include "deh_str.h"
#include "doomtype.h"
#include "i_sound.h"
#include "m_misc.h"
#include "w_wad.h"

#define MOY(name) __attribute__((import_module("moy"), import_name(name)))

MOY("snd") int32_t moy_snd(const int16_t *pcm, int32_t n);
MOY("time") int32_t moy_time(void);

#define RATE 22050
#define DEPTH 2048
#define CHANNELS 16
#define CACHE_BYTES (256 * 1024)
#define ENTRIES 64
/* The console's mixer takes this many frames at once; the queue keeps it
 * above that on top of the gap it covers. */
#define BLOCK 256
#define HALF 0x8000u                /* 0.5 in 16.16: an 11,025 Hz effect */

typedef struct {
    const uint8_t *data;    /* the samples, past the lump's header and padding; NULL: none */
    int pending;            /* a lump to read at the end of _draw, or -1 */
    uint32_t len;           /* samples */
    uint32_t pos;           /* 16.16, into data */
    uint32_t step;          /* the effect's rate over RATE, 16.16 */
    int32_t vol;            /* 0..127 */
} channel_t;

typedef struct {
    int lump;
    uint32_t off, size;     /* the whole lump, in cache */
} entry_t;

static channel_t channels[CHANNELS];
static boolean sfx_prefix;
static uint8_t cache[CACHE_BYTES];
static entry_t entries[ENTRIES];
static int n_entries;
static uint32_t head;

/* Read by dg_cart.c into the cart's pmem: frames the console has taken,
 * lumps read from the card for sound, and the queue depth now aimed at. */
uint32_t dg_sound_frames, dg_sound_reads, dg_sound_target;

/* The frames the console's queue ran dry for, by what the game was doing
 * across the gap before the pump that found it empty (dg_cart.c's
 * DG_Phase): play, the screen melt with the first frame drawn before it,
 * another screen than a level, and a level load. */
uint32_t dg_sound_dry[4];
int DG_Phase(void);

/* ---- the cache ------------------------------------------------------------ */

/* Stop every channel playing from [lo, hi) of the cache, and forget the
 * entries there. */
static void evict(uint32_t lo, uint32_t hi)
{
    for (int k = 0; k < CHANNELS; k++) {
        channel_t *c = &channels[k];
        if (c->data && c->data >= cache + lo && c->data < cache + hi)
            c->data = NULL;
    }
    for (int i = 0; i < n_entries;) {
        entry_t *e = &entries[i];
        if (e->off < hi && e->off + e->size > lo)
            entries[i] = entries[--n_entries];
        else
            i++;
    }
}

/* Room for `size` bytes at the head, wrapping to the start when the end
 * cannot hold them, and never over [keep, keep + keep_size). The offset, or
 * -1 when that would overwrite `keep`. */
static int32_t make_room(uint32_t size, uint32_t keep, uint32_t keep_size)
{
    uint32_t at = head + size > CACHE_BYTES ? 0 : head;
    if (keep_size && at < keep + keep_size && at + size > keep)
        return -1;
    evict(at, at + size);
    if (n_entries == ENTRIES)
        evict(entries[0].off, entries[0].off + entries[0].size);
    head = at + size;
    return (int32_t)at;
}

/* Forget the entry at `off`; channels still playing its bytes play on until
 * they are overwritten. */
static void forget(uint32_t off)
{
    for (int i = 0; i < n_entries; i++)
        if (entries[i].off == off) {
            entries[i] = entries[--n_entries];
            return;
        }
}

static entry_t *find(int lump)
{
    for (int i = 0; i < n_entries; i++)
        if (entries[i].lump == lump)
            return &entries[i];
    return NULL;
}

/* The lump's bytes in the cache: a hit in the older half moves to the head,
 * a miss is read from the card. NULL when it does not fit. */
static const uint8_t *lump_bytes(int lump, uint32_t *size)
{
    entry_t *e = find(lump);
    if (e) {
        if ((head - e->off + CACHE_BYTES) % CACHE_BYTES > CACHE_BYTES / 2) {
            uint32_t from = e->off, n = e->size;
            int32_t at = make_room(n, from, n);
            if (at >= 0) {
                memmove(cache + at, cache + from, n);
                forget(from);
                entries[n_entries++] = (entry_t){ lump, (uint32_t)at, n };
                *size = n;
                return cache + at;
            }
            e = find(lump);
        }
        *size = e->size;
        return cache + e->off;
    }
    uint32_t n = (uint32_t)W_LumpLength(lump);
    if (n > CACHE_BYTES / 4)
        return NULL;
    int32_t at = make_room(n, 0, 0);
    W_ReadLump(lump, cache + at);
    dg_sound_reads++;
    entries[n_entries++] = (entry_t){ lump, (uint32_t)at, n };
    *size = n;
    return cache + at;
}

/* ---- doomgeneric's sound module ------------------------------------------- */

static boolean Init(boolean use_sfx_prefix)
{
    sfx_prefix = use_sfx_prefix;
    for (int i = 0; i < CHANNELS; i++)
        channels[i].pending = -1;
    return true;
}

static void Shutdown(void)
{
    for (int i = 0; i < CHANNELS; i++) {
        channels[i].data = NULL;
        channels[i].pending = -1;
    }
}

static int GetSfxLumpNum(sfxinfo_t *sfx)
{
    char name[9];
    if (sfx->link)
        sfx = sfx->link;
    if (sfx_prefix)
        M_snprintf(name, sizeof(name), "ds%s", DEH_String(sfx->name));
    else
        M_StringCopy(name, DEH_String(sfx->name), sizeof(name));
    return W_CheckNumForName(name);
}

static void Update(void) {}

static void UpdateSoundParams(int handle, int vol, int sep)
{
    (void)sep;
    if (handle >= 0 && handle < CHANNELS)
        channels[handle].vol = vol;
}

/* Start `c` on the lump's bytes. A DMX lump: format 3, the rate, the sample
 * count, then the samples with 16 bytes of padding at each end, which are not
 * played. */
static void start(channel_t *c, const uint8_t *d, uint32_t size)
{
    if (!d || size < 8)
        return;
    uint32_t rate = (uint32_t)(d[2] | d[3] << 8);
    uint32_t n = (uint32_t)d[4] | (uint32_t)d[5] << 8 | (uint32_t)d[6] << 16
                 | (uint32_t)d[7] << 24;
    if (d[0] != 3 || d[1] != 0 || !rate || n > size - 8 || n <= 32)
        return;
    c->len = n - 32;
    c->pos = 0;
    c->step = (rate << 16) / RATE;
    c->data = d + 8 + 16;
}

static int StartSound(sfxinfo_t *sfx, int channel, int vol, int sep)
{
    (void)sep;
    if (channel < 0 || channel >= CHANNELS || sfx->lumpnum < 0)
        return -1;
    channel_t *c = &channels[channel];
    c->data = NULL;
    c->pending = -1;
    c->vol = vol;
    if (find(sfx->lumpnum)) {
        uint32_t size = 0;
        const uint8_t *d = lump_bytes(sfx->lumpnum, &size);
        start(c, d, size);
        return c->data ? channel : -1;
    }
    c->pending = sfx->lumpnum;
    return channel;
}

static void StopSound(int handle)
{
    if (handle >= 0 && handle < CHANNELS) {
        channels[handle].data = NULL;
        channels[handle].pending = -1;
    }
}

static boolean SoundIsPlaying(int handle)
{
    return handle >= 0 && handle < CHANNELS
        && (channels[handle].data != NULL || channels[handle].pending >= 0);
}

/* The lumps channels are waiting for, read into the cache; dg_cart.c calls
 * this at the end of _draw. */
void DG_SoundLoad(void)
{
    for (int k = 0; k < CHANNELS; k++) {
        channel_t *c = &channels[k];
        if (c->pending < 0)
            continue;
        uint32_t size = 0;
        const uint8_t *d = lump_bytes(c->pending, &size);
        c->pending = -1;
        start(c, d, size);
    }
}

static snddevice_t devices[] = {
    SNDDEVICE_SB, SNDDEVICE_PAS, SNDDEVICE_GUS, SNDDEVICE_WAVEBLASTER,
    SNDDEVICE_SOUNDCANVAS, SNDDEVICE_AWE32,
};

sound_module_t DG_sound_module = {
    devices, sizeof(devices) / sizeof(devices[0]),
    Init, Shutdown, GetSfxLumpNum, Update, UpdateSoundParams,
    StartSound, StopSound, SoundIsPlaying, NULL,
};

/* ---- the pump ------------------------------------------------------------- */

static int32_t acc[DEPTH];
static int16_t out[DEPTH];
static const int16_t silence[DEPTH];

/* One channel into acc, each output frame between two of the effect's
 * samples; the channel frees itself at the effect's end. An 11,025 Hz
 * effect, all but one of them, takes two output frames per sample: the
 * sample, and the point half way to the next. */
static void mix(channel_t *c, int32_t n)
{
    const uint8_t *d = c->data;
    uint32_t len = c->len, pos = c->pos, step = c->step;
    int32_t g = c->vol * 2, i = 0;
    if (step == HALF) {
        uint32_t k = pos >> 16;
        if (pos & HALF) {
            if (k + 1 >= len)
                goto done;
            acc[i++] += (((int32_t)d[k] + d[k + 1] - 256) * g) >> 1;
            k++;
        }
        int32_t a = k < len ? ((int32_t)d[k] - 128) * g : 0;
        while (i + 1 < n && k + 1 < len) {
            int32_t b = ((int32_t)d[k + 1] - 128) * g;
            acc[i] += a;
            acc[i + 1] += (a + b) >> 1;
            a = b;
            i += 2;
            k++;
        }
        if (i < n && k < len) {
            acc[i++] += a;
            c->pos = (k << 16) | HALF;
        } else {
            c->pos = k << 16;
        }
        if ((c->pos >> 16) + 1 >= len && i < n)
            goto done;
        return;
    }
    for (; i < n; i++) {
        uint32_t k = pos >> 16;
        if (k >= len)
            goto done;
        int32_t s0 = d[k], s1 = k + 1 < len ? d[k + 1] : s0;
        int32_t v = ((s0 - 128) << 8) + (((s1 - s0) * (int32_t)(pos & 0xffff)) >> 8);
        acc[i] += (v * c->vol) >> 7;
        pos += step;
    }
    c->pos = pos;
    return;
done:
    c->data = NULL;
}

static int32_t last_pump, gap16, left, last_phase;

/* Top the console's queue up to the target: the longest recent gap between
 * two pumps, in frames, and a block, within [2 blocks, DEPTH]. The gap is
 * kept in sixteenths of a millisecond so that it eases all the way down. */
void DG_SoundPump(void)
{
    int32_t now = moy_time();
    int32_t since = last_pump ? now - last_pump : 0;
    int phase = DG_Phase();
    int gap_phase = phase > last_phase ? phase : last_phase;
    last_pump = now;
    last_phase = phase;
    gap16 = since * 16 > gap16 ? since * 16 : gap16 - gap16 / 512;
    int32_t target = gap16 * (RATE / 16) / 1000 + BLOCK;
    if (target < 2 * BLOCK)
        target = 2 * BLOCK;
    if (target > DEPTH)
        target = DEPTH;
    dg_sound_target = (uint32_t)target;
    int32_t room = moy_snd(0, 0);
    int32_t dry = since * (RATE / 50) / 20 - left;
    if (room == DEPTH && dry > 0 && dg_sound_frames)
        dg_sound_dry[gap_phase] += (uint32_t)dry;
    int32_t n = target - (DEPTH - room);
    if (n > room)
        n = room;
    left = DEPTH - room + (n > 0 ? n : 0);
    if (n <= 0)
        return;
    int active = 0;
    for (int k = 0; k < CHANNELS; k++)
        active |= channels[k].data != NULL;
    if (!active) {
        dg_sound_frames += (uint32_t)moy_snd(silence, n);
        return;
    }
    memset(acc, 0, (size_t)n * sizeof(acc[0]));
    for (int k = 0; k < CHANNELS; k++)
        if (channels[k].data)
            mix(&channels[k], n);
    for (int32_t i = 0; i < n; i++) {
        int32_t v = acc[i];
        out[i] = (int16_t)(v > 32767 ? 32767 : v < -32768 ? -32768 : v);
    }
    dg_sound_frames += (uint32_t)moy_snd(out, n);
}
