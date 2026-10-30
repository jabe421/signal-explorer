"""Interruptible, 1x replay of MDF4 bus events through a protocol codec."""
from contextlib import closing
import math
from threading import Condition, Event
import time

from PySide6.QtCore import QThread, Signal

from signal_explorer.recording.codecs import CANCodec, RecordingCodec


class MF4Playback(QThread):
    # A multi-day gap in a bench log needs inspection before any transmission.
    MAX_GAP_SECONDS = 24 * 60 * 60
    progress = Signal(str)
    failed = Signal(str)
    completed = Signal(str)
    frame_sent = Signal(int)  # One-based row in the unfiltered recording.
    paused = Signal()

    def __init__(self, path, send, fd_enabled=False, bus_channel=1, direction="all", parent=None,
                 codec: RecordingCodec | None = None, tx_options=None):
        super().__init__(parent)
        self.codec = codec or CANCodec()
        self.path = str(path)
        self.send = send
        self.fd_enabled = fd_enabled
        self.tx_options = {"fd_enabled": fd_enabled, **(tx_options or {})}
        self.bus_channel = bus_channel
        self.direction = direction
        self.cancel = Event()
        self.sent = 0
        self.max_lateness = 0.0
        self._condition = Condition()
        self._pause_at = None
        self._paused_time = 0.0
        self._pause_acknowledged = False

    def stop(self):
        with self._condition:
            self.cancel.set()
            self._condition.notify_all()

    def pause(self):
        with self._condition:
            if self._pause_at is None:
                self._pause_at = time.monotonic()
                self._pause_acknowledged = False
            self._condition.notify_all()

    def resume(self):
        with self._condition:
            if self._pause_at is not None:
                self._paused_time += time.monotonic() - self._pause_at
                self._pause_at = None
            self._condition.notify_all()

    def _wait_until(self, target):
        """Wait against the recorded clock, excluding time spent paused."""
        with self._condition:
            while not self.cancel.is_set():
                if self._pause_at is not None:
                    if not self._pause_acknowledged:
                        self._pause_acknowledged = True
                        self.paused.emit()
                    self._condition.wait()
                else:
                    remaining = target + self._paused_time - time.monotonic()
                    if remaining <= 0:
                        return True
                    # Windows has a finite platform limit for lock wait timeouts.
                    # Bound each wait while retaining the full deadline.
                    self._condition.wait(min(remaining, 60.0))
            return False

    def _selected(self, msg):
        return (msg.channel == self.bus_channel and
                (self.direction == "all" or (self.direction == "rx" and msg.direction == "rx")
                 or (self.direction == "tx" and msg.direction == "tx")))

    def _outgoing(self, msg):
        return self.codec.prepare_tx(msg, self.tx_options)

    def run(self):
        try:
            self.progress.emit("Checking recording before TX playback...")
            count = skipped = 0
            first = previous = None
            # Full preflight prevents a partial replay followed by a format/FD error.
            with closing(self.codec.read(self.path)) as reader:
                for row, msg in enumerate(reader, 1):
                    if not self._wait_until(0):
                        return
                    if not self._selected(msg):
                        continue
                    if not self.codec.replayable(msg):
                        skipped += 1
                        continue
                    if not math.isfinite(msg.timestamp) or (previous is not None and msg.timestamp < previous):
                        raise ValueError("Selected log timestamps must be finite and chronological.")
                    if previous is not None and msg.timestamp - previous > self.MAX_GAP_SECONDS:
                        gap = msg.timestamp - previous
                        raise ValueError(f"Timestamp discontinuity at logfile row {row}: {gap:,.3f}-second gap "
                                         f"({gap / 86400:,.1f} days). The log may mix adapter and PC clocks. "
                                         "Replay was cancelled before transmitting. Create a new recording with aligned timestamps.")
                    self._outgoing(msg)
                    first = msg.timestamp if first is None else first
                    previous = msg.timestamp
                    count += 1
            if not count:
                raise ValueError(f"No replayable {self.codec.protocol} events match this role, bus, direction and response IDs.")
            self.progress.emit(f"Playing {count} frames at 1x timing...")
            start = last_update = None
            with closing(self.codec.read(self.path)) as reader:
                for row, msg in enumerate(reader, 1):
                    if self.cancel.is_set():
                        return
                    if not self._selected(msg) or not self.codec.replayable(msg):
                        continue
                    frame = self._outgoing(msg)
                    # File opening/parsing must not consume the recorded gaps.
                    if start is None:
                        start = time.monotonic() - self._paused_time
                        last_update = time.monotonic()
                    target = start + (msg.timestamp - first)
                    if not self._wait_until(target):
                        return
                    self.max_lateness = max(self.max_lateness, time.monotonic() - target - self._paused_time)
                    if not self.send(frame):
                        raise RuntimeError("Adapter send failed. Check connection, bus settings and termination.")
                    self.sent += 1
                    self.frame_sent.emit(row)
                    if time.monotonic() - last_update >= 0.1:
                        self.progress.emit(f"Playback {self.tx_options.get('operation_label', 'TX')} {self.sent}/{count}")
                        last_update = time.monotonic()
            self.completed.emit(f"Playback complete: {self.sent} {self.tx_options.get('operation_label', 'TX')}, {skipped} non-transmittable events skipped; "
                                f"max scheduling delay {self.max_lateness * 1000:.1f} ms")
        except Exception as exc:
            self.failed.emit(f"Playback failed: {exc}")
