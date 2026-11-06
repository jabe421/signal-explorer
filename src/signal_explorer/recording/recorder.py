"""Raw MDF 4.10 bus logging using python-can/asammdf, off the GUI thread."""
from dataclasses import replace
import math
import heapq
import os
from pathlib import Path
from queue import Empty, Full, Queue
import shutil
import tempfile
from threading import Event, RLock, Thread
import time

from PySide6.QtCore import QObject, Signal
from signal_explorer.recording.codecs import BusEvent, CANCodec, RecordingCodec
from signal_explorer.bus.timestamps import SessionClock


class MF4Recorder(QObject):
    changed = Signal()
    finalized = Signal(str, str)  # temporary file, warning/error

    def __init__(self, parent=None, queue_size=100_000, temp_directory=None, codec: RecordingCodec | None = None):
        super().__init__(parent)
        self.codec = codec or CANCodec()
        self.queue_size = queue_size
        self.temp_directory = temp_directory
        self._lock = RLock()
        self._thread = None
        self._accepting = False
        self._stopping = Event()
        self._done = Event()
        self._done.set()
        self.pending_path = None
        self.count = 0
        self.problem = ""
        self._queue = Queue(maxsize=queue_size)

    @property
    def recording(self):
        with self._lock:
            return self._accepting

    @property
    def busy(self):
        return not self._done.is_set()

    def start(self, metadata=None, database=None):
        if self.busy or self.pending_path:
            raise RuntimeError("Save the previous recording before starting another log.")
        directory = Path(self.temp_directory or Path(tempfile.gettempdir()) / "signal-explorer-recordings")
        directory.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(prefix="SignalExplorer-", suffix=".mf4", dir=directory, delete=False) as file:
            path = Path(file.name)
        self._queue = Queue(maxsize=self.queue_size)
        self._stopping.clear()
        self._done.clear()
        self.count = 0
        self.problem = ""
        self.pending_path = None
        with self._lock:
            self._accepting = True
        self._thread = Thread(target=self._write, args=(path, dict(metadata or {}), database),
                              name="SignalExplorer-MF4", daemon=False)
        try:
            self._thread.start()
        except Exception:
            with self._lock:
                self._accepting = False
            self._done.set()
            path.unlink(missing_ok=True)
            raise
        self.changed.emit()

    def capture(self, msg):
        # Called directly from RX/TX threads; no widgets or disk I/O here.
        with self._lock:
            if not self._accepting:
                return
            try:
                self._queue.put_nowait(msg if isinstance(msg, BusEvent) else self.codec.capture(msg))
            except Full:
                self.problem = "Logging stopped: recording queue overflow. The saved recording is incomplete."
                self._accepting = False
                self._stopping.set()
                self.changed.emit()

    def stop(self):
        with self._lock:
            self._accepting = False
            self._stopping.set()
        self.changed.emit()

    def _write(self, path, metadata, database):
        writer = None
        finalized = False
        corrections = 0
        reasons = {'invalid': 0, 'late_out_of_order': 0}
        samples = []
        reordered = 0
        window = .05
        held = []
        max_seen = last_timestamp = 0.0
        sequence = 0
        clock = SessionClock()

        def corrected(msg, value, reason):
            nonlocal corrections
            corrections += 1
            reasons[reason] += 1
            if len(samples) < 100:
                original = msg.timestamp
                if not isinstance(original, (int, float)) or not math.isfinite(original):
                    original = repr(original)
                native = getattr(msg.payload, 'raw_timestamp', None)
                if native is not None and (not isinstance(native, (int, float)) or not math.isfinite(native)):
                    native = repr(native)
                samples.append(dict(frame=self.count+len(held)+1, original=original, corrected=value,
                                    native_timestamp=native,
                                    channel=str(msg.channel), direction=msg.direction, reason=reason))
            return replace(msg, timestamp=value)

        def write_next():
            nonlocal last_timestamp, last_progress
            _, _, msg = heapq.heappop(held)
            if msg.timestamp < last_timestamp:
                msg = corrected(msg, last_timestamp, 'late_out_of_order')
            last_timestamp = msg.timestamp
            writer.write(msg)
            self.count += 1
            if time.monotonic()-last_progress > .1:
                self.changed.emit()
                last_progress = time.monotonic()

        try:
            writer = self.codec.writer(path, database)
            if hasattr(writer, 'configure_channels'):
                writer.configure_channels(metadata.get('channels', []))
            last_progress = time.monotonic()
            while not self._stopping.is_set() or not self._queue.empty():
                try:
                    msg = self._queue.get(timeout=0.05)
                except Empty:
                    while held:
                        write_next()
                    continue
                try:
                    valid_timestamp = math.isfinite(msg.timestamp) and msg.timestamp > 0
                except (TypeError, ValueError):
                    valid_timestamp = False
                if not valid_timestamp:
                    msg = corrected(msg, max(max_seen, last_timestamp, clock.now()), 'invalid')
                elif msg.timestamp < max(max_seen-window, last_timestamp):
                    msg = corrected(msg, max(max_seen, last_timestamp), 'late_out_of_order')
                elif msg.timestamp < max_seen:
                    reordered += 1
                max_seen = max(max_seen, msg.timestamp)
                heapq.heappush(held, (msg.timestamp, sequence, msg))
                sequence += 1
                while held and (held[0][0] <= max_seen-window or len(held) >= min(8192, self.queue_size)):
                    write_next()
            while held:
                write_next()
        except Exception as exc:
            self.problem = f"Logging failed: {exc}. The recording may be incomplete."
        finally:
            with self._lock:
                self._accepting = False
                self._stopping.set()
            if writer is not None:
                try:
                    metadata.update(format="ASAM MDF 4.10 bus logging", protocol=self.codec.protocol, bus_channel=1,
                                    timestamp_corrections=corrections, recorded_frames=self.count,
                                    incomplete_reason=self.problem,
                                    timestamp_reorder_window_seconds=window,
                                    timestamp_reordered_frames=reordered,
                                    timestamp_correction_reasons=reasons,
                                    timestamp_correction_samples=samples,
                                    timestamp_correction_samples_truncated=corrections > len(samples))
                    metadata.setdefault('tx_semantics', 'Local send accepted by adapter; not a delivery acknowledgement')
                    writer.finish(metadata)
                    finalized = True
                except Exception as exc:
                    self.problem += f" Could not finalize MDF4: {exc}. Temporary file: {path}"
                    try:
                        writer.abort()
                    except Exception:
                        pass
            if finalized:
                self.pending_path = path
            self._done.set()
            self.changed.emit()
            self.finalized.emit(str(path) if finalized else "", self.problem)

    def save_as(self, destination):
        """Atomic destination replacement; keep the original if copying fails."""
        if self.busy or self.pending_path is None:
            raise RuntimeError("No finalized recording is ready to save.")
        destination = Path(destination)
        if destination.suffix.lower() != ".mf4":
            destination = Path(str(destination) + ".mf4")
        if destination.resolve() == self.pending_path.resolve():
            self.pending_path = None
            self.changed.emit()
            return destination
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(prefix=".signal-explorer-", suffix=".tmp",
                                             dir=destination.parent, delete=False) as file:
                temporary = Path(file.name)
                with self.pending_path.open("rb") as source:
                    shutil.copyfileobj(source, file)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, destination)
        finally:
            if temporary and temporary.exists():
                temporary.unlink()
        self.pending_path.unlink()
        self.pending_path = None
        self.changed.emit()
        return destination
