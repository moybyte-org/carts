# Doom

id Software's Doom, via ozkl's [doomgeneric](https://github.com/ozkl/doomgeneric),
built as a compiled Moybyte cart for ESP32-S3 and ESP32-P4 consoles.
GPL-2.0-or-later, see [NOTICE](NOTICE).

Controls: the d-pad moves and turns, A fires, B uses, RUN opens the menu. On
touch-only boards the screen works as a 3×3 pad. It plays Doom's sound
effects on consoles with a speaker; there's no music.

`dg_cart.c` connects doomgeneric to the console: screen, input, clock and
file reads. `dg_sound.c` mixes the sound effects into the console's audio. `recipe.py` fetches doomgeneric at a pinned commit, applies a few
small patches (each patched file says what changed), and links `main.wasm`.
