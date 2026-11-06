# CAN listen-only and Timing window

Choose **Listen-only** before connecting CAN to request hardware silent mode.
The mode locks while connected. All application CAN transmit paths are blocked
by the shared send service, including Python, cyclic sending and MF4 replay.
RX, monitoring and logging continue. The virtual adapter simulates the send
restriction; it cannot verify electrical behavior. Normal mode remains default.

## Supported silent connections

- Vector XL requires initialization access to every selected channel. Close
  other applications using those channels; failure prevents activation.
- PCAN checks the native listen-only preset result before initialization.
- Kvaser checks the SILENT_MODE capability and requests silent, exclusive access.
- NI-XNET sets listen-only before starting streams and checks its property.
- Intrepid, SLCAN and gs_usb silent setup is not exposed in this release. A
  listen-only API request for these backends is rejected without active fallback.
  Their normal existing connections are retained.

Hardware qualification remains necessary. Silent mode is meant to suppress
data, ACKs and active errors, not merely disable the Send button. A lone sender
may retry because a passive monitor does not acknowledge its messages. Use two
active peers when testing with a listen-only analyzer.

## Timing menu

Open **Timing > CAN timing and bitrate detection...** for a separate modeless
window. Existing main-window bitrate presets remain available. Select **Use
custom connection rates**, enter rates in bit/s and apply for the next CAN
connection. Uncheck it and Apply to restore presets. Settings are held per
backend for this application session, not saved across restarts. They apply to
all enabled CAN channels; independent per-channel timing is not implemented.

Vector, PCAN, Kvaser and NI-XNET also allow sample point (%) and SJW (time quanta),
separately for nominal and FD data phases. Preview computes supported timing
using python-can BitTiming/BitTimingFd. Sample points are quantized; inspect the
calculated value rather than assuming the requested percentage is exact. The
backend selects a supported clock: Classic Vector/Kvaser 16 MHz, PCAN 8 MHz,
NI-XNET 40 MHz; FD Vector/Kvaser/PCAN 80 MHz and NI-XNET 40 MHz. Requested SJW
must fit the resulting timing. Drivers and hardware impose additional limits.
Applied requests and calculated timing are included in CAN session metadata.

Detailed timing is unavailable for Intrepid, CANable and Virtual. Driver-default
timing and supported rate selection remain available. Virtual bitrate settings
do not change physical timing or simulation scheduling.

## Passive bitrate detection

Disconnect CAN, select a supported Vector/PCAN/Kvaser/NI channel and Classic
CAN, then open the auto-detection tab. The displayed main channel is scanned,
not every enabled channel. Supply 1-32 candidate rates, observation interval
(0.1-10 s per candidate) and minimum valid data frames (2-1000). Defaults scan
common rates with 1 s per candidate and at least five valid data frames.

Each candidate opens only in hardware listen-only, discards startup queue data,
then counts valid Classic data frames and reported error events. A unique
candidate with enough frames and no reported errors is applied to the next
connection with Listen-only still selected. Ambiguous or quiet scans leave
settings unchanged and report inability to determine a rate. No test frame is
sent. Error reporting and traffic availability limit this heuristic; verify
known messages before relying on a candidate.

The scan runs outside the UI thread; CAN connection settings lock while it
owns the adapter. Cancel stops between driver calls and releases handles. A
blocking vendor open may delay cancellation; closing waits for cleanup instead
of destroying a running thread. A native failure aborts the scan without any
active fallback. No automatic application connection or TX follows detection.

FD data-rate detection is deferred. Classic traffic does not establish an FD
data-phase rate. Virtual and currently unsupported silent backends cannot run
this physical detection workflow.

## Verification and manual follow-up

Automated checks cover timing calculations, invalid requests, RX with TX
blocked, sender guards, scan cancellation/failure cleanup, Vector permission
failure, PCAN preset errors, Kvaser capability rejection and NI readback failure.
They are not electrical or physical timing qualification. **Action Items.xlsx**
tracks remaining hardware checks and earlier project follow-ups.

Primary references:

- [Vector python-can API](https://python-can.readthedocs.io/en/v4.6.1/interfaces/vector.html)
- [PCAN listen-only preset](https://www.peak-system.com/documentation/API/PCAN-Basic.Net/html/3e5e1a2d-b990-4cc6-9908-001585cd3871.htm)
- [Kvaser channel capabilities](https://kvaser.com/canlib-webhelp/canlib_8h.htm)
- [NI-XNET interface properties](https://nixnet.readthedocs.io/en/latest/api_reference/session/intf.html)
