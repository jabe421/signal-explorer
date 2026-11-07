"""Interruptible, 1x replay of MDF4 bus events through a protocol codec."""
from contextlib import closing
import math
import logging
import sys
from threading import Condition, Event, Lock
import time

from PySide6.QtCore import QThread, Signal

from signal_explorer.recording.codecs import CANCodec, RecordingCodec
from signal_explorer.recording.replay_timing import MODES, TimingSamples

logger = logging.getLogger(__name__)


class MF4Playback(QThread):
    # A multi-day gap in a bench log needs inspection before any transmission.
    MAX_GAP_SECONDS = 24 * 60 * 60
    progress = Signal(str)
    failed = Signal(str)
    completed = Signal(str)
    frame_sent = Signal(int)  # One-based row in the unfiltered recording.
    paused = Signal()

    def __init__(self, path, send, fd_enabled=False, bus_channel=1, direction="all", parent=None,
                 codec: RecordingCodec | None = None, tx_options=None, timing_mode='balanced', wait_backend='auto'):
        super().__init__(parent)
        if timing_mode not in MODES:
            raise ValueError('Replay timing mode must be balanced or precision.')
        self.timing_mode = timing_mode
        self._spin_seconds = MODES[timing_mode]
        if wait_backend not in ('auto', 'condition'):
            raise ValueError('Replay wait backend must be auto or condition.')
        self._requested_wait_backend = wait_backend
        self._native_wait = None
        self._wait_backend = 'Python condition'
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
        self._metrics_lock = Lock()
        self._scheduler = TimingSamples()
        self._send_duration = TimingSamples()
        self._return_lateness = TimingSamples()
        self._timing_state = 'idle'
        self._replay_start = self._replay_end = self._cpu_start = None
        self._worker_cpu = 0.0

    def timing_snapshot(self):
        with self._metrics_lock:
            end = self._replay_end if self._replay_end is not None else time.perf_counter()
            raw = (self._scheduler.raw_snapshot(), self._send_duration.raw_snapshot(), self._return_lateness.raw_snapshot())
            report = dict(mode=self.timing_mode, state=self._timing_state, sent=self.sent,
                        final_spin_ms=self._spin_seconds*1000,
                        wait_backend=self._wait_backend,
                        elapsed_wall_seconds=max(0, end-self._replay_start) if self._replay_start is not None else 0,
                        worker_cpu_ms=self._worker_cpu*1000,
                        semantics='Host scheduling and send-callback return, not wire timing or ECU delivery. Replay-phase CPU/wall exclude preflight. Wall time includes pauses. OS CPU accounting may be quantized. Percentiles use the most recent bounded sample window.')
        # Sorting must not hold the replay thread's metrics lock.
        report.update(zip(('scheduler_lateness', 'send_duration', 'send_return_lateness'),
                          (TimingSamples.summarize(samples) for samples in raw)))
        return report

    def timing_summary(self):
        report = self.timing_snapshot()
        if not report['scheduler_lateness']['count']:
            return 'No replay send attempts measured.'
        return (f"{self.timing_mode}; scheduler p95 {report['scheduler_lateness']['p95_ms']:.3f} ms, "
                f"max {report['scheduler_lateness']['max_ms']:.3f} ms; "
                f"send-call max {report['send_duration']['max_ms']:.3f} ms; "
                f"send-return lateness max {report['send_return_lateness']['max_ms']:.3f} ms")

    def _set_timing_state(self, state):
        with self._metrics_lock:
            self._timing_state = state

    def stop(self):
        with self._condition:
            self.cancel.set()
            self._condition.notify_all()
        self._wake_wait()

    def pause(self):
        with self._condition:
            if self._pause_at is None:
                self._pause_at = time.perf_counter()
                self._pause_acknowledged = False
            self._condition.notify_all()
        self._wake_wait()

    def resume(self):
        with self._condition:
            if self._pause_at is not None:
                self._paused_time += time.perf_counter() - self._pause_at
                self._pause_at = None
            self._condition.notify_all()
        self._wake_wait()

    def _wake_wait(self):
        waiter = self._native_wait
        if waiter is not None:
            try:
                waiter.wake()
            except OSError:
                logger.exception('Could not wake the replay timer')

    def _wait_until(self, target):
        """Wait against the recorded clock, excluding time spent paused."""
        while True:
            native_deadline = None
            with self._condition:
                if self.cancel.is_set():
                    return False
                if self._pause_at is not None:
                    if not self._pause_acknowledged:
                        self._pause_acknowledged = True
                        self._set_timing_state('paused')
                        self.paused.emit()
                    self._condition.wait()
                    if self._pause_at is None:
                        self._set_timing_state('preflight' if self._replay_start is None else 'running')
                    continue
                else:
                    remaining = target + self._paused_time - time.perf_counter()
                    if remaining <= 0:
                        return True
                    # Windows has a finite platform limit for lock wait timeouts.
                    # Bound each wait while retaining the full deadline.
                    coarse_wait = remaining-self._spin_seconds
                    if coarse_wait > 0:
                        if self._native_wait is None:
                            self._condition.wait(min(coarse_wait, 60.0))
                        else:
                            native_deadline = min(target+self._paused_time-self._spin_seconds,
                                                  time.perf_counter()+60.0)
            if native_deadline is not None:
                try:
                    self._native_wait.wait_until(native_deadline)
                except OSError:
                    logger.exception('High-resolution replay wait failed; using condition fallback')
                    self._native_wait.close()
                    self._native_wait = None
                    self._wait_backend = 'Python condition (native wait failed)'
            # Precision mode retries the final <= 0.5 ms outside the condition
            # lock so pause/stop can acquire it. Never spin while paused.

    def _selected(self, msg):
        return (msg.channel == self.bus_channel and
                (self.direction == "all" or (self.direction == "rx" and msg.direction == "rx")
                 or (self.direction == "tx" and msg.direction == "tx")))

    def _outgoing(self, msg):
        return self.codec.prepare_tx(msg, self.tx_options)

    def run(self):
        outcome = 'stopped'
        try:
            if sys.platform == 'win32' and self._requested_wait_backend == 'auto':
                try:
                    from signal_explorer.recording.windows_wait import WindowsReplayWait
                    self._native_wait = WindowsReplayWait()
                    self._wait_backend = 'Windows high-resolution waitable timer'
                except (OSError, AttributeError):
                    logger.warning('High-resolution replay timer unavailable; using condition fallback', exc_info=True)
                    self._wait_backend = 'Python condition (native timer unavailable)'
            self._set_timing_state('preflight')
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
            self.progress.emit(f"Playing {count} frames at 1x timing ({self.timing_mode})...")
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
                        with self._metrics_lock:
                            self._replay_start = time.perf_counter()
                            self._cpu_start = time.thread_time()
                        start = self._replay_start - self._paused_time
                        last_update = time.perf_counter()
                    target = start + (msg.timestamp - first)
                    while True:
                        if not self._wait_until(target):
                            return
                        with self._condition:
                            if self.cancel.is_set():
                                return
                            if self._pause_at is not None:
                                continue
                            due = target + self._paused_time
                            break
                    self._set_timing_state('sending')
                    began = time.perf_counter()
                    try:
                        accepted = self.send(frame)
                    finally:
                        ended = time.perf_counter()
                        with self._metrics_lock:
                            self._scheduler.add(began-due)
                            self._send_duration.add(ended-began)
                            self._return_lateness.add(ended-due)
                            self.max_lateness = self._scheduler.maximum
                            self._worker_cpu = time.thread_time()-self._cpu_start
                    if not accepted:
                        raise RuntimeError("Adapter send failed. Check connection, bus settings and termination.")
                    with self._metrics_lock:
                        self.sent += 1
                        self._timing_state = 'running'
                    self.frame_sent.emit(row)
                    if time.perf_counter() - last_update >= 0.1:
                        self.progress.emit(f"Playback {self.tx_options.get('operation_label', 'TX')} {self.sent}/{count}")
                        last_update = time.perf_counter()
            outcome = 'completed'
            self.completed.emit(f"Playback complete: {self.sent} {self.tx_options.get('operation_label', 'TX')}, {skipped} non-transmittable events skipped; "
                                + self.timing_summary())
        except Exception as exc:
            outcome = 'failed'
            logger.exception('Recording playback failed')
            self.failed.emit(f"Playback failed: {exc}")
        finally:
            if self._native_wait is not None:
                self._native_wait.close()
                self._native_wait = None
            with self._metrics_lock:
                self._timing_state = outcome
                self._replay_end = time.perf_counter()
                if self._cpu_start is not None:
                    self._worker_cpu = time.thread_time()-self._cpu_start
            logger.info('Replay timing: %s', self.timing_snapshot())
