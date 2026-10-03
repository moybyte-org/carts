# Doom

id Software's Doom, via ozkl's [doomgeneric](https://github.com/ozkl/doomgeneric),
built as a compiled Moybyte cart for ESP32-S3 and ESP32-P4 consoles.
GPL-2.0-or-later, see [NOTICE](NOTICE).

Controls: the d-pad moves and turns, A fires, B uses, RUN opens the menu. On
touch-only boards the screen works as a 3×3 pad. It plays Doom's sound
effects on consoles with a speaker; there's no music.

Saving: RUN, Save Game, then a slot. A names an empty slot after the map you
are on and A again saves; Load Game lists the slots that hold a save. The
settings (screen size, detail, messages, volume) are kept too, whenever the
menu closes. Both stay on the console when the cart is updated, and go when it
is removed (the cart declares `saves/` and `default.cfg` writable).

`dg_cart.c` connects doomgeneric to the console: screen, input, clock, and
Doom's files, read through `read` and written whole through `write`. `dg_sound.c` mixes the sound effects into the console's audio. `recipe.py` fetches doomgeneric at a pinned commit, applies a few
small patches (each patched file says what changed), and links `main.wasm`.
