# Signal Explorer

Desktop tool for connecting to automotive buses and inspecting live traffic.
The first milestone is **CAN**: pick an adapter, connect, and read frames.

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

Ethernet, LIN, and SENT are planned as additional transports behind the same UI. They are not implemented yet.

Vendor DLLs are **not** bundled. Install each vendor’s driver on the PC that has the hardware.

## Run

Python 3.12+ is required.

```powershell
cd "C:\Users\Jabe\Documents\Signal Explorer"
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
for FD-capable devices. PEAK LIN channels are not exposed in the GUI.
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
