# Selective multi-channel CAN / CAN FD

Updated November 2, 2026.

Select the adapter backend, then click **Enable channels...**. Choose the physical
device by model/serial and check only the CAN channels you want to open. Confirm
with **OK**, choose bitrate and CAN FD settings, then **Connect**. The ordinary
Channel selector remains available for single-channel and manual connections.
Changing that selector clears the multi-channel selection. Disconnect before
changing enabled channels or bus timing.

Channel selections are saved per device and backend for this Windows user.
After confirming CAN1 and CAN2, those same channels are preselected on reconnect,
Refresh, and the next launch of Signal Explorer. The last used physical device
is restored even if discovery returns devices in a different order. A device
without a saved selection starts with its first channel enabled. Restoring
settings never opens a connection automatically.

Unavailable saved channels stay unchecked without replacing their saved settings;
they return when the hardware/channel becomes available again. If none of the
saved channels is available, choose available channels before connecting.
Canceling the channel dialog leaves the saved selection unchanged. Explicitly
choosing a single channel in the ordinary Channel dropdown replaces that device's
saved subset with the selected single channel.

All enabled channels use the selected arbitration bitrate, Classic/FD mode and
FD data bitrate. FD is rejected before opening if any checked channel reports
that it cannot support FD. Unchecked channels are not included in the session.
Vector and Intrepid use one native multi-channel handle per device; other CAN
drivers use individually opened channel handles with explicit TX routing.

## Monitor and send

The trace's **CAN Channel** column (formerly Bus Number) displays physical labels
such as CAN1 and CAN2. CAN Auto-scroll defaults unchecked (latest-value rows). Check it for retained
10,000-frame timestamped history. LIN/Ethernet defaults are unchanged.
History and latest-value modes keep channels separate,
even when the same ID appears on both. Direction still distinguishes RX and
locally accepted TX. FD/BRS/ESI flags, filtering and DBC decoding remain available.

Choose exactly one enabled **TX channel** before pressing Send. Once/repeated
transmission and log replay use that destination; the selector locks during a
repeat or replay. Transmission to a disabled channel is rejected, never broadcast.

For a VN1630 CAN1-to-CAN2 loopback, enable CAN1 and CAN2, leave CAN3/4 unchecked,
use matching bus timing and termination, then send on TX channel CAN1. Expect a
CAN1 TX row and a CAN2 RX row with matching ID/data. A local TX row alone is not
proof of reception.

## Hardware discovery and limits

Vector choices come from the XL driver's active CAN capability flags, physical
channel numbers and transceiver names. Installed CAN piggies and onboard CAN
controllers appear; LIN-only piggies and digital I/O are not offered as CAN.
Vector virtual channels have a separate device group and are not added to a
physical VN1630 session.

Intrepid uses explicit known device profiles and native network IDs. The
neoVI FIRE 3 profile exposes CAN1-CAN16 and lists LIN1-LIN8 in its device inventory.
High network IDs (CAN9-CAN16) are preserved; they are not consecutive CAN indices.
This CAN selector does not open LIN sessions or add a FIRE 3 LIN transport.
The existing LIN tab's qualified/experimental backends remain separate, and the
same Intrepid device cannot be opened simultaneously by the current CAN and LIN
services. Unknown Intrepid models retain conservative single-network/manual
selection rather than being assigned the FIRE 3 layout.

FIRE 3 hardware reference:
[Intrepid neoVI FIRE 3](https://intrepidcs.com/project/neovi-fire-3/?lang=ko).
Vector backend reference:
[python-can Vector interface](https://python-can.readthedocs.io/en/stable/interfaces/vector.html).

## Logging, playback and Python scripts

MF4 logging preserves separate BusChannel values and embeds the native-channel,
display-label and BusChannel mapping in `SignalExplorer-session.json`. For Vector,
CAN1-CAN4 map to recorded buses 1-4, including nonadjacent channel selections.
Other backends use reported CAN labels when available, otherwise selection order. Older single-channel
recordings remain readable. Choose **Recorded bus** to select the source and
**TX channel** for the replay destination; replay is one recorded bus to one
enabled TX destination, not automatic multi-bus rebroadcast.

Scripts may target and filter a specific enabled channel:

```python
se.can.send(0x321, bytes(range(8)), channel="CAN1")
reply = se.can.recv(1.0, arbitration_id=0x321, channel="CAN2")
se.check(reply is not None, "CAN2 received CAN1 loopback")
```

`recv` timeout is in seconds. Omitting the TX channel uses the currently selected
TX destination; omitting the RX channel accepts any enabled channel. Native IDs
are available in `se.session["channels"]` and incoming frame `channel` values.
Existing single-channel scripts continue to work.

## Validation, November 2, 2026

VN1630A serial 604069 was physically discovered with four CAN channels, two
1057Gcap CAN piggies and two onboard 1051cap transceivers. With only CAN1/CAN2
enabled at 500 kbit/s Classic CAN, one standard ID 5A1 frame containing
`A5 5A 01 02 03 04 05 06` was sent on CAN1 and received on CAN2 without errors.
The session was closed afterward. CAN FD/BRS routing and multi-channel MF4
round trips are covered by simulated tests; no physical FD test was performed.
FIRE 3 channel discovery/routing is mock tested; FIRE 3 hardware was unavailable.

See [transmit-list.md](transmit-list.md) for per-row multi-message routing and
live editing, [scripting.md](scripting.md) for the channel API, and
[recording.md](recording.md) for codec/replay boundaries.
