# Signal Explorer

Desktop tool for connecting to automotive buses and inspecting live traffic.
Supports Classic CAN / CAN FD adapters, a simulated LIN workbench, experimental native Vector, Intrepid and PEAK LIN backends, and Ethernet monitoring/transmission with simulation, PC Ethernet ports and Intrepid ValueCAN4-2EL.

Python test scripts can now run against the connected CAN / CAN FD session in a
separate editor window: **File Viewer > Open Python Script...**. The editor includes
syntax colors, line numbers, save/edit/find controls, Run/Pause/Resume/Stop and
executing-line highlighting. Try **File Viewer > Open CAN Simulation Example** with
Virtual CAN and demo traffic connected; a source copy is in
`examples/scripts/can_demo_test.py`. It continuously sends eight-byte requests on
321 hex, verifies the demo peer's 221-hex echoes, and increments each byte until
Stop. Monitoring and logging continue independently.
See [docs/scripting.md](docs/scripting.md) for the API and control boundaries.
**File Viewer > Open Logfile** opens a read-only MDF4 log in the editor-style
window. Replay RX, TX, or both through the connected protocol session using 1x
recorded timing, with Pause/Resume/Stop and highlighted rows. Line delay controls
highlight refresh only, preserving transmission timing. See
[docs/file-viewer.md](docs/file-viewer.md) for log viewing and replay.
Ethernet and LIN Python scripting are reserved for a later release. The PDF
Install and User guides were updated October 30, 2026 for NI-XNET / USB-8502.
The companion scripting guide retains the October 29 scripting release.

