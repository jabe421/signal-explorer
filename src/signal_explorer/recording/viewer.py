"""Background log loading and editor-compatible replay lifecycle.

The recording panel retains ownership of playback, keeping its disconnect,
repeat-send and shutdown coordination shared with the file viewer.
"""
from contextlib import closing
from pathlib import Path
from threading import Lock

from asammdf import MDF
from PySide6.QtCore import QObject, QThread, QTimer, Qt, Signal

from signal_explorer.recording.codecs import CANCodec
from signal_explorer.lin.codec import LINCodec
from signal_explorer.ethernet.codec import EthernetCodec


def format_event(event):
    frame = event.payload
    prefix = f'{event.timestamp:.6f}  Bus {event.channel}  {event.direction.upper():2}'
    if event.protocol == 'CAN':
        flags = '/'.join(name for name, enabled in (
            ('EXT', frame.is_extended_id), ('FD', frame.is_fd),
            ('BRS', frame.bitrate_switch), ('RTR', frame.is_remote_frame),
            ('ERROR', frame.is_error_frame)) if enabled) or 'STD'
        return f'{prefix}  {frame.arbitration_id:03X}  {flags:10}  DLC {frame.dlc:2}  {bytes(frame.data).hex(" ").upper()}'
    if event.protocol == 'LIN':
        return f'{prefix}  ID {frame.frame_id:02X}  {frame.kind}  {frame.model}  {frame.data.hex(" ").upper()}'
    return f'{prefix}  {len(frame.raw)} bytes  {frame.raw.hex(" ").upper()}'


class LogLoader(QThread):
    loaded = Signal(object)
    failed = Signal(str)
    MAX_ROWS = 100_000
    MAX_TEXT = 32 * 1024 * 1024

    def __init__(self, path, parent=None):
        super().__init__(parent)
        self.path = str(path)

    def run(self):
        try:
            with MDF(self.path) as mdf:
                names = set(mdf.channels_db)
            codecs = [codec for codec, keys in (
                (CANCodec(), {'CAN_DataFrame', 'CAN_ErrorFrame', 'CAN_RemoteFrame'}),
                (LINCodec(), {'LIN_Frame', 'SignalExplorer_LINEvent'}),
                (EthernetCodec(), {'SignalExplorer_EthernetFrame'})) if names & keys]
            if len(codecs) != 1:
                raise ValueError('Open a single-protocol CAN, LIN or Signal Explorer Ethernet MDF4 bus log.')
            codec = codecs[0]
            lines = []; buses = set(); count = size = 0
            with closing(codec.read(self.path)) as reader:
                for count, event in enumerate(reader, 1):
                    if self.isInterruptionRequested():
                        return
                    buses.add(event.channel)
                    if len(lines) < self.MAX_ROWS and size < self.MAX_TEXT:
                        line = format_event(event)
                        lines.append(line); size += len(line)
            if not count:
                raise ValueError('The log contains no supported bus events.')
            if not self.isInterruptionRequested():
                self.loaded.emit(dict(path=self.path, protocol=codec.protocol, text='\n'.join(lines),
                                      rows=len(lines), count=count, buses=sorted(buses)))
        except Exception as exc:
            self.failed.emit(f'Open logfile failed: {exc}')


class ReplayTransport(QObject):
    connection_changed = Signal()

    def __init__(self, panels, parent=None):
        super().__init__(parent)
        self.panels = panels
        self.protocol = 'CAN'
        for panel in panels.values():
            panel.service.connected.connect(self.connection_changed)
            panel.service.disconnected.connect(self.connection_changed)
            panel.playback_changed.connect(self.connection_changed)

    @property
    def panel(self):
        return self.panels[self.protocol]

    @property
    def is_connected(self):
        service = self.panel.service
        return service.is_connected and getattr(service, 'can_replay', True)

    def session(self):
        return self.panel.service.session_info


