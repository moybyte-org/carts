# Moy Mesh

Text messages on a Moybyte console's LoRa radio, on Meshtastic's channels
and directly to a node under its key; it works with Meshtastic. MIT, see
[LICENSE.txt](LICENSE.txt).

The window (`main.lua`) opens on its conversations, each channel's thread
and each node's direct thread, the newest first with what is unread, then
Nodes and Radio. A thread is a chat: what other nodes sent on the left under
the sender's name and signal, what was sent on the right with its state, a
day's head where the thread crosses a day, and the field that sends at the
foot. A message's details (Enter, or a tap) name its channel, signal and
state; a failed message is sent again from them or by Enter. A direct thread
to a node whose key has not been heard asks the node for it; one whose key
changed holds the next message until the new key is accepted. Radio sets the
node's names, the region, the preset (after a confirm) and the channels, each
renamed, keyed, removed or copied as a channel link; Add channel reads a
link pasted or on the clipboard into a preview that Apply sets. Its
service (`service.lua`) holds the radio and keeps listening while the window
is closed and a game plays: it is a Meshtastic node of its own, with a node
number, an X25519 key pair (the console keeps it, `moybyte.key_make`: its
secret never reaches the app or its prefs) and a name it announces (its NodeInfo: the
console's name unless the app's prefs set one), a table of the nodes it heard
(`nodes`), and direct messages to and from them under their keys, each of its
own tracked to delivered or failed. It keeps 30 messages over every
channel's and every node's thread in the app's prefs (`log`), the fullest
thread giving up its oldest first, and tells the window `heard` and `nodes`. Both run `mesh.lua` first (the manifest's
`sources`), which holds the app's id, the regions and presets, the radio's
setup (region, preset, frequency slot, hop limit, up to eight channels) and
Meshtastic's channel links (`meshtastic.org/e/#...`), read into a preview the
window confirms. The window sends it `send(text, to)`, after the setup
changes `retune()`, and `act(verb, ...)` for what a person does to a node or
a message (ask for a key, accept one, delete or resend a message, set a
channel's key, drop a replaced channel's thread); `service.lua`'s header
documents both sides. Every stamp is `moybyte.clock()`'s, Unix seconds on
every console. The window is one responsive layout: the T-Deck's screen, a
Guition's and a desk window are the room its `_layout(w, h, fs)` is given.

| | |
|---|---|
| Region | picks the frequency, and must match the law where the console is: EU_868 until another is chosen |
| This node | the console's node id, `!` and eight hex digits, drawn at its first start |

To a Meshtastic network it is a `CLIENT_MUTE` node: it hears its channels,
talks on them, answers the acknowledgements and NodeInfo requests addressed to
it, and relays nothing. The protocol is read from the Meshtastic firmware and its
protobufs; `service.lua`'s header says which parts.

The cart requires `lora` and `prefs`, so a console without a LoRa radio
refuses to install it, and asks for `clipboard` to paste and copy channel
links. A Moybyte checkout runs its tests on a copy,
`tests/fixtures/apps/moymesh.moy` (`tests/test_moymesh_app.py`).
