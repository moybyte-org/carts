/*
 * SPDX-License-Identifier: GPL-2.0-or-later
 * Copyright (C) 2026 Nikola Jovicic
 *
 * Doom's sound effects for Moybyte's compiled-cart tier: doomgeneric's sound
 * module, mixing the effects that play into the console's sample stream --
 * `snd`, 22,050 Hz mono with a queue of 2,048 frames (moy-spec
 * proposals/wasm-runtime.md). dg_cart.c calls DG_SoundPump once a frame. It
 * asks the console for the room in its queue and mixes exactly that much, so
 * the effects run on the console's audio clock, whatever the frame rate.
 *
 * An effect is its WAD lump: DMX's 8-bit unsigned samples, at 11,025 Hz for
 * all but one of the shareware's. A channel reads it through the zone as
 * PU_CACHE and never pins it: the zone is 1 MiB, and eight pinned effects
 * are enough to leave a level's next screen-sized block nowhere to go. So a
 * lump the zone purges while it plays ends its channel, which the pump sees
 * before it reads another sample. Doom's stereo separation folds away: the
 * console is mono, and the two sides of a Doom channel always average to its
 * volume. There is no music.
 */
#include <stdint.h>
#include <string.h>

#include "deh_str.h"
#include "doomtype.h"
#include "i_sound.h"
#include "m_misc.h"
#include "w_wad.h"
#include "z_zone.h"

#define MOY(name) __attribute__((import_module("moy"), import_name(name)))

MOY("snd") int32_t moy_snd(const int16_t *pcm, int32_t n);

#define RATE 22050
#define DEPTH 2048
#define CHANNELS 16

typedef struct {
    const uint8_t *data;    /* the samples, past the lump's header and padding */
    uint32_t len;           /* samples */
    uint32_t at, frac;      /* the next sample, and 16 bits of the way past it */
    uint32_t step;          /* the effect's rate over RATE, 16.16 */
    int32_t vol;            /* 0..127 */
    int lump;               /* -1: the channel is free */
    const void *cache;      /* the lump's block while the zone keeps it */
} channel_t;

static channel_t channels[CHANNELS];
static boolean sfx_prefix;

/* Frames the console has taken from the pump, since _init. */
uint32_t dg_sound_frames;

static void release(channel_t *c)
{
    c->lump = -1;
}

/* Whether the zone still holds the channel's lump where it started. */
static boolean cached(const channel_t *c)
{
    return (unsigned)c->lump < numlumps && lumpinfo[c->lump].cache == c->cache;
}

static boolean Init(boolean use_sfx_prefix)
{
    sfx_prefix = use_sfx_prefix;
    for (int i = 0; i < CHANNELS; i++)
        channels[i].lump = -1;
    return true;
}

static void Shutdown(void)
{
    for (int i = 0; i < CHANNELS; i++)
        release(&channels[i]);
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

/* A DMX lump: format 3, the rate, the sample count, then the samples with 16
 * bytes of padding at each end, which are not played. */
static int StartSound(sfxinfo_t *sfx, int channel, int vol, int sep)
{
    (void)sep;
    if (channel < 0 || channel >= CHANNELS || sfx->lumpnum < 0)
        return -1;
    channel_t *c = &channels[channel];
    release(c);
    int lump = sfx->lumpnum;
    const uint8_t *d = W_CacheLumpNum(lump, PU_CACHE);
    uint32_t size = (uint32_t)W_LumpLength(lump);
    uint32_t rate = size >= 8 ? (uint32_t)(d[2] | d[3] << 8) : 0;
    uint32_t n = size >= 8 ? (uint32_t)d[4] | (uint32_t)d[5] << 8
                             | (uint32_t)d[6] << 16 | (uint32_t)d[7] << 24 : 0;
    if (size < 8 || d[0] != 3 || d[1] != 0 || !rate || n > size - 8 || n <= 32)
        return -1;
    c->cache = d;
    c->data = d + 8 + 16;
    c->len = n - 32;
    c->at = c->frac = 0;
    c->step = (rate << 16) / RATE;
    c->vol = vol;
    c->lump = lump;
    return channel;
}

static void StopSound(int handle)
{
    if (handle >= 0 && handle < CHANNELS)
        release(&channels[handle]);
}

static boolean SoundIsPlaying(int handle)
{
    return handle >= 0 && handle < CHANNELS && channels[handle].lump >= 0;
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

/* One channel into acc: each output frame interpolates between two of the
 * effect's samples, and the channel frees itself at the effect's end. */
static void mix(channel_t *c, int32_t n)
{
    const uint8_t *d = c->data;
    uint32_t at = c->at, frac = c->frac, len = c->len, step = c->step;
    int32_t vol = c->vol;
    for (int32_t i = 0; i < n; i++) {
        if (at >= len) {
            release(c);
            return;
        }
        int32_t s0 = (int32_t)d[at] - 128;
        int32_t s1 = at + 1 < len ? (int32_t)d[at + 1] - 128 : s0;
        int32_t v = s0 * 256 + (((s1 - s0) * (int32_t)frac) >> 8);
        acc[i] += (v * vol) >> 7;
        frac += step;
        at += frac >> 16;
        frac &= 0xffff;
    }
    c->at = at;
    c->frac = frac;
}

void DG_SoundPump(void)
{
    int32_t n = moy_snd(0, 0);
    if (n <= 0)
        return;
    if (n > DEPTH)
        n = DEPTH;
    memset(acc, 0, (size_t)n * sizeof(acc[0]));
    for (int k = 0; k < CHANNELS; k++) {
        channel_t *c = &channels[k];
        if (c->lump >= 0 && !cached(c))
            release(c);
        if (c->lump >= 0)
            mix(c, n);
    }
    for (int32_t i = 0; i < n; i++) {
        int32_t v = acc[i];
        out[i] = (int16_t)(v > 32767 ? 32767 : v < -32768 ? -32768 : v);
    }
    dg_sound_frames += (uint32_t)moy_snd(out, n);
}
