# Moy Mesh

Text messages on a Moybyte console's LoRa radio, on the default LongFast
channel; it works with Meshtastic. MIT, see [LICENSE.txt](LICENSE.txt).

The window (`main.lua`) lists what was heard and sent, newest first, each
message another node sent with its RSSI and SNR, and sends what is typed. Its
service (`service.lua`) holds the radio and keeps listening while the window
is closed and a game plays: it keeps the last 30 messages in the app's prefs
(`log`) and tells the window `heard`. Both run `mesh.lua` first (the
manifest's `sources`), which holds the app's id and its regions. The window sends it `send(text)` and,
after the region changes, `retune()`. The window is responsive: on a desk it
is a window of its own, laid out for the room its `_layout(w, h, fs)` is given,
and a room narrower than the handheld's 320 lists the text without the signal.

| | |
|---|---|
| Region | picks the frequency, and must match the law where the console is: EU_868 until another is chosen |
| This node | the console's node id, `!` and eight hex digits, drawn at its first start |

To a Meshtastic network it is a `CLIENT_MUTE` node: it hears the channel and
talks on it, and relays nothing. The protocol is read from the Meshtastic firmware and its
protobufs; `service.lua`'s header says which parts.

The cart requires `lora` and `prefs`, so a console without a LoRa radio
refuses to install it. A Moybyte checkout runs its tests on a copy,
`tests/fixtures/apps/moymesh.moy` (`tests/test_moymesh_app.py`).
