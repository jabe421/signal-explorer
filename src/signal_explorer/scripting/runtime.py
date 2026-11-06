"""Child-process API; no Qt, hardware handles or native drivers here."""
from dataclasses import dataclass
import json
import math
from pathlib import Path
import sys
from threading import Condition, Lock, Thread, TIMEOUT_MAX
import time


class ScriptStopped(BaseException):
    pass


class EventWriter:
    def __init__(self, stream):
        self.stream, self.lock = stream, Lock()

    def emit(self, event, **data):
        with self.lock:
            self.stream.write(json.dumps(dict(event=event, **data), ensure_ascii=True) + '\n')
            self.stream.flush()


class ScriptOutput:
    encoding = 'utf-8'

    def __init__(self, writer, stream='stdout'):
        self.writer, self.stream = writer, stream

    def write(self, value):
        value = str(value)
        for offset in range(0, len(value), 4096):
            self.writer.emit('output', text=value[offset:offset+4096], stream=self.stream)
        return len(value)

    def flush(self): pass
    def isatty(self): return False


class ExecutionControl:
    def __init__(self, writer, input_stream):
        self.writer, self.input = writer, input_stream
        self.condition = Condition()
        self.paused = self.stopped = False
        self.pause_started = None
        self.pause_total = 0.0
        self.line = 0
        self.error = None
        self.responses = {}
        self.serial = 0
        self._pause_reported = False
        Thread(target=self._read, name='ScriptControl', daemon=True).start()

    def _read(self):
        try:
            for line in self.input:
                command = json.loads(line)
                with self.condition:
                    kind = command.get('type')
                    if kind == 'response':
                        if command['id'] in self.responses:
                            self.responses[command['id']] = command
                    elif kind == 'pause' and not self.paused:
                        self.paused = True; self.pause_started = time.monotonic()
                    elif kind == 'resume' and self.paused:
                        self.pause_total += time.monotonic() - self.pause_started
                        self.pause_started = None; self.paused = False
                    elif kind == 'stop': self.stopped = True
                    elif kind == 'abort': self.error = command.get('message', 'Script transport failed.')
                    self.condition.notify_all()
        except Exception as exc:
            with self.condition:
                self.error = f'Script control connection failed: {exc}'
        finally:
            with self.condition:
                self.stopped = True; self.condition.notify_all()

    def clock(self):
        with self.condition:
            now = time.monotonic()
            return now - self.pause_total - (now-self.pause_started if self.paused else 0)

    def checkpoint(self):
        with self.condition:
            while True:
                if self.error: raise RuntimeError(self.error)
                if self.stopped: raise ScriptStopped()
                if not self.paused:
                    if self._pause_reported:
                        self.writer.emit('resumed'); self._pause_reported = False
                    return
                if not self._pause_reported:
                    self.writer.emit('paused', line=self.line); self._pause_reported = True
                self.condition.wait()

    def sleep(self, seconds):
        if not math.isfinite(seconds) or seconds < 0:
            raise ValueError('Sleep duration must be finite and nonnegative.')
        deadline = self.clock() + seconds
        while True:
            self.checkpoint()
            remaining = deadline - self.clock()
            if remaining <= 0: return
            with self.condition:
                if not (self.paused or self.stopped or self.error):
                    remaining = deadline-self.clock()
                    if remaining > 0:
                        self.condition.wait(min(TIMEOUT_MAX, remaining))

    def rpc(self, method, parameters):
        self.checkpoint()
        self.writer.emit('line', line=self.line)
        with self.condition:
            self.serial += 1; identifier = self.serial
            self.responses[identifier] = None
        self.writer.emit('request', id=identifier, method=method, parameters=parameters)
        try:
            while True:
                self.checkpoint()
                with self.condition:
                    response = self.responses[identifier]
                    if response is None:
                        # Check under the same lock before waiting, so a Pause,
                        # Stop or response cannot be lost between checks.
                        if not (self.paused or self.stopped or self.error):
                            self.condition.wait()
                        continue
                self.checkpoint()
                if 'error' in response: raise RuntimeError(response['error'])
                return response.get('result')
        finally:
            with self.condition: self.responses.pop(identifier, None)


