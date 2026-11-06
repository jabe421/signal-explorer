# Source build release notes

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
