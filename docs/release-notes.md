# Source build release notes

## November 10, 2026: DBC navigation, signal overlays, and log cleanup

- **DBC editor:** The original CAN ID list sits beside a new **Message Tree**
  with expandable signals and transmit/receive groups by network node. Filter
  both views as you type a message name or CAN ID.
- **DBC layout and bit map:** Resize the DBC preview with a vertical divider.
  The editor remembers its window, dividers, and signal column widths. The
  color-coded bit map displays DBC bit numbers and one label per visible
  signal field, with MSB/LSB markers for multi-bit signals.
- **Signal Graph:** Use **Add To** to overlay signals on an existing plot.
  Overlays have contrasting colors and share a vertical scale. For CAN
  replay, choose the destination channel and **Sent (TX)**.
- **Unsaved recordings:** On exit, choose **Yes** to save or **No** to discard
  the pending log and close. Canceling Save keeps the recording available.
- **Documentation:** Refreshed the User’s Guide, companion guides, and
  reference pages for these workflows.

## November 6, 2026: separate CAN Bus Health window

Add **Analysis > CAN Bus Health...** with per-channel frame-based load ranges,
rates, RX/TX totals, error observations, send failures and supported native
controller diagnostics. Show driver load separately from estimates and preserve
Unavailable/stale values. Separate display/script losses and recorder status.
Use bounded capture-side metrics and asynchronous read-only native queries;
trace filtering and display pause do not affect health observations. The main
trace layout was unchanged in that update.
See [bus-health.md](bus-health.md) for backend support and measurement limits.
Validation: 272 tests pass, with native behavior covered by mocks and virtual
traffic. Physical load/error-state qualification remains in Action Items.xlsx.

## November 6, 2026: CAN transmit robustness

Move GUI connection setup/cleanup, main sends and script send waits off the UI
thread. Add Connecting/Cancel/Disconnecting states and close late cancelled open
results before allowing reuse. Schedule cyclic rows independently of driver work
with bounded per-handle queues, one pending job per row and no catch-up bursts.
Request zero native send timeout; serialize shared handles and allow independent
handles to progress separately. Retry only known pre-acceptance rejection up to
three times with bounded backoff; uncertain timeouts are not retried.
See [transmit-robustness.md](transmit-robustness.md) for behavior and hardware limits.

## November 6, 2026: listen-only and CAN Timing window

Add a connection-level Listen-only option for Vector/PCAN/Kvaser/NI and the
virtual send restriction. Check backend setup/capabilities and prevent all
application CAN TX in this mode. Unsupported silent backends fail without an
active fallback. Normal adapter support remains.

Add **Timing > CAN timing and bitrate detection...** with custom rates,
backend-specific sample point/SJW preview, and cancellable passive Classic CAN
candidate scanning. Profiles last for the current session. Detection applies a
unique candidate only, retaining listen-only for the next connection. Hardware
qualification, Intrepid/CANable silent integration and FD detection remain open.
See [can-timing.md](can-timing.md) and the root **Action Items.xlsx** tracker.
All 253 automated tests pass; physical silent behavior has not been qualified.

## November 5, 2026: CAN timestamp provenance and driver TX reports

Add opt-in **Use driver TX reports** for Vector, Intrepid, Kvaser, PEAK PCAN and
NI-XNET. Request receive-own notifications and omit synthetic TX in this mode.
The default remains local acceptance estimates; CANable/virtual support remains.
Use a per-connection monotonic host epoch for CAN estimates and the Intrepid
shared RX/TX anchor. Preserve other driver-calibrated epoch timestamps.
Expose source/native timestamp/TX evidence in trace tooltips and script frames.
`send()` always returns host acceptance time; use a matching `recv(direction="tx")`
for native reports. Reports are not proof of intended-ECU receipt.

Reorder recorder arrivals within a bounded 50-ms window before chronological
repair; record reason counts and capped diagnostic samples for corrections.
CAN MDF session metadata includes timestamp source/evidence counts. Existing logs
are unchanged. All 230 tests pass, including five mocked native report backends;
physical timing/driver qualification is pending. See [timestamps.md](timestamps.md).

## November 5, 2026: script buffering and timing

