# Recording and replay extension points

## Current implementation

- `recording/codecs.py`: `BusEvent`, `RecordingCodec`, `EventWriter` contracts;
  the CAN implementation adapts python-can's MDF4 writer/reader.
  `lin/codec.py` provides LIN frame encoding and explicit application event extensions. BusEvent holds
  timestamp, source channel, direction, protocol and a protocol-specific payload.
- `recording/recorder.py`: bounded event queue, background writer, stop/drain,
  timestamp policy, temporary-file retention and atomic destination replacement.
- `recording/playback.py`: interruptible preflight, source selection, relative
  timestamp scheduling, TX validation through the codec, cancellation and progress.
- `ui/recording_panel.py`: shared record/save/replay workflow. The app currently
  wires this panel to the CAN service and its single active adapter/channel.
- `bus/service.py`: raw `traffic` events before trace queuing. RX observation is
  emitted from the reader thread; successful TX observation is emitted from the
  sending thread. Subscribers must be thread-safe if using DirectConnection.

MDF4 writer internals used for session attachments are isolated in CANEventWriter.
The dependency ranges currently target python-can 4.x and asammdf 8.x; round-trip
checks should be run before changing their major versions.

## Adding a protocol later

Implement a RecordingCodec with capture, writer, read, replayable and prepare_tx.
The writer implements write/finish/abort and must use that protocol's ASAM MDF
bus-event schema. The reader yields BusEvent values; prepare_tx returns the
transport's own TX payload, validated against supplied `tx_options`.

Bind the new transport's raw observation signal and send callback to the shared
recorder and scheduler. The CAN tab binds CANCodec; the simulated LIN tab binds LINCodec and uses
role-aware header/buffer-update replay. Bind further protocols similarly.
Protocol-specific framing and driver code must stay in codecs/transports, not in
the queue or timing engine. Contract tests demonstrate scheduler reuse using an
example payload; they do not implement another bus protocol or its MDF schema.

LIN simulation now implements role/schedule awareness, protected IDs, checksums
and buffered responses. Native hardware backends still need implementation. Ethernet will need frame/packet selection, interface mapping,
and careful treatment of link timing and higher-layer protocols. A protocol codec
must decide which captured events are reproducible and which cannot be directly
transmitted. Do not replay arbitrary packets or LIN events by treating them as
CAN frames. Each protocol needs its own schema and interoperability tests.

The current one-channel-to-MDF-bus-1 mapping must become an explicit channel map
for multi-channel recording. A future mixed-bus session should route each event
to the matching protocol writer/group and output transport. The single-protocol
interface makes that extension possible, but mixed-bus sessions are not currently
implemented. All protocols share timestamp ordering and clock-domain requirements;
separate hardware clocks need explicit synchronization, not simple concatenation.
