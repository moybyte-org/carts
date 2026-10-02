#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 Nikola Jovicic
"""Write carts/doom/cover.png: the word DOOM, in lettering drawn here.

    python3 carts/doom/cover.py

The cover is the cart's picture in a console's list (moy-spec SPEC.md 3.6):
128x128, indexed. Any frame of Doom shows the WAD's art, which is id
Software's and never hosted here, and the game's logo is theirs too -- so
this draws the name in blocky slab letters of its own, on a 9-row grid, with
colours picked in Doom's spirit: a near-black, red-brown ground, and letters
running from yellow at the top through orange to a deep red, with a lit top
edge, a dark lower edge and a black outline. Nothing is read from a WAD.

Standard library only; the same picture on every run.
"""

import os
import struct
import zlib

SIZE = 128
CELL_W, CELL_H = 4, 6
LETTERS = {
    "D": ["#####.",
          "######",
          "##..##",
          "##..##",
          "##..##",
          "##..##",
          "##..##",
          "######",
          "#####."],
    "O": [".####.",
          "######",
          "##..##",
          "##..##",
          "##..##",
          "##..##",
          "##..##",
          "######",
          ".####."],
    "M": ["##...##",
          "###.###",
          "#######",
          "##.#.##",
          "##...##",
          "##...##",
          "##...##",
          "##...##",
          "##...##"],
}
WORD = "DOOM"
GAP = 4


def mix(a, b, t):
    return tuple(int(round(a[i] + (b[i] - a[i]) * t)) for i in range(3))


# The letters' ramp, top to bottom, in ten steps.
RAMP = [(255, 236, 120), (255, 206, 60), (250, 168, 30), (236, 124, 20), (214, 86, 16),
        (190, 56, 14), (164, 34, 12), (138, 20, 10), (112, 12, 8), (88, 8, 6)]
OUTLINE = (12, 4, 2)
SHADOW = (40, 6, 4)
GROUND_TOP, GROUND_BOTTOM = (34, 10, 6), (8, 3, 2)


def word_mask():
    """{(x, y): row of the letter grid} for every pixel of the word."""
    width = sum(len(LETTERS[ch][0]) * CELL_W for ch in WORD) + (len(WORD) - 1) * GAP
    height = 9 * CELL_H
    x0, y0 = (SIZE - width) // 2, (SIZE - height) // 2
    mask = {}
    lx = x0
    for ch in WORD:
        for gy, row in enumerate(LETTERS[ch]):
            for gx, c in enumerate(row):
                if c != "#":
                    continue
                for y in range(CELL_H):
                    for x in range(CELL_W):
                        mask[(lx + gx * CELL_W + x, y0 + gy * CELL_H + y)] = gy * CELL_H + y
        lx += len(LETTERS[ch][0]) * CELL_W + GAP
    return mask, y0, height


def picture():
    mask, y0, height = word_mask()
    seed = [0x5EED]

    def grain():
        seed[0] = (seed[0] * 1103515245 + 12345) & 0x7FFFFFFF
        return (seed[0] >> 16) % 3
    rows = []
    for y in range(SIZE):
        row = []
        ground = mix(GROUND_TOP, GROUND_BOTTOM, abs(y - SIZE / 2) / (SIZE / 2))
        for x in range(SIZE):
            if (x, y) in mask:
                step = min(len(RAMP) - 1, mask[(x, y)] * len(RAMP) // height)
                c = RAMP[step]
                if (x, y - 1) not in mask:
                    c = mix(c, (255, 255, 220), 0.55)
                elif (x, y + 1) not in mask or (x + 1, y) not in mask:
                    c = mix(c, SHADOW, 0.6)
            elif any((x + dx, y + dy) in mask for dx in (-1, 0, 1) for dy in (-1, 0, 1)):
                c = OUTLINE
            elif any((x - d, y - d) in mask for d in (2, 3)):
                c = SHADOW
            else:
                g = grain()
                c = (ground[0] + g * 3, ground[1] + g, ground[2] + g)
            row.append(c)
        rows.append(row)
    return rows


def png(rows):
    palette, index = [], {}
    raw = bytearray()
    for row in rows:
        raw.append(0)
        for c in row:
            if c not in index:
                index[c] = len(palette)
                palette.append(c)
            raw.append(index[c])
    assert len(palette) <= 256

    def chunk(tag, body):
        return (struct.pack(">I", len(body)) + tag + body
                + struct.pack(">I", zlib.crc32(tag + body) & 0xFFFFFFFF))
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", SIZE, SIZE, 8, 3, 0, 0, 0))
            + chunk(b"PLTE", b"".join(bytes(c) for c in palette))
            + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
            + chunk(b"IEND", b""))


def main():
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cover.png")
    data = png(picture())
    with open(out, "wb") as f:
        f.write(data)
    print("%s: %d bytes" % (out, len(data)))


if __name__ == "__main__":
    main()