See [Users Guide.pdf](Users%20Guide.pdf) for operation, UI reference, workflows,
troubleshooting and model-specific connector maps, and [Install Guide.pdf](Install%20Guide.pdf)
for deployment. The user guide follows the supplied Kvaser manual's layout conventions.
The new [SIGNAL EXPLORER Scripting User's Guide](Scripting%20User%27s%20Guide.pdf)
teaches the Python CAN API and common MCU tests with nine commented source files
in [examples/scripts/mcu](examples/scripts/mcu). Five use the built-in simulator;
four require matching firmware and refuse TX until their contract is confirmed.

Supported CAN backends (via [python-can](https://python-can.readthedocs.io/)):

| Adapter | python-can interface | Extra software |
| --- | --- | --- |
| Virtual (no hardware) | `virtual` | None |
| CANable (SLCAN) | `slcan` | Device on a COM port |
| CANable / candleLight | `gs_usb` | WinUSB / Zadig on Windows if needed |
| Kvaser (including U100-C) | `kvaser` | Kvaser CANlib drivers |
| PEAK PCAN (including PCAN-USB Pro FD) | `pcan` | PEAK drivers + PCAN-Basic API |
| Vector | `vector` | Vector driver + XL Library; assign channels in Vector Hardware Config |
| Intrepid ValueCAN / neoVI | `neovi` | Intrepid drivers; optional `pip install python-ics` |
| NI USB-8502 (one or two ports) | `nixnet` | 64-bit NI-XNET driver + `.[nixnet]` Python extra |

### NI USB-8502 / NI-XNET

Install the NI-XNET Windows driver with NI MAX before connecting the USB-8502,
following the NI installer prompts. Then, from this project's root:

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[nixnet]"
# To retain both optional vendor bindings:
.\.venv\Scripts\python.exe -m pip install -e ".[intrepid,nixnet]"
```

Choose **NI (USB-8502 / NI-XNET)** in the CAN adapter list and **Refresh devices**.
Detected entries identify model, serial, NI interface name and physical port.
Use the exact NI MAX name, such as `CAN1` or `CAN2`, rather than a zero-based index;
manual interface entry is supported. Classic CAN is the default. For CAN FD,
enable **CAN FD** and match arbitration/data rates; select **FD frame** and
optionally **BRS** for outgoing frames. The NI backend uses `fd_bitrate`.

**NI internal termination (120 ohm)** defaults off and is selectable only before
connection; it is disabled if the detected port cannot provide termination.
Enable it only where the bench needs an end resistor. Close NI MAX Bus Monitor
and other applications using that port before opening it here. The USB-8502 is
USB-powered and does not require an external supply for CAN.

The same monitoring, repeat TX, DBC, Python script and MF4 logging/replay paths
are used as for the existing adapters. Only high-speed CAN/FD NI interfaces are
enumerated; other NI-XNET hardware requires separate qualification. This does
not add NI-CAN/USB-8473, NI LIN or NI Ethernet support. NI native sessions and
conversion are mock tested; physical USB-8502 operation awaits bench testing.
See the guides for wiring, NI MAX verification, diagnostics and acceptance steps.

Run the complete suite, including NI mocks, with
`.\.venv\Scripts\python.exe -m pytest tests -q` after installing `.[dev]`.
NI driver installation is not required for the mocked tests.

A separate LIN tab supports Monitor/Master/Slave sessions with Virtual LIN,
**Intrepid ValueCAN4-2EL**, **Vector VN16xx / XL LIN** (including a VN1640 fitted
with LIN piggies), and **PEAK PLIN / PCAN-USB Pro FD**. Native transmission has
mock coverage; powered physical LIN validation is pending. The **Ethernet** tab
discovers **PC Ethernet ports through Npcap (Windows)** and **Intrepid ValueCAN4-2EL**,
and retains the isolated simulator. Npcap and the Intrepid optional package/driver
are needed only for their respective hardware. Live passive PC capture has been
checked; powered ValueCAN Ethernet and physical TX bench validation remain pending.
SENT remains future work. Both PDF guides cover this Ethernet hardware release;
see also [docs/ethernet.md](docs/ethernet.md) for setup, controls and limitations.

For **Intrepid LIN**, use the existing `[intrepid]` optional dependencies and
installed 64-bit `icsneo40.dll`. Select the detected **ValueCAN4-2EL** serial and
LIN 1 in the LIN tab. ValueCAN4-2 and ValueCAN3 are CAN-only and are excluded.
The app temporarily sets baud rate and the master resistor for the selected
role, restores device settings on disconnect, and never writes EEPROM. Slave
responses live in the adapter. The dedicated Intrepid **Wake** control is
disabled until a documented legacy API operation is available; use an external
master to wake a sleeping bus.

For **Vector LIN**, use the installed Vector drivers and XL Driver Library.
Refresh lists channels that the driver reports as active LIN-capable, with
serial, physical channel and piggy name. A CAN-only VN1610 is not listed as LIN.
Close other applications holding initialization access to the selected LIN
channel. No application/channel assignment is needed for the detected channel.

Native CAN and LIN sessions on the **same Vector or Intrepid device** are
currently mutually exclusive inside Signal Explorer. Disconnect one before
connecting the other; simultaneous multi-protocol access awaits a shared
native device session. Different devices can be used in separate protocol tabs.

For **PEAK LIN**, install PEAK's LIN drivers, **PLIN Manager** and the matching
64-bit **PLIN-API** (`PLinApi.dll`). PCAN-Basic is the CAN API and does not provide
LIN. In the LIN tab select **PEAK PLIN / PCAN-USB Pro FD**, click **Refresh**, choose
the detected LIN channel, role and baud rate, then **Connect LIN**. The PCAN-USB
Pro / Pro FD and PLIN-USB families use this API; a CAN-only PCAN-USB does not gain
LIN capability. No additional Python package is required. If necessary, set
`SIGNAL_EXPLORER_PLIN_DLL` to the absolute path of the installed DLL before launch.
Vendor components are installed separately and are not redistributed.

Master sends headers (plus locally configured responses) through **Send Header**
or **Start Schedule**. Slave responses are buffered in the adapter and transmit
only on incoming headers. Monitor enables no publishers. Existing LDF decoding,
MF4 logging and role-aware playback apply to native sessions. Simulated peers
and fault injection are available only in Virtual LIN. See [LIN details](docs/lin.md)
for hardware preparation and validation limits.

Vendor DLLs are **not** bundled. Install each vendor’s driver on the PC that has the hardware.

## Run

Python 3.12+ is required.

```powershell
cd "C:\Users\Jabe\Documents\Signal Explorer\signal-explorer"
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e .
python -m signal_explorer
```

Connect to **virtual** first. Enable **Inject demo traffic** to see frames without a physical bus.

## First development target

`src/signal_explorer/bus/service.py` is the bus session (connect, read thread, disconnect).
`src/signal_explorer/ui/main_window.py` is the connection bar and live trace.
Add a new adapter by extending `BACKENDS` in `src/signal_explorer/bus/backends.py`.

## Live monitor and transmit

The monitor defaults to timestamped history with **Auto-scroll (all messages)**
checked. Every frame gets a row. Uncheck it to display one fixed row per CAN ID
and standard/extended format, updating data, timestamp, direction and count.
Toggle any time: capture, filters and DBC decoding continue in both modes, and
switching back restores the most recent 10,000 frames. Older history is discarded
to bound memory usage.
Clear resets the display and frame count.

- **Send:** connect first, enter a hexadecimal ID and data bytes (0-8 for Classic CAN, up to 64 for CAN FD).
  Select Extended ID for 29-bit identifiers. Leave **Interval (ms)** blank to send once. Enter a positive whole number
  (for example `100`) and click **Send** to send immediately, then repeat every
  100 milliseconds. The **Send** button becomes **Stop** during repetition; click it again to stop. Fields are locked while sending;
  stop to edit them. Disconnect, closing the app, or a send error also stops
  repetition. Reconnecting never restarts it automatically. Intervals use a
  desktop timer and are approximate, not guaranteed real-time timing. TX means the adapter accepted the local
  send, not that another CAN node received or acknowledged it.
- **ID filter:** enter hex IDs/ranges such as `100, 200-2FF`, then Apply filter
  or Enter. Blank shows all IDs. Filtering only affects the display; hidden
  messages keep updating. Invalid expressions leave the previous filter active.
- **Load DBC:** select a local `.dbc` file to show message names and decoded
  signals with units. Existing rows are decoded too. Unmatched IDs stay raw;
  short or invalid payloads display a decode error without stopping capture.
  Unload DBC returns to raw display. DBC-based transmit editing is not included.

For a hardware-free check, select Virtual, enable Inject demo traffic and
connect. Load `examples/demo.dbc` to decode ID `100`. Send ID `200` with data
`DE AD BE EF`; send again with different data to see the same row update.

Run feature checks with `.\.venv\Scripts\python.exe -m unittest discover -s tests -v`.

## Vector VN1610 and Intrepid ValueCAN4

All original adapters (CANable SLCAN and gs_usb, Kvaser, Virtual) remain available.
Use **Refresh devices** after plugging in an adapter. Select an adapter before
choosing its physical channel. The app currently opens one channel at a time.

### Intrepid ValueCAN / neoVI

Install the vendor USB drivers and native 64-bit `icsneo40.dll`, then install the
optional binding with `.\.venv\Scripts\python.exe -m pip install -e ".[intrepid]"`.
The adapter uses the native API, **not COM4**. Signal Explorer lists the serial
number with each detected network. ValueCAN4-2 exposes **HSCAN (CAN1)** and
**HSCAN2 (CAN2)**. Other Intrepid models can also accept a manually entered network
name or network ID. Select the matching bus bitrate and Connect.

### Vector VN1610

Install the Vector USB drivers and the **64-bit XL Driver Library** from
https://www.vector.com/en/business-unit/testing-validation-diagnostics/products/network-interfaces/xl-driver-library/.
The USB driver alone is not enough. If the XL library is not on PATH or in the
standard Public Documents/Vector/XL Driver Library*/bin location, select
**Vector DLL**, choose your installed `vxlapi64.dll`, and restart Signal Explorer.
Alternatively set `SIGNAL_EXPLORER_VECTOR_DLL` to that absolute DLL path before
launching. Vendor DLLs stay outside this source repository.

Detected Vector channels show hardware name, serial number and physical CAN
channel. Signal Explorer passes these directly to the XL API, so no CANalyzer or
Signal Explorer application assignment is required in Vector Hardware Config.
With manual entry and no detected device selected, the number is the XL global
channel index (zero-based). Close applications holding incompatible bitrate
settings if a channel cannot be initialized.

Hardware validation on this PC: ValueCAN4-2 serial V2Q475 was detected and CAN1
opened/read/closed at 500 kbit/s without sending traffic. The installed Vector XL library now detects the VN1610 and both of its physical CAN channels. Detection and connection-option forwarding are also covered by
mocked tests; those do not substitute for a physical CAN traffic test.

## Kvaser U100-C and PEAK PCAN-USB Pro FD

Select **Kvaser (U100 / CANlib)** or **PEAK (PCAN-USB Pro FD / PCAN)**,
then Refresh devices. The channel list shows attached channels when available.
With no device attached, the API status is shown and manual entry remains
available. Kvaser uses zero-based CANlib channel indices. PEAK uses channel names
such as `PCAN_USBBUS1` and `PCAN_USBBUS2`; use detected names when other PEAK
adapters are also attached. Each PCAN-USB Pro FD CAN channel can be opened
individually. Kvaser virtual driver channels are identified by name in the list.

Classic CAN is the default. Enable **CAN FD** and set arbitration/data bitrates
for FD-capable devices. PEAK LIN channels use the separate LIN tab and PLIN API.
No vendor DLLs are bundled. On this PC both installed API libraries load;
physical U100-C / PCAN-USB Pro FD traffic tests await the actual hardware.

## Exploring controls and trace columns

Columns, from left to right: Timestamp, Counts, Bus Number, Message ID (hex),
Ext, Direction, DLC, Data, DBC Message, Signals, FD, BRS, ESI. Bus Number displays the native
adapter channel identifier (e.g. COM3, a zero-based Vector/Kvaser channel index,
or a PCAN channel name). Fixed-row counts distinguish channel, ID and format.

Connecting starts monitoring as before. Click **Pause Exploring** to freeze the
trace and its counters while keeping the adapter connected. The button becomes
**Start Exploring**; click it to resume. Frames received while paused are drained
and discarded from the trace, rather than buffered for replay. Message sending,
including repeated sends, remains available while paused. Disconnecting stops
monitoring; the explore button is disabled until the device is connected again.

## CAN FD and engine demo

Choose **Virtual**, check **CAN FD**, keep **Inject demo traffic** enabled, and
Connect. Load `examples/demo.dbc` to decode these synthetic frames every 100 ms:

- `100`: DemoStatus, counter and temperature (Classic CAN).
- `200`: EngineData, EngineRPM (rpm), MAF (g/s), O2B1S1Voltage (V), O2Lambda (Classic CAN).
- `300`: EngineFD, the engine signals plus SampleCounter, O2B2S1Voltage and O2B2Lambda
  in a **32-byte FD frame with BRS**. Some signals are beyond the first eight bytes.

These signals are simulated engineering examples, not real vehicle OBD-II requests
or responses. Classic virtual mode still produces IDs 100 and 200.

For sending FD frames, connect in FD mode, select **FD frame**, optionally select
**BRS** (bit-rate switch), enter data, and Send. The interval field and Send/Stop
behavior work for both formats. Classic frames can also be sent on an FD session.
FD supports exact payload sizes **0-8, 12, 16, 20, 24, 32, 48, 64 bytes**. Other
sizes are rejected with an explanation rather than silently padded. The trace DLC
column shows byte count (python-can convention), not the encoded four-bit DLC.
FD, BRS and ESI columns show received flags; ESI is not a user-send option.

Hardware connection settings support Vector XL, Kvaser CANlib, Intrepid neoVI,
and PEAK PCAN. Arbitration bitrate defaults to 500 kbit/s and data bitrate to
2 Mbit/s. PEAK gets explicit calculated timing (80 MHz clock, 80% nominal and 75%
data sample points). Other hardware backends use their driver timing defaults.
Known unsupported channel capabilities are rejected; driver initialization may
also reject unsupported settings. CANable SLCAN and gs_usb remain available in
Classic CAN mode; CANable FD firmware-specific support is not enabled here.

Validation uses virtual CAN for FD receive/transmit up to 64 bytes, repeated
sends, BRS, length validation, ID filters, pause/resume and DBC decoding. Hardware
FD connection arguments are tested with mocks. Physical CAN FD timing, wiring,
and vendor behavior have not been validated by these tests. Virtual CAN delivers
frames in memory and does not simulate arbitration, bitrate switching timing,
acknowledgement or bus load.

## MDF4 raw bus logging and TX playback

Install/update the new dependencies with:

```powershell
.\.venv\Scripts\python.exe -m pip install -e .
```

The project now includes `python-can[mf4]` and `asammdf` (with its NumPy and other
transitive dependencies). Vendor drivers are unchanged.

Connect an adapter or virtual bus, then **Start Logging**. The button becomes
**Stop Logging**. Logging records both RX and successful local TX events,
including Classic CAN, CAN FD flags/payloads, remote frames and error frames.
Capture is independent of trace filtering, Clear and Pause Exploring. Stopping
finalizes the recording in a background worker, then prompts for a `.mf4` filename.
Cancelling keeps the recording: use **Save Log...** later. Starting another log
is disabled until that recording is saved. Save failures retain the temporary
original. Disconnect and normal application close also finalize and prompt;
cancelling a save during close keeps the app open.

Use **Playback Log as TX** to select a raw CAN MDF4 recording. Choose the recorded
bus number (our logs use bus 1), and RX + TX / recorded RX only / recorded TX only.
The selected traffic is sent through the **currently connected output channel**
at 1x recorded relative timing. The button becomes **Stop Playback**. Playback
validates the complete selected stream before sending, stops repeated manual
sending, and is stopped by disconnect or application close. Error frames are
retained in recordings but skipped in replay. ESI during TX is hardware-owned;
recorded ESI is not forced onto the outgoing frame. Incoming traffic continues.
Playback TX is included if logging is active. TX entries mean adapter acceptance,
not proof that another node received or acknowledged the frame.

Files use **ASAM MDF 4.10 CAN bus-event structures**, not a custom file with an
MF4 extension. They are produced by python-can's MF4Writer / asammdf. A DBC loaded
at recording start is embedded; raw frames remain available without it. An
embedded `SignalExplorer-session.json` records the original adapter/channel,
bitrates, source serial when available, numeric bus mapping and any incomplete
recording/timestamp correction information. Source channel names such as COM3 are
mapped to MDF BusChannel 1 because the current app opens one source at a time.

This is ASAM-format implementation, **not an ASAM certification or a guarantee
of compatibility with every external MDF producer/consumer**. Files have been
round-trip checked using MF4Reader and asammdf; raw CAN group fields, bus-event
flags, direction, timestamps, attachments and FD DLC encoding are tested. Playback
expects supported raw CAN bus-event groups, not arbitrary decoded-signal-only
MDF files. Testing against CANoe/CANalyzer and other engineering tools is still
needed before claiming broad interoperability.

Timing uses source timestamps and a monotonic software scheduler. The first
selected replayable frame is sent immediately, preserving the gaps thereafter.
It is best-effort desktop timing, not deterministic hardware replay; the UI
reports maximum scheduling lateness. Virtual tests cannot verify physical bus
arbitration or timing. Replaying RX + TX can intentionally reproduce both sides
of a conversation, including duplicate observations; use direction selection
when you only want one side.

Raw events enter a bounded 100,000-event queue from the receive/send threads;
MDF writing and finalization happen off the UI thread. Overflow or write failure
stops logging explicitly and marks a finalized partial log incomplete. Invalid
or backwards timestamps are corrected to maintain a chronological stream and
the number of corrections is recorded. Temporary files are under the OS temp
folder `signal-explorer-recordings`; they are not guaranteed crash/power-loss
recovery files and may be removed by OS cleanup. Normal save cancellation keeps
the finalized file. Sustained high-bus-load performance, automatic rotation and
hardware-synchronized replay have not been qualified.

The `recording` package separates reusable event/codec interfaces, recording,
save handling and replay scheduling from CAN encoding. See
[docs/recording.md](docs/recording.md) for the extension points for future LIN and
Ethernet transports. LIN has a simulated transport, dedicated codec and experimental native Vector, Intrepid and PEAK backends. Ethernet supports simulation, Windows Npcap and ValueCAN4-2EL transports with the same recording and replay infrastructure. Its MF4 raw-frame schema remains application-specific, including for hardware captures.

## LIN simulation workbench

Restart the app and choose the **LIN** tab. Adapter settings above the tabs switch
with the selected protocol. Existing CAN adapters and CAN FD functions are kept.
LIN simulation can run while a CAN session remains connected. The bundled
`examples/demo.ldf` is loaded automatically; its packaged copy is included in
installed builds. Install/update `ldfparser` with the normal project install:
`.\.venv\Scripts\python.exe -m pip install -e .`.

- **Monitor:** leave Simulated peer checked and Connect LIN. A peer master polls
  IDs 10 and 20, with a peer slave supplying the slave-published response.
- **Master:** connect, then Send Header for one transaction or Start Schedule to
  run the editable slot table. Simulated peer supplies slave responses. A master
  can also publish its own frame data (ID 10 in the demo).
- **Slave:** connect with a simulated peer to receive master requests. Select
  locally published IDs and Apply Responses. Buffer updates do not transmit
  immediately; matching master headers trigger responses. ID 20 is the demo slave
  response. Stopping replay restores the manually configured buffers.

Pause Exploring only freezes the display/counters. Start/Stop Schedule is
separate. Clear and ID filtering do not change recording or scheduling. Sleep and
Wake simulate network state; fault injection affects the next header/transaction.
The simulator detects two masters, multiple response publishers, missing
responses, checksum errors and invalid PID parity. Classic/enhanced checksums,
PID parity and diagnostic-ID classic-checksum behavior are tested. Frame lengths
are 1-8 bytes; diagnostic IDs 3C/3D require eight bytes. Slot validation includes
1.4x nominal on-wire budget. Desktop timers still cannot guarantee physical LIN
timing.

Load LDF imports unconditional frame definitions, publishers, signal decoding,
initial data and supported schedule tables. Event-triggered/sporadic/diagnostic
schedule commands and LIN diagnostic transport/configuration services are not
implemented. Unsupported schedule entries produce an error, not silent execution.
The demo has FanCommand/Enable and EngineRPM/CoolantTemperature/FanActual signals.
Custom unconditional IDs and schedule slots can also be added.

LIN shares the MDF4 recorder/save workflow. Valid full frames use `LIN_Frame`
fields; headers, sleep/wake and faults use the clearly named custom
`SignalExplorer_LINEvent` extension. The LDF and session metadata are embedded.
These LIN records have been round-trip tested with asammdf, not independently
certified or validated in CANalyzer. **Playback Headers** (Master) replays the
recorded header timing using the current response publishers/data. **Replay
Response Buffers** (Slave) performs timed updates only for configured local IDs;
actual TX still requires an incoming master header. This is intentionally not
CAN-style complete-frame replay. Monitor mode cannot replay/transmit.

Virtual LIN does not model transceiver voltages, real break/sync timing, bus
contention waveforms or USB latency. Native **ValueCAN4-2EL, Vector XL LIN and
PEAK PLIN** options are also selectable. They have mocked driver coverage;
powered hardware validation is pending. The EL unit VE0245 was detected and
its settings read over USB without transmitting. See [docs/lin.md](docs/lin.md)
for native setup, limitations and the powered bench checklist.
