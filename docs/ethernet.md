# Ethernet workbench

Restart Signal Explorer and select **Ethernet**. The tab scans for adapters on
first display; **Refresh adapters** repeats discovery while disconnected. Select
**Simulated Ethernet**, a **PC Ethernet** interface or **Intrepid ValueCAN4-2EL**,
then **Connect Ethernet**. Discovery never opens a capture session or transmits.
The simulator needs no extra dependencies and remains the default.

## Hardware setup

**PC Ethernet (Windows):** install [Npcap](https://npcap.com/), then refresh.
Signal Explorer uses its installed `System32/Npcap/wpcap.dll` directly; no Scapy
or Python packet-capture package is needed. The list includes wired and virtual
Ethernet interfaces, with friendly name, MAC address and link up/down status.
Wi-Fi and loopback are excluded. Interfaces still appear when Npcap is missing
or cannot access them, but are disabled with a setup explanation. Administrator-only
Npcap installations may require running the application elevated; normal Npcap
capture permissions are otherwise sufficient. The app does not install drivers,
change your PC's IP configuration, or change link speed/duplex.

Check **Promiscuous capture** before connecting to include other destination MACs
visible on that port. This cannot reveal traffic hidden by a switch; use a mirror
port or Ethernet TAP for that. PC capture labels frames with the NIC's source MAC
as TX and other frames as RX. This is an inference, including traffic generated
by other PC applications. Npcap suppresses this application's injected echoes so
locally accepted sends are logged once. MAC rewriting, VLAN stripping and checksum
offload depend on the NIC/driver; an offloaded outbound checksum can appear invalid
in a capture even though the NIC later sends a correct checksum on the wire.

**ValueCAN4-2EL:** install the existing Intrepid drivers/64-bit `icsneo40.dll` and
the project optional dependencies: `python -m pip install -e ".[intrepid]"`.
Use a current python-ics release with the vendor's Ethernet ExtraDataPtr fixes
(1!26.3.9 or newer recommended; this build was tested with 1!26.3.17).
Refresh to find the device by serial number. CAN-only ValueCAN4-2/ValueCAN3 units
are excluded. Disconnect any CAN or LIN session using the same unit, and close
Vehicle Spy/neoVI Explorer before connecting. Separate devices can stay connected.

The ValueCAN4-2EL has a 10/100 Ethernet PHY and requires **external 12 V** for
Ethernet; USB discovery alone does not prove a working Ethernet link. Configure
its PHY in neoVI Explorer beforehand if necessary; Signal Explorer preserves
existing speed/duplex settings, temporarily enables the Ethernet network in RAM,
and restores settings on close. It never saves EEPROM settings. Device sessions
are exclusive across this app's CAN/LIN/Ethernet tabs. No DoIP activation-line
control, DoIP/XCP protocol stack or automatic ARP/IP stack is implemented here.
See [Intrepid's device specifications](https://intrepidcs.com/products/vehicle-network-adapters/valuecan-4/valuecan-4-2el-specifications/)
and [SVCAN4 settings reference](https://cdn.intrepidcs.net/support/neoVIDLL/SVCAN4Settings.htm).

Opening either hardware session starts capture. **Send**, repeated send and
playback perform real transmission on the selected port. Demo traffic, simulated
peer replies and **Inject Test RX** operate only on the virtual adapter. Loading
a test preset still works with hardware, but its default IP/destination MAC are
test addresses: edit the frame for your bench before sending. PC presets use the
selected NIC's source MAC; ValueCAN raw frames use the source MAC in the editor.
The capture handle being open is not a link-up or external-power verification.

Live discovery detected VE0245 and five PC Ethernet interfaces on the development
PC. Passive capture on its active PC port and clean shutdown were checked. Native
TX, recording/replay, rollback and driver errors have automated mock coverage;
physical TX and the powered ValueCAN Ethernet bench remain unvalidated.

## Try the simulator

1. Leave **Inject demo traffic** and **Simulated peer replies** checked, then
   press **Connect Ethernet**. The demo cycles UDP telemetry, ARP requests,
   VLAN-tagged UDP and experimental EtherType `88B5` raw frames every 100 ms.
2. Choose a test frame and press **Load Test Frame** to populate the editor.
   **Inject Test RX** instead injects one incoming test frame into the monitor.
3. Press **Send** for one simulated transmission, or enter an interval in
   milliseconds to repeat. While repeating, **Send** becomes **Stop** and the
   editor is locked. Disconnecting, closing, or starting playback also stops it.
4. **Pause Exploring** freezes trace updates and counters while the link,
   demo traffic, transmissions and logging continue. **Start Exploring** resumes
   with new frames; it does not backfill traffic received while paused.
5. Use **Start Logging**, then **Stop Logging**. Choose an `.mf4` filename when
   prompted. Canceling retains the recording for **Save Log...**. Both RX and TX
   are recorded, independently of trace filters, Clear, or monitor pause.
6. **Playback Log as Simulated TX** sends the selected recorded bus/direction
   through the simulated link at 1x relative timing. Each selected record becomes
   TX, including recorded RX. Disable demo traffic and peer replies before
   connecting for a replay-only test; otherwise these produce additional RX.
   **Stop Playback** cancels remaining sends.

## Trace and editor

The columns are timestamp (Unix seconds), cumulative count per retained flow, bus number,
direction, source/destination MAC, hexadecimal EtherType, VLAN ID, VLAN priority
(PCP), frame length, protocol, summary and hexadecimal payload. Scroll horizontally
to see all columns; selecting a row shows its complete frame bytes underneath.

**Auto-scroll (all frames)** displays chronological history, retaining the most
recent 10,000 frames. Uncheck it to update one row per flow instead. A flow uses
bus, direction, MAC addresses, EtherType, VLAN and decoded IP endpoints/ports.
Both history and flow caches retain up to 10,000 entries. Counts remain cumulative
until **Clear** or flow eviction. History and latest-flow views share
the same received data. **Clear** clears the trace and display counters only.

The search field matches all space-separated terms across MAC addresses, IP
endpoints, protocol, summary, VLAN and payload hex, without regard to case.
**Apply filter** or Enter applies it; EtherType accepts a hex value such as
`0800` or `0806`. The direction selector filters RX/TX independently of the
recording playback direction selector. **Clear filter** restores all rows.

The editor accepts source/destination MACs, an inner EtherType, optional single
802.1Q VLAN ID (0–4094) and PCP (0–7), and raw hexadecimal payload. Frames use a
standard 1500-byte maximum payload. Short frames are padded to 60 bytes; lengths
and bytes exclude preamble, SFD and FCS. Captured NIC frames can be shorter when
the driver omits padding; their actual bytes are retained, then padded on replay.
Source MAC must be nonzero and unicast.
Jumbo frames, stacked VLAN tags and IEEE 802.3 length-encoded frames are rejected.

UDP presets generate IPv4 and UDP headers/checksums. Editing raw payload bytes
does not recalculate checksums; malformed packets remain visible with a decode
warning. The simulated peer (`02:00:00:00:00:02`, `192.0.2.2`) replies to valid
UDP addressed to it and raw `88B5` frames with an `ACK:` payload. The local test
address is `02:00:00:00:00:01`, `192.0.2.1`. Replies are immediate simulation
events, not measured network latency. ARP presets are test frames, not a full
ARP stack. IPv6 has a raw label only; TCP/ICMP have basic IP summaries.

## Recording, diagnostics and extension points

Both simulation and hardware write an MDF 4.10 container with a custom structured signal
`SignalExplorer_EthernetFrame`: `BusChannel`, `Direction` (0 RX / 1 TX), `Length`
and a fixed-size `RawBytes` array. The valid bytes are selected using `Length`.
Absolute timestamps derive from the MDF start time and sample timestamps.
An attached `SignalExplorer-session.json` records the selected hardware or
simulation, schema version, FCS exclusion and TX semantics. This is an application-specific
schema, not standardized ASAM Ethernet bus-event interoperability. Existing
simulation MF4 files still replay; CAN and LIN
logs use their existing codecs and are rejected by Ethernet playback.

The codec and frame model round-trip MAC headers, VLAN fields, padding and raw
payload bytes. Playback preflights the entire file before sending; scheduling is
best-effort desktop timing, not a hardware timing guarantee. Hardware TX means
acceptance by the driver/API; peer reception is not confirmed. Successful Intrepid
TX reports are suppressed to avoid counting an accepted send twice; native TX
errors stop capture with a diagnostic. Simulation does not
model physical link speed, wiring, collisions, transceiver faults or packet loss.
No PCAP import/export, SOME/IP, DoIP or signal database is implemented in this build.

Native capture uses a background reader and a bounded 2,000-frame display queue.
**Display drops** reports UI queue overflow; this does not discard events from the
recorder, which has its own bounded queue and explicit overflow handling.
**Driver drops** reports Npcap buffer/interface drops (not available through this
Intrepid binding). **Unsupported/error frames** counts rejected packets such as
truncated captures, jumbo frames, stacked VLAN tags, 802.3 length-encoded frames,
invalid source MACs or Intrepid CRC/length errors. These rejected frames are not
logged. The current codec supports standard-MTU Ethernet II and one VLAN tag.
Receive/API failures stop the session, logging and replay; repeated send stops too.
Display RX/TX counters follow monitor pause, while recording stays independent.
No sustained high-bandwidth capture or lossless line-rate guarantee is claimed.

Implementation lives in `src/signal_explorer/ethernet/` (frame model, simulated
service, discovery, Npcap/Intrepid transports and codec) and
`ui/ethernet_panel.py` / `ui/ethernet_trace_model.py`.
It reuses the protocol-neutral recorder, replay scheduler and recording controls;
a standard Ethernet bus-event codec or further adapters can be added separately.
Run `python -m unittest discover -s tests -p "test_ethernet*.py" -v` for simulation,
frame/checksum validation, native driver mocks, discovery, trace/filter, recording
and playback checks.