class LogReplayController(QObject):
    """The same window control contract as scripts, backed by timed MDF replay."""
    changed = Signal()
    output = Signal(str)
    line_changed = Signal(int)
    finished = Signal(str)
    loaded = Signal(object)

    def __init__(self, panels, parent=None):
        super().__init__(parent)
        self.transport = ReplayTransport(panels, self)
        self.owner = None
        self.state = 'idle'; self.outcome = None; self.line = 0
        self.loader = self.worker = None
        self.loaded_path = None
        self._lock = Lock(); self._latest_line = 0
        self._display = QTimer(self)
        self._display.timeout.connect(self._display_line)

    @property
    def active(self):
        return self.loader is not None or self.worker is not None

    def _set_state(self, state):
        self.state = state
        self.changed.emit()

    def load(self, path, owner):
        if self.active:
            raise RuntimeError('Stop the current operation before opening another log.')
        self.owner = owner; self.loaded_path = None; self.line = 0; self.outcome = None
        loader = self.loader = LogLoader(Path(path).resolve(), self)
        loader.loaded.connect(self._loaded)
        loader.failed.connect(self._failed)
        loader.finished.connect(self._load_finished)
        self._set_state('loading')
        loader.start()

    def _loaded(self, result):
        if self.state == 'stopping':
            return
        self.loaded_path = result['path']
        self.transport.protocol = result['protocol']
        self.loaded.emit(result)
        self.output.emit(f"Opened {result['protocol']} log: {result['count']} events.\n")
        if result['rows'] < result['count']:
            self.output.emit(f"Display limited to the first {result['rows']} rows; replay still reads the entire file.\n")

    def _load_finished(self):
        loader = self.loader; self.loader = None
        loader.deleteLater()
        self._set_state('failed' if self.state == 'failed' else 'stopped' if self.state == 'stopping' else 'idle')
        self.finished.emit(self.state)

    def start(self, path, owner, bus_channel, direction, delay_ms=0):
        if self.active:
            raise RuntimeError('A logfile operation is already running.')
        if str(path) != self.loaded_path:
            raise RuntimeError('Open a logfile before replaying it.')
        self.owner = owner; self.line = 0; self.outcome = None
        with self._lock:
            self._latest_line = 0
        self._display.setInterval(max(20, int(delay_ms)))
        self.transport.panel.start_playback(path, bus_channel, direction, self._attach)

    def _attach(self, worker):
        self.worker = worker
        worker.frame_sent.connect(self._capture_line, Qt.ConnectionType.DirectConnection)
        worker.paused.connect(self._paused)
        worker.progress.connect(lambda text:self.output.emit(text+'\n'))
        worker.completed.connect(self._completed)
        worker.failed.connect(self._failed)
        worker.finished.connect(self._ended)
        self._display.start()
        self._set_state('running')

    def _capture_line(self, row):
        # Called on the replay thread: only retain the latest row. UI sampling
        # cannot add a delay to bus transmission or queue a signal per frame.
        with self._lock:
            self._latest_line = row

    def _display_line(self):
        with self._lock:
            row = self._latest_line
        if row and row != self.line:
            self.line = row
            self.line_changed.emit(row)

    def pause(self):
        if self.worker and self.state == 'running':
            self._set_state('pausing')
            self.worker.pause()

    def _paused(self):
        if self.state == 'pausing':
            self._display_line()
            self._set_state('paused')
            self.output.emit('Replay paused.\n')

    def resume(self):
        if self.worker and self.state in ('paused', 'pausing'):
            self.worker.resume()
            self._set_state('running')
            self.output.emit('Replay resumed; recorded gaps exclude time spent paused.\n')

    def stop(self, reason='Stopped by user.'):
        if not self.active or self.state == 'stopping':
            return
        self.output.emit(reason+'\n')
        self._set_state('stopping')
        if self.loader:
            self.loader.requestInterruption()
        if self.worker:
            self.worker.stop()

    def _completed(self, text):
        self.outcome = 'completed'
        self.output.emit(text+'\n')

    def _failed(self, text):
        self.outcome = 'failed'
        self._set_state('failed')
        self.output.emit(text+'\n')

    def _ended(self):
        worker = self.worker
        self._display_line(); self._display.stop()
        self.outcome = 'stopped' if worker.cancel.is_set() else self.outcome or 'completed'
        self.worker = None
        self._set_state(self.outcome)
        self.output.emit(self.outcome.upper()+'\n')
        self.finished.emit(self.outcome)
