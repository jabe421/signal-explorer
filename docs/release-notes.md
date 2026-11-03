# Source build release notes

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
