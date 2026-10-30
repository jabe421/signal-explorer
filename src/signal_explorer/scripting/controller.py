"""Qt host for a single script process, independent from editor/protocol UI."""
from collections import deque
import json
import math
from pathlib import Path
from queue import Queue, Empty, Full
import sys
from threading import RLock
import time

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, QTimer, Qt, Signal


class ScriptController(QObject):
    changed = Signal()
    output = Signal(str)
    line_changed = Signal(int)
    finished = Signal(str)
    MAX_RECEIVE = 2000

    def __init__(self, transport, parent=None):
        super().__init__(parent)
        self.transport, self.owner = transport, None
        self.process = None
        self.state = 'idle'; self.outcome = None; self.line = 0
        self._lock = RLock(); self._accepting = False
        self._events = Queue(maxsize=self.MAX_RECEIVE)
        self._overflow = False
        self._pending = None; self._deferred = deque()
        self._buffer = b''; self._result = None; self._failure = None
        self._pause_at = None; self._want_pause = False
        self._timer = QTimer(self); self._timer.setInterval(20); self._timer.timeout.connect(self._tick)
        self._kill_timer = QTimer(self); self._kill_timer.setSingleShot(True)
        self._kill_timer.timeout.connect(self._kill)
        transport.traffic.connect(self._capture, Qt.ConnectionType.DirectConnection)
        transport.about_to_disconnect.connect(self._disconnected)

    @property
    def active(self): return self.process is not None

    def _set_state(self, state):
        self.state = state; self.changed.emit()

    def start(self, source, filename='<Signal Explorer script>', owner=None, delay_ms=0):
        if self.active: raise RuntimeError('Another script is running. Stop it before starting a new script.')
        if not self.transport.is_connected: raise RuntimeError('Connect the '+self.transport.protocol+' session before running a script.')
        self.owner = owner; self.outcome = None; self.line = 0
        self._pending = None; self._deferred.clear(); self._buffer = b''
        self._result = self._failure = None
        self._want_pause = False; self._pause_at = None
        with self._lock:
            self._events = Queue(maxsize=self.MAX_RECEIVE)
            self._overflow = False; self._accepting = True
        process = self.process = QProcess(self)
        environment = QProcessEnvironment.systemEnvironment()
        environment.insert('PYTHONIOENCODING', 'utf-8')
        environment.insert('PYTHONUNBUFFERED', '1')
        process.setProcessEnvironment(environment)
        if not filename.startswith('<'): process.setWorkingDirectory(str(Path(filename).parent))
        payload = dict(type='start', source=source, filename=filename,
                       session=self.transport.session(), delay_ms=int(delay_ms))
        process.started.connect(lambda: self._boot(process,payload))
        process.readyReadStandardOutput.connect(lambda: self._read(process))
        process.readyReadStandardError.connect(lambda: self._stderr(process))
        process.finished.connect(lambda code, status: self._ended(process, code, status))
        process.errorOccurred.connect(lambda error: self._process_error(process, error))
        self._set_state('starting'); self._timer.start()
        process.start(sys.executable, ['-u', '-m', 'signal_explorer.scripting.worker'])

    def _write(self, message):
        if self.process and self.process.state() != QProcess.ProcessState.NotRunning:
            if self.process.state()==QProcess.ProcessState.Starting and message.get('type')!='start':return
            self.process.write((json.dumps(message)+'\n').encode('utf-8'))

    def _boot(self,process,payload):
        if self.process is not process:return
        self._write(payload)
        if self.state=='stopping':self._write(dict(type='stop'))
        elif self._want_pause:self._write(dict(type='pause'))

    def pause(self):
        if not self.active or self.state == 'stopping': return
        self._want_pause = True
        if self._pause_at is None: self._pause_at = time.monotonic()
        self._write(dict(type='pause')); self._set_state('pausing')

    def resume(self):
        if not self.active or self.state == 'stopping': return
        self._want_pause = False
        if self._pause_at is not None and self._pending and self._pending['deadline'] is not None:
            self._pending['deadline'] += time.monotonic() - self._pause_at
        self._pause_at = None
        self._write(dict(type='resume')); self._set_state('running')

    def stop(self, reason='Stopped by user.'):
        if not self.active or self.state == 'stopping': return
        self.output.emit(reason+'\n')
        with self._lock: self._accepting = False
        self._pending = None; self._deferred.clear()
        self._write(dict(type='stop')); self._set_state('stopping')
        self._kill_timer.start(1000)

    def _kill(self):
        if self.process:
            self.output.emit('Script process terminated; it did not stop cooperatively.\n')
            self.process.kill()

    def _disconnected(self):
        self.stop(self.transport.protocol+' disconnected; script stopped. Reconnect and Run to start a new session.')

    def _capture(self, event):
        # Runs on the service's RX/TX thread. Never use a widget/QProcess here.
        with self._lock:
            if not self._accepting: return
            try: self._events.put_nowait(self.transport.encode(event))
            except Full: self._overflow = True

    def _tick(self):
        if not self.active or self.state == 'stopping': return
        if not self.transport.is_connected:
            self._disconnected(); return
        with self._lock: overflow = self._overflow
        if overflow:
            self._failure = 'Script receive queue overflow. Test stopped; monitoring/logging use independent paths.'
            self.stop(self._failure); return
        if self._want_pause: return
        while self._deferred and self.state != 'stopping': self._request(self._deferred.popleft())
        if self._pending:
            request = self._pending
            # Bounded work per tick preserves UI responsiveness under load.
            for _ in range(self.MAX_RECEIVE):
                try: event = self._events.get_nowait()
                except Empty: break
                if not self.transport.matches(event, request['parameters']):continue
                self._reply(request['id'], event); self._pending = None
                break
            if self._pending and request['deadline'] is not None and time.monotonic() >= request['deadline']:
                self._reply(request['id'], None); self._pending = None

    def _reply(self, identifier, result=None, error=None):
        reply = dict(type='response', id=identifier)
        reply.update(error=str(error)) if error is not None else reply.update(result=result)
        self._write(reply)

    def _request(self, event):
        if self.state == 'stopping' or self.process is None or self.process.state()==QProcess.ProcessState.NotRunning:return
        if self._want_pause:
            self._deferred.append(event); return
        try:
            if not self.transport.is_connected: raise RuntimeError(self.transport.protocol+' session is disconnected.')
            method, parameters = event['method'], event['parameters']
            if method == 'send':
                self._reply(event['id'], self.transport.send(parameters))
            elif method == 'recv':
                if self._pending: raise RuntimeError('Only one outstanding receive is supported per script.')
                timeout = parameters.get('timeout')
                if timeout is not None and (not math.isfinite(timeout) or timeout < 0):
                    raise ValueError('Invalid receive timeout.')
                self._pending = dict(id=event['id'], parameters=parameters,
                                     deadline=time.monotonic()+timeout if timeout is not None else None)
                self._tick()
            else: raise ValueError('Unsupported script operation: '+method)
        except Exception as exc: self._reply(event['id'], error=exc)

    def _read(self, process):
        if process is not self.process: return
        self._buffer += bytes(process.readAllStandardOutput())
        if len(self._buffer) > 1_000_000:
            self._buffer = b''; self._failure = 'Script output overflow.'; self.stop(self._failure); return
        for _ in range(200):
            if b'\n' not in self._buffer: break
            raw, self._buffer = self._buffer.split(b'\n', 1)
            try: event = json.loads(raw)
            except Exception:
                self.output.emit(raw.decode('utf-8', errors='replace')+'\n'); continue
            kind = event.get('event')
            if kind == 'request': self._request(event)
            elif kind == 'started':
                if self.state != 'stopping':
                    self._set_state('pausing' if self._want_pause else 'running')
            elif kind == 'paused' and self._want_pause and self.state != 'stopping':
                self.line = event.get('line', self.line); self.line_changed.emit(self.line)
                self._set_state('paused')
            elif kind == 'resumed' and not self._want_pause and self.state != 'stopping': self._set_state('running')
            elif kind == 'line' and self.state != 'stopping':
                self.line = event['line']; self.line_changed.emit(self.line)
            elif kind == 'output': self.output.emit(event.get('text', ''))
            elif kind == 'error':
                self.line = event.get('line', 0); self.line_changed.emit(self.line)
                self.output.emit(event.get('text', 'Script error.'))
            elif kind == 'result': self._result = event
        if b'\n' in self._buffer: QTimer.singleShot(0, lambda: self._read(process))

    def _stderr(self, process):
        text = bytes(process.readAllStandardError()).decode('utf-8', errors='replace')
        if process is self.process: self.output.emit(text[:16000])

    def _process_error(self, process, error):
        if process is self.process and error == QProcess.ProcessError.FailedToStart:
            self._failure = 'Could not start Python script process: '+process.errorString()
            self.output.emit(self._failure+'\n'); self._ended(process, -1, None)

    def _ended(self, process, code, status):
        if process is not self.process: return
        # Drain final output before deciding the verdict (including large bursts).
        self._read(process)
        while b'\n' in self._buffer: self._read(process)
        self._stderr(process)
        was_stopping = self.state == 'stopping'
        self._timer.stop(); self._kill_timer.stop()
        with self._lock: self._accepting = False
        result = self._result or {}
        self.outcome = ('failed' if self._failure else 'stopped' if was_stopping else
                        result.get('outcome', 'failed'))
        if code != 0 and not was_stopping: self.outcome = 'failed'
        if not result and not was_stopping and not self._failure:
            self.output.emit(f'Script process exited without a result (exit code {code}).\n')
        self._pending = None; self._deferred.clear()
        self.process = None; process.deleteLater()
        self.output.emit(f"{self.outcome.upper()} ({result.get('checks', 0)} checks)\n")
        self._set_state(self.outcome); self.finished.emit(self.outcome)
