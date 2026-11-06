# Recording and replay extension points

Updated November 2, 2026.

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
