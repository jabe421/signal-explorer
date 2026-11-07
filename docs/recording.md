# Recording and replay extension points

Updated November 6, 2026.

## Current implementation

- `recording/codecs.py`: `BusEvent`, `RecordingCodec` and `EventWriter` contracts;
  CAN adapts python-can MDF4 bus events. `lin/codec.py` and `ethernet/codec.py`
  provide separate protocol codecs and application event extensions.
- `recording/recorder.py`: bounded queue, background writer, stop/drain,
  timestamp policy, retained temporary captures and atomic destination replacement.
- `recording/playback.py`: interruptible preflight, source/direction selection,
  relative timing, protocol TX validation, cancellation and progress.
- `ui/recording_panel.py`: shared record/save/replay UI for each protocol tab.
  Each capture is single-protocol; CAN includes every enabled CAN channel.
- `bus/service.py`: raw RX/TX observations before display queues, filters and
  monitor pause. Scripts and single/multi-message sending share this path.
  DirectConnection subscribers must be thread-safe.

CAN uses the python-can MF4 schema. LIN includes LIN_Frame and custom
SignalExplorer_LINEvent records; Ethernet uses custom
SignalExplorer_EthernetFrame records in an MDF 4.10 container. A valid container
and round-trip tests are not independent ASAM certification or standardized
Ethernet bus-event interoperability. CANEventWriter isolates writer internals
needed for session attachments. Dependencies target python-can 4.x/asammdf 8.x;
run round-trip tests before changing major versions.

## Channel mapping and replay

CAN preserves separate MDF BusChannel values for enabled ports. The embedded
`SignalExplorer-session.json` attachment contains a `channels` list with
`native_channel`, `label` and `bus_channel`. Vector CAN1-CAN4 map to buses 1-4,
even when only CAN2/CAN4 or CAN4 alone are enabled. Other backends use reported
CAN labels where available, otherwise selection order. A single source normally
uses bus 1; it is not always bus 1. Older single-channel logs remain readable.

Select one **Recorded bus**, RX/TX direction and one main **TX channel** for CAN
replay. Every selected replayable observation becomes TX at best-effort original
relative timing. Replay stops the main repeat sender and multi-message schedules;
reconnection does not restart them. This is not automatic multi-bus rebroadcast.
Logging during replay records the new session observations. Selecting both the
original TX and looped-back RX can intentionally duplicate the payload.

CAN error frames are not replayed. LIN replay is role-aware header sending or
timed response-buffer updates, not unsolicited complete frames in Slave mode.
Ethernet replay uses raw frames, adds required minimum padding, and preserves
recorded addresses; it provides no TCP/DoIP session reconstruction. Native LIN
and Ethernet backends exist, with physical qualification limits described in
the User Guide. Do not reinterpret another protocol's records as CAN frames.

## Replay timing measurements and waiting policy

Open **Timing > Replay timing...** for settings and live/last-run measurements
for CAN, LIN and Ethernet, including File Viewer replay. **Balanced** is the
default and performs no busy-wait. Windows replay uses a private high-resolution
waitable timer and a state-change event so pause/resume/stop interrupt long
waits. Global timer resolution is unchanged. If the native timer is unavailable
or fails, the report identifies the interruptible Python condition fallback.

**Precision** is optional: wake up to 0.5 ms early and check the final interval
outside the pause/stop lock. It may increase CPU use and cannot guarantee timing
under Windows scheduling or bus arbitration. Stop replay before changing mode;
the setting applies to the next replay in all protocol tabs and File Viewer.
Application restart restores Balanced.

Measurements distinguish scheduler lateness before the send callback, time
inside the callback, and lateness when it returns. These are host measurements,
not wire timestamps, CAN acknowledgement or ECU delivery. An in-flight native
send cannot be cancelled retroactively. Original logfile offsets, source/direction
selection, preflight and pause-adjusted deadlines remain in effect. Late events
are not silently skipped. File Viewer's line delay still affects display only.

Each metric stores whole-run count/mean/max and p50/p95/p99 from the most recent
4096 attempts. Sorting runs outside the replay lock. Failed sends are measured
too. The window retains stopped/failed/completed results; **Copy timing report**,
**Help > Diagnostics** and diagnostic logs include the report. Replay Output
includes a summary. Worker CPU counters are OS-accounted and may be quantized,
including zero for short runs. CPU/wall measurements cover the replay phase,
excluding preflight parsing. Elapsed replay wall time includes pauses.

Reproduce the scheduler comparison without hardware:

```powershell
python tools/benchmark_replay_timing.py --frames 600 --interval-ms 2 --repeats 3 --output tmp/replay-timing.json
```

