"""LIN MDF4 frame records plus explicit application event extensions.

Standard LIN_Frame fields are used for valid full frames. Headers, sleep/wake,
and faults use SignalExplorer_LINEvent so they are never mislabelled as frames.
"""
from dataclasses import replace
from datetime import datetime
import heapq
import json
from pathlib import Path
import time

import numpy as np
from asammdf import MDF, Signal
from asammdf.blocks.v4_blocks import SourceInformation
from asammdf.blocks.v4_constants import SOURCE_BUS, BUS_TYPE_LIN

from signal_explorer.lin.model import LinFrame, lin_checksum, checksum_model, protected_id
from signal_explorer.recording.codecs import BusEvent

KINDS = ("frame", "header", "no_response", "checksum_error", "parity_error", "collision", "sleep", "wake")


def _dtype(name):
    return np.dtype([(f"{name}.{field}", dtype) for field, dtype in (
        ("BusChannel", "u1"), ("ID", "u1"), ("PID", "u1"), ("Dir", "u1"),
        ("DataLength", "u1"), ("ReceivedDataByteCount", "u1"), ("DataBytes", "(8,)u1"),
        ("Checksum", "u1"), ("ChecksumModel", "u1"), ("EventKind", "u1"), ("PublisherName", "S64"))])


class LINEventWriter:
    def __init__(self, path, database=None):
        self.path = path
        self.mdf = MDF(version="4.10")
        self.mdf.header.author = "Signal Explorer"
        self.start = self.mdf.header.start_time.timestamp()
        self.names = ("LIN_Frame", "SignalExplorer_LINEvent")
        source = SourceInformation(source_type=SOURCE_BUS, bus_type=BUS_TYPE_LIN)
        for name in self.names:
            self.mdf.append(Signal(name=name, samples=np.zeros(0, dtype=_dtype(name)),
                                   timestamps=np.zeros(0), source=source))
        if database and Path(database).is_file():
            self.mdf.attach(Path(database).read_bytes(), file_name=Path(database).name)

    def write(self, event):
        if event.protocol != "LIN":
            raise ValueError("LIN codec cannot record another protocol.")
        frame = event.payload
        group = 0 if frame.kind == "frame" else 1
        name = self.names[group]
        row = np.zeros(1, dtype=_dtype(name))
        values = dict(BusChannel=1, ID=frame.frame_id, PID=frame.pid, Dir=int(event.direction == "tx"),
                      DataLength=len(frame.data), ReceivedDataByteCount=len(frame.data),
                      Checksum=frame.checksum or 0, ChecksumModel=int(frame.model == "enhanced"),
                      EventKind=KINDS.index(frame.kind), PublisherName=frame.publisher.encode()[:64])
        for field, value in values.items():
            row[f"{name}.{field}"] = value
        row[f"{name}.DataBytes"][0, :len(frame.data)] = list(frame.data)
        self.mdf.extend(group, [(np.array([event.timestamp - self.start]), None), (row, None)])

    def finish(self, metadata):
        metadata["event_extension"] = "SignalExplorer_LINEvent; EventKind: " + ", ".join(f"{i}={k}" for i,k in enumerate(KINDS))
        self.mdf.attach(json.dumps(metadata, indent=2).encode(), file_name="SignalExplorer-session.json", mime="application/json")
        self.mdf.save(self.path, overwrite=True, compression=2)
        self.mdf.close()

    def abort(self):
        self.mdf.close()


class LINCodec:
    protocol = "LIN"

    def __init__(self, role="monitor", response_ids=()):
        self.role = role
        self.response_ids = set(response_ids)

    def capture(self, frame):
        return BusEvent(frame.timestamp, frame.channel, frame.direction, "LIN", frame)

    def writer(self, path, database=None):
        return LINEventWriter(path, database)

    def read(self, path):
        with MDF(path) as mdf:
            start = mdf.header.start_time.timestamp()
            def events(group_index, name):
                cycles = mdf.groups[group_index].channel_group.cycles_nr
                for offset in range(0, cycles, 1000):
                    signal = mdf.get(name, group=group_index, record_offset=offset, record_count=1000)
                    for row, timestamp in zip(signal.samples, signal.timestamps):
                        def value(field, default=0):
                            return row[f"{name}.{field}"] if f"{name}.{field}" in row.dtype.names else default
                        kind = KINDS[int(value("EventKind"))] if name != "LIN_Frame" else "frame"
                        length = int(value("DataLength", value("ReceivedDataByteCount")))
                        if not 0 <= length <= 8:
                            raise ValueError("Invalid LIN length in recording.")
                        frame = LinFrame(float(timestamp)+start, int(value("ID")),
                                         bytes(value("DataBytes")[:length]), "tx" if value("Dir") else "rx",
                                         kind, int(value("Checksum")) if kind in ("frame", "checksum_error") else None,
                                         "enhanced" if value("ChecksumModel") else "classic",
                                         bytes(value("PublisherName", b"")).rstrip(b"\x00").decode(errors="replace"),
                                         int(value("BusChannel", 1)), int(value("PID", protected_id(int(value("ID"))))))
                        yield self.capture(frame)
            streams = []
            for i, group in enumerate(mdf.groups):
                names = {c.name for c in group.channels}
                for name in ("LIN_Frame", "SignalExplorer_LINEvent"):
                    if name in names:
                        streams.append(events(i, name))
            yield from heapq.merge(*streams, key=lambda event: event.timestamp)

    def replayable(self, event):
        if self.role == "master":
            return event.payload.kind == "header"
        return self.role == "slave" and event.payload.kind == "frame" and event.payload.frame_id in self.response_ids

    def prepare_tx(self, event, options):
        frame = event.payload
        if event.protocol != "LIN" or self.role not in {"master", "slave"}:
            raise ValueError("LIN playback requires Master or Slave mode.")
        if frame.frame_id not in options.get("lengths", {}):
            raise ValueError("Recorded ID is not defined in the current LIN session.")
        if self.role == "master":
            return ("master", frame.frame_id)
        if len(frame.data) != options["lengths"][frame.frame_id]:
            raise ValueError("Recorded response length differs from the current LIN definition.")
        expected_model = options.get("models", {}).get(frame.frame_id, frame.model)
        if checksum_model(frame.frame_id, expected_model) != checksum_model(frame.frame_id, frame.model):
            raise ValueError("Recorded checksum model differs from the current LIN definition.")
        if frame.pid != protected_id(frame.frame_id):
            raise ValueError("Cannot replay a LIN frame with invalid PID parity.")
        if frame.checksum != lin_checksum(frame.frame_id, frame.data, frame.model):
            raise ValueError("Cannot replay a LIN response with a bad checksum.")
        return ("slave", frame.frame_id, frame.data, frame.model)
