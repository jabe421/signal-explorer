"""LDF parsing and explicitly supported unconditional frame schedules."""
from pathlib import Path
import ldfparser
from ldfparser.schedule import LinFrameEntry
from ldfparser.frame import LinUnconditionalFrame

from signal_explorer.lin.model import ScheduleSlot


class LinDatabase:
    def __init__(self, path):
        self.path = str(Path(path))
        self.ldf = ldfparser.parse_ldf(self.path)
        self.frames = {f.frame_id: f for f in self.ldf.get_unconditional_frames()}
        self.baudrate = self.ldf.get_baudrate()
        self.master = self.ldf.get_master().name
        self.model = "classic" if str(self.ldf.get_protocol_version()).startswith("1.") else "enhanced"

    def decode(self, event):
        frame = self.frames.get(event.frame_id)
        if frame is None or event.kind != "frame":
            return "", ""
        if len(event.data) != frame.length:
            return frame.name, "Decode error: payload length differs from LDF"
        try:
            values = frame.decode(bytearray(event.data), keep_unit=True)
            return frame.name, "; ".join(f"{name}={value}" for name, value in values.items())
        except Exception as exc:
            return frame.name, f"Decode error: {exc}"

    def initial_data(self, identifier):
        return bytes(self.frames[identifier].encode_raw({}))

    def schedules(self):
        return [table.name for table in self.ldf.get_schedule_tables()]

    def schedule(self, name):
        slots = []
        for entry in self.ldf.get_schedule_table(name).schedule:
            if not isinstance(entry, LinFrameEntry) or not isinstance(entry.frame, LinUnconditionalFrame):
                raise ValueError("This release supports unconditional-frame schedule entries only.")
            slots.append(ScheduleSlot(entry.frame.frame_id, entry.delay * 1000))
        return slots