On the November 6, 2026 development PC, three 600-frame runs at 2 ms spacing
gave median p95 scheduler lateness around 14.6 ms with condition waits, 0.66 ms
with the high-resolution timer, and 0.26 ms with Precision. These are observations,
not performance limits. VN1630A serial 604069 CAN1-to-CAN2 checks used 120-frame,
5 ms recordings in Classic CAN (500 kbit/s) and CAN FD (500 kbit/s / 2 Mbit/s),
two runs per mode/protocol. All eight replays arrived in sequence. Precision
reduced the median of those runs' p95 driver-reported RX spacing errors from
1.34 to 0.62 ms in Classic CAN and 1.09 to 0.71 ms in CAN FD. No independent
wire-time reference was used. Other hardware/load combinations need separate
qualification; timing varies with the PC's load.

Implementation references: [Windows high-resolution waitable timers](https://learn.microsoft.com/en-us/windows/win32/api/synchapi/nf-synchapi-createwaitabletimerexw)
and [interruptible multi-object waits](https://learn.microsoft.com/en-us/windows/win32/sync/waiting-for-multiple-objects).

## Queue overflow policies

Each protocol's logger uses a bounded 100,000-event queue. Select the policy
before **Start Logging**; it cannot change while recording/finalizing:

- **Stop on overflow** (default): count the event that could not be queued,
  stop accepting new events, drain queued data and finalize an incomplete log.
  Traffic after the stop is outside the recording and is not counted as dropped.
- **Continue with loss reporting**: discard the incoming event when full,
  retain queued events, and accept new events again as space becomes available.
  The incomplete warning remains for the rest of that session, even after
  recovery. This does not provide lossless recording under sustained overload.

The live status shows cumulative dropped events in red and refreshes at 10 Hz,
including while the disk writer is stalled. Capture avoids copying events that
cannot fit and does not write diagnostic logs or emit a signal for every drop.
On stop/disconnect, a single incomplete-recording warning precedes the normal
save dialog. A new session resets counters and warning state. Disk/write errors
stop either mode; continue mode applies only to queue overflow.

The `SignalExplorer-session.json` attachment stores `overflow_policy`,
`accepted_events`, `recorded_frames`, `dropped_events`, `dropped_rx_events`,
`dropped_tx_events`, `dropped_other_events`, `first_dropped_timestamp`,
`last_dropped_timestamp`, `incomplete`, `incomplete_reason`,
`overflow_drop_policy` and `loss_count_scope`. Unknown/invalid dropped-event
timestamps are null. After a normal drain, accepted events equal written events.
These are recorder-queue counts, distinct from display and driver loss counters.
The same policy/metadata apply to CAN, LIN and Ethernet codecs without changing
their existing file schemas or interoperability limitations.

Physical bench check, November 6, 2026: VN1630A serial 604069, CAN1 connected to
CAN2, passed normal recording and both overflow policies with Classic CAN at
500 kbit/s and CAN FD at 500 kbit/s / 2 Mbit/s (64-byte payloads). The recorder
engine saved the expected CAN1 TX/CAN2 RX data, loss counts and channel mapping,
and resumed capture after pressure cleared in continue mode. Overflow was
induced with a four-event queue and a controlled slow writer. This does not
qualify sustained bus saturation, actual disk-full recovery, other adapters or
physical LIN/Ethernet recording.

## Timestamp order and TX evidence

The recorder now reorders small arrival skews within a bounded 50-ms window
without modifying valid timestamps. Larger late arrivals and invalid timestamps
can still require correction. The embedded session attachment distinguishes
reordered frames from corrections, includes reason counts and up to 100
original/corrected/native timestamp samples, and records CAN source/evidence
counts. This is not clock synchronization or full native-timestamp recovery.

CAN's optional **Use driver TX reports** records only driver-observed TX events;
the default records host acceptance estimates. Missing native reports are not
replaced with synthetic confirmations. See [timestamps.md](timestamps.md) for
per-backend semantics, limitations and metadata fields.

## Reuse for future protocols

Implement a RecordingCodec with capture, writer, read, replayable and prepare_tx.
The writer implements write/finish/abort. Use an appropriate ASAM bus-event
schema where available; document custom extensions and interoperability limits.
Bind raw observations and the transport send callback to the shared recorder and
scheduler. Keep framing and driver logic in codecs/transports, not the queue or
timing engine. Contract tests demonstrate reuse without qualifying a new schema.

Mixed-protocol sessions and simultaneous output routing are not implemented.
Future mixed-bus recording must dispatch to the proper protocol writer/group
and output transport. Separate hardware clock domains require explicit
synchronization, not concatenation. Intrepid RX clock anchoring aligns hardware
intervals to host TX time per connection; it is not cross-adapter synchronization.