@dataclass(frozen=True)
class CanFrame:
    arbitration_id: int
    data: bytes
    timestamp: float
    channel: str
    direction: str
    is_extended_id: bool = False
    is_fd: bool = False
    bitrate_switch: bool = False
    is_error_frame: bool = False
    is_remote_frame: bool = False


class CanClient:
    def __init__(self, control): self.control = control

    def send(self, arbitration_id, data=b'', *, extended=False, fd=False, brs=False, channel=None):
        """Send once; return the local TX acceptance timestamp (Unix seconds)."""
        return self.control.rpc('send', dict(arbitration_id=arbitration_id, data=bytes(data).hex(),
                                             extended=extended, fd=fd, brs=brs, channel=channel))

    def recv(self, timeout=1.0, *, arbitration_id=None, extended=None, direction='rx', channel=None):
        """Return a matching frame or None at timeout. None timeout waits until stopped.

        Unmatched events remain available in this script's bounded mailbox.
        Display filters and monitor pause do not affect it. Timeout excludes pause.
        """
        if timeout is not None and (not math.isfinite(timeout) or timeout < 0):
            raise ValueError('Receive timeout must be nonnegative, finite, or None.')
        if direction not in ('rx', 'tx', 'all'): raise ValueError('Direction must be rx, tx or all.')
        result = self.control.rpc('recv', dict(timeout=timeout, arbitration_id=arbitration_id,
                                               extended=extended, direction=direction, channel=channel))
        if result is None: return None
        result = dict(result); result.pop('protocol', None); result['data'] = bytes.fromhex(result['data'])
        return CanFrame(**result)

    def stats(self):
        """Return cumulative run counters and the current receive-buffer size."""
        return self.control.rpc('stats', {})

    def flush(self):
        """Discard currently buffered frames; return their count, retaining loss counters."""
        return self.control.rpc('flush', {})

    def set_filters(self, filters=None):
        """Set ORed ID/mask capture filters; return buffered frames pruned.

        Each dict has can_id, can_mask, and optional extended, direction, channel.
        None/[] restores all eligible CAN data frames. Only this script is affected.
        """
        return self.control.rpc('set_filters', dict(filters=filters))


class ScriptContext:
    def __init__(self, control, session):
        self.control, self.session = control, dict(session)
        self.protocol = session['protocol']
        self.is_simulated = bool(session.get('simulated'))
        self.checks = 0
        self.failures = 0
        self.failed_line = 0
        if self.protocol != 'CAN': raise ValueError('Only CAN scripting is enabled in this release.')
        self.can = CanClient(control)

    def sleep(self, milliseconds):
        """Interruptible, pause-aware wait. Use this instead of time.sleep()."""
        self.control.writer.emit('line', line=self.control.line)
        self.control.sleep(float(milliseconds)/1000)

    def log(self, message):
        self.control.checkpoint()
        self.control.writer.emit('output', text=str(message)+'\n', stream='log')

    def check(self, condition, description):
        self.control.checkpoint(); self.checks += 1
        self.control.writer.emit('output', text=f"{'PASS' if condition else 'FAIL'}: {description}\n", stream='check')
        if not condition:
            self.failures += 1; self.failed_line = self.control.line
            raise AssertionError(description)


def line_tracer(control, filename, delay_ms=0):
    """Trace the editor source and nearby helpers; avoid tracing framework code."""
    directory = str(Path(filename).parent) if not filename.startswith('<') else None
    runtime_directory = str(Path(__file__).parent)
    last_update = 0.0

    def trace(frame, event, arg):
        nonlocal last_update
        name = frame.f_code.co_filename
        primary = name == filename
        if not primary:
            if not directory or not name.startswith(directory+os_sep) or name.startswith(runtime_directory+os_sep):
                return None
        if event == 'line':
            if primary: control.line = frame.f_lineno
            control.checkpoint()
            now = time.monotonic()
            if primary and (delay_ms or now-last_update >= .04):
                control.writer.emit('line', line=frame.f_lineno); last_update = now
            if primary and delay_ms: control.sleep(delay_ms/1000)
        return trace

    import os
    os_sep = os.sep
    return trace
