# Moy Mesh

Text messages on a Moybyte console's LoRa radio, on Meshtastic's channels
and directly to a node under its key; it works with Meshtastic. MIT, see
[LICENSE.txt](LICENSE.txt).

The window (`main.lua`) lists what was heard and sent, newest first, each
message another node sent with its RSSI and SNR, and sends what is typed. Its
service (`service.lua`) holds the radio and keeps listening while the window
is closed and a game plays: it is a Meshtastic node of its own, with a node
number, an X25519 key pair and a name it announces (its NodeInfo: the
console's name unless the app's prefs set one), a table of the nodes it heard
(`nodes`), and direct messages to and from them under their keys, each of its
own tracked to delivered or failed. It keeps 30 messages over every
channel's and every node's thread in the app's prefs (`log`), the fullest
thread giving up its oldest first, and tells the window `heard` and `nodes`. Both run `mesh.lua` first (the manifest's
`sources`), which holds the app's id, the regions and presets, the radio's
setup (region, preset, frequency slot, hop limit, up to eight channels) and
Meshtastic's channel links (`meshtastic.org/e/#...`), read into a preview the
window confirms. The window sends it `send(text, to)` and, after the setup
changes, `retune()`; `service.lua`'s header documents both sides. The window is responsive: on a desk it
is a window of its own, laid out for the room its `_layout(w, h, fs)` is given.
A heard message's row names its sender and signal before its text, so a row
too long for the room loses the end of the text, never the signal.

| | |
|---|---|
| Region | picks the frequency, and must match the law where the console is: EU_868 until another is chosen |
| This node | the console's node id, `!` and eight hex digits, drawn at its first start |

To a Meshtastic network it is a `CLIENT_MUTE` node: it hears its channels,
talks on them, answers the acknowledgements and NodeInfo requests addressed to
it, and relays nothing. The protocol is read from the Meshtastic firmware and its
protobufs; `service.lua`'s header says which parts.

The cart requires `lora` and `prefs`, so a console without a LoRa radio
refuses to install it. A Moybyte checkout runs its tests on a copy,
`tests/fixtures/apps/moymesh.moy` (`tests/test_moymesh_app.py`).
