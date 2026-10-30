"""Protocol adapters: future LIN/Ethernet codecs reuse recorder and replay engines.

Each codec supplies its ASAM MDF bus-event encoding, decoding and TX validation.
No LIN or Ethernet support is implied by the interfaces defined here.
"""
from copy import deepcopy
from dataclasses import dataclass
import json
from typing import Any, Iterable, Protocol

import can

from signal_explorer.bus.canfd import validate_frame


@dataclass(frozen=True)
class BusEvent:
    timestamp: float
    channel: str | int | None
    direction: str
    protocol: str
    payload: Any


class EventWriter(Protocol):
    def write(self, event: BusEvent) -> None: ...
    def finish(self, metadata: dict) -> None: ...
    def abort(self) -> None: ...


class RecordingCodec(Protocol):
    protocol: str
    def capture(self, payload: Any) -> BusEvent: ...
    def writer(self, path, database=None) -> EventWriter: ...
    def read(self, path) -> Iterable[BusEvent]: ...
    def replayable(self, event: BusEvent) -> bool: ...
    def prepare_tx(self, event: BusEvent, options: dict) -> Any: ...


class CANEventWriter:
    def __init__(self, path, database=None):
        self.writer = can.MF4Writer(path, database=database, compression_level=2)
        self.writer._mdf.header.author = "Signal Explorer"

    def write(self, event):
        if event.protocol != "CAN":
            raise ValueError("CAN MDF codec cannot record another protocol.")
        msg = deepcopy(event.payload)
        msg.timestamp = event.timestamp
        msg.channel = 1  # Single source connection mapped in embedded session metadata.
        self.writer.on_message_received(msg)

    def finish(self, metadata):
        self.writer._mdf.attach(json.dumps(metadata, indent=2).encode(),
                                file_name="SignalExplorer-session.json", mime="application/json")
        self.writer.stop()

    def abort(self):
        self.writer._mdf.close()
        self.writer.file.close()


class CANCodec:
    protocol = "CAN"

    def capture(self, msg):
        return BusEvent(msg.timestamp, msg.channel, "rx" if msg.is_rx else "tx", "CAN", deepcopy(msg))

    def writer(self, path, database=None):
        return CANEventWriter(path, database)

    def read(self, path):
        with can.MF4Reader(path) as reader:
            for msg in reader:
                yield self.capture(msg)

    def replayable(self, event):
        return not event.payload.is_error_frame

    def prepare_tx(self, event, options):
        if event.protocol != self.protocol:
            raise ValueError("Recording protocol does not match the connected transport.")
        frame = deepcopy(event.payload)
        frame.channel = None
        frame.is_rx = False
        frame.timestamp = 0
        frame.error_state_indicator = False
        validate_frame(frame, options.get("fd_enabled", False))
        frame._check()
        return frame