Script receive history now retains unmatched frames. Its bounded 2,000-entry
ring evicts oldest data on overload, counts loss and warns without automatically
stopping a sleeping/paused script. Add `se.can.stats()`, `flush()` and per-script
ID/mask/direction/channel capture filters. `send()` returns the exact local TX
observation timestamp in Unix seconds. Replace worker polling waits with
condition notifications and wake pending receives only for matching arrivals.
Timeouts use monotonic capture arrival, preserving late frames for later calls.
Raw recording and monitor paths remain independent. See [scripting.md](scripting.md)
for API examples and timing/loss interpretation.

## November 5, 2026: CAN monitor performance

Batch CAN/CAN FD display updates every 40 ms with a bounded queue and explicit
Display drops count. Update row ranges once per batch, scroll once per batch and
refresh traffic counters at 10 Hz. Raw logging/script observation remains before
the display queue; pause/resume and Clear discard stale display generations.
Add constant-time DBC lookup, lazy shared decode caches for summary/child rows,
constant-time signal-parent lookup and indexed history storage. Copy payloads
once at capture to protect against reused driver buffers. See
[can-monitor-performance.md](can-monitor-performance.md) for limits and benchmarks.

## Windows installer 0.1.0-beta.2

Correct the minimum Windows build from 19041 to 17763 (Windows 10 version 1809),
matching Qt 6.12's documented Windows baseline. Windows 10 1909, build 18363,
was unnecessarily excluded by beta.1. The application remains x64-only; 32-bit
Windows is not supported by this installer. Add an explicit requirement message
and update the manual workflow/build defaults to beta.2. Installer compilation
does not establish runtime qualification on the older Windows VM.

## November 2, 2026 (application 0.1.0)

- Select a physical CAN device and enable individual ports with **Enable
  channels...**. Vector discovery reflects active CAN transceivers/piggies;
  Intrepid FIRE 3 inventory supports 16 CAN networks and lists 8 LIN networks.
  FIRE 3 LIN transport is not added. Classic CAN and CAN FD remain available.
- Remember the last CAN device and selected subset per backend/device for the
  Windows user. Restoration never connects or sends automatically. A new device
  defaults to its first available port. The initial application backend is Virtual.
- Display **CAN Channel** instead of Bus Number in the CAN trace. Separate
  same-ID traffic by channel. CAN Auto-scroll defaults off for latest-value rows;
  checking it restores retained timestamped history. LIN/Ethernet are unchanged.
- Retain the main single-frame sender and add **Multi-message TX...** with
  independent raw/DBC rows, explicit destination channels, once/cyclic sending,
  FD/BRS, full FD payload editing, signal values and Ramp/Pulse/Sine waveforms.
- Allow live message/signal edits while connected or sending. Valid edits apply
  atomically to later sends; invalid edits leave the previous configuration
  running. Manual signal edits stop that waveform; raw payload edits stop all
  waveforms on the row. In-flight frames may finish with old values.
- Add **Open TX List / Save TX List** buttons and retain File-menu equivalents.
  Version-1 JSON stores configuration, waveforms and private DBC definitions;
  loading never starts sending. Clone remains; Cut/Copy/Paste are removed from
  the sender window. Python editor clipboard controls remain available.
- Preserve enabled CAN sources in MF4 BusChannel values and the embedded session
  channel map. Replay one recorded bus to one selected TX destination. Starting
  replay stops GUI periodic senders; no automatic multi-bus replay is introduced.
- Add optional enabled `channel` arguments to Python `se.can.send/recv` and a
  `se.session["channels"]` inventory. Existing single-channel scripts still work.
  Python source editing remains locked while a script runs.

Validation: 196 automated tests pass, with 40 subtests and 11 existing Qt
`invalidateFilter` deprecation warnings. One VN1630A serial 604069 physical
Classic CAN check at 500 kbit/s transmitted standard ID `5A1` with
`A5 5A 01 02 03 04 05 06` on CAN1 and received identical data on CAN2 without
errors. The session was released afterward. Physical FD, FIRE 3 and new
multi-message workflows remain unqualified; virtual/mock checks cover those
software paths. Earlier NI/LIN/Ethernet hardware qualification limits remain.

No new runtime dependency or installer is added. The User Guide, Install Guide,
Scripting User's Guide, screenshots and supporting Markdown describe this build.
See [can-channels.md](can-channels.md), [transmit-list.md](transmit-list.md),
[scripting.md](scripting.md), [recording.md](recording.md) and
[file-viewer.md](file-viewer.md) for details.
