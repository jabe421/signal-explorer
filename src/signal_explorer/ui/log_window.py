"""Read-only log mode of the script-editor window, sharing its layout/tools."""
from pathlib import Path

from PySide6.QtWidgets import QFileDialog, QLabel, QMessageBox, QComboBox, QSpinBox, QToolBar

from signal_explorer.recording.viewer import LogReplayController
from signal_explorer.ui.script_window import ScriptWindow


class LogWindow(ScriptWindow):
    def __init__(self, panels, parent=None):
        controller = LogReplayController(panels)
        super().__init__(controller, parent)
        controller.setParent(self)
        self.editor.highlighter.setDocument(None)
        self.output_label.setText('Replay Output')
        self.console.setPlaceholderText('Replay progress, results and errors appear here.')
        self.open_action.setText('Open Logfile...')
        self.open_log_action.setVisible(False)
        self.run.setText('Replay')
        self.delay.setToolTip('Log display/highlight refresh interval only. 0 follows at UI speed. Transmission always uses 1x recorded timing.')
        self.note.setText('Read-only MDF4 log | Replay sends selected recorded RX/TX events through the connected protocol session. '
                          '1x recorded timing; line delay affects display only. LIN retains Master/Slave role rules.')
        bar = QToolBar('Replay selection', self); self.addToolBarBreak(); self.addToolBar(bar)
        bar.addWidget(QLabel('Recorded bus: '))
        self.source_bus = QSpinBox(); self.source_bus.setRange(0, 255); self.source_bus.setValue(1)
        self.source_bus.setToolTip('Recorded BusChannel mapped to the currently connected adapter/channel.')
        bar.addWidget(self.source_bus)
        self.direction = QComboBox()
        for label, key in (('RX and TX', 'all'), ('RX only', 'rx'), ('TX only', 'tx')):
            self.direction.addItem(label, key)
        bar.addWidget(QLabel('Recorded direction: ')); bar.addWidget(self.direction)
        bar.addWidget(QLabel('  Replay: 1x recorded timing'))
        controller.loaded.connect(self._loaded)
        self._refresh()

    def _title(self, *_):
        self.setWindowTitle(f'{self.path or "Logfile"} - Signal Explorer File Viewer')

    def _confirm_save(self):
        return True

    def new_script(self, *_):
        # ScriptWindow initializes its shared editor by calling this hook.
        self.editor.setPlainText('')
        self.editor.document().setModified(False)
        self._title()
        return True

    def open_script(self, path=None):
        return self.open_log(path)

    def open_log(self, path=None):
        if self.controller.active:
            return False
        if not isinstance(path, (str, Path)):
            path, _ = QFileDialog.getOpenFileName(self, 'Open Logfile', '', 'ASAM MDF4 logs (*.mf4 *.MF4);;All files (*)')
        if not path:
            return False
        try:
            self.console.clear()
            self.path = Path(path).resolve()
            self.editor.setPlainText('Loading logfile...')
            self.editor.document().setModified(False)
            self.editor.set_execution(0)
            self._title()
            self.controller.load(self.path, self)
            return True
        except Exception as exc:
            QMessageBox.warning(self, 'Open logfile failed', str(exc))
            return False

    def _loaded(self, result):
        self.editor.setPlainText(result['text'])
        self.editor.document().setModified(False)
        buses = result['buses']
        if buses:
            self.source_bus.setValue(buses[0])
            self.source_bus.setToolTip(f'Recorded bus channels: {", ".join(map(str,buses))}. Sends to the current connected channel.')
        self._refresh()

    def _run(self):
        try:
            self.console.clear(); self.editor.set_execution(0)
            self.controller.start(self.path, self, self.source_bus.value(),
                                  self.direction.currentData(), self.delay.value())
        except Exception as exc:
            QMessageBox.warning(self, 'Cannot replay logfile', str(exc))

    def _refresh(self, *_):
        controller = self.controller
        active = controller.active
        self.editor.setReadOnly(True)
        for action in (self.new_action, self.save_action, self.save_as_action):
            action.setEnabled(False)
        self.open_action.setEnabled(not active)
        ready = controller.transport.is_connected and not controller.transport.panel.playback_busy
        self.run.setEnabled(bool(controller.loaded_path) and not active and ready)
        self.pause.setEnabled(controller.worker is not None and controller.state in ('running','paused','pausing'))
        self.pause.setText('Resume' if controller.state in ('paused','pausing') else 'Pause')
        self.stop.setEnabled(active and controller.state != 'stopping')
        self.delay.setEnabled(not active)
        for name in ('source_bus','direction'):
            widget = getattr(self, name, None)
            if widget is not None:
                widget.setEnabled(not active)
        service = controller.transport.panel.service
        info = service.session_info
        self.session.setText(f'{controller.transport.protocol} replay destination: '
                             + (f"{info.get('adapter','')} | {info.get('native_channel','')}"
                                + (f" | {info['role']}" if 'role' in info else '')
                                if service.is_connected else 'Disconnected — connect in the corresponding protocol tab.'))
        self.statusBar().showMessage(controller.state.capitalize()+' | Logfile is read-only')
        self.editor.set_execution(controller.line, controller.state, False)

    def _finished(self, *_):
        if self.controller.loaded_path is None and self.controller.state == 'failed':
            self.editor.setPlainText('Could not open logfile. See Replay Output for details.')
            self.editor.document().setModified(False)
        super()._finished()
