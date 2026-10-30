"""Save-on-stop recording and explicit TX replay controls."""
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, Signal, Slot, QTimer
from PySide6.QtWidgets import (QWidget, QHBoxLayout, QVBoxLayout, QPushButton, QLabel,
                               QComboBox, QSpinBox, QFileDialog, QMessageBox)

from signal_explorer.recording.recorder import MF4Recorder
from signal_explorer.recording.playback import MF4Playback


class RecordingPanel(QWidget):
    playback_changed = Signal(bool)
    close_ready = Signal()

    def __init__(self, service, parent=None, codec=None, playback_codec_factory=None, tx_options=None,
                 replay_label=None):
        super().__init__(parent)
        self.service = service
        self.recorder = MF4Recorder(self, codec=codec)
        self.playback_codec_factory = playback_codec_factory
        self.tx_options = tx_options
        self.replay_label = replay_label
        self.database_path = None
        self.playback = None
        self._closing = False
        self._saving = False
        self.log = QPushButton("Start Logging")
        self.log.clicked.connect(self._toggle_logging)
        self.save = QPushButton("Save Log...")
        self.save.clicked.connect(self._save_log)
        self.replay = QPushButton("Playback Log as TX")
        self.replay.clicked.connect(self._toggle_playback)
        self.source_bus = QSpinBox()
        self.source_bus.setRange(0, 255)
        self.source_bus.setValue(1)
        self.source_bus.setToolTip("Recorded MDF BusChannel to send through the currently connected adapter/channel.")
        self.direction = QComboBox()
        for label, key in (("RX + TX", "all"), ("Recorded RX only", "rx"), ("Recorded TX only", "tx")):
            self.direction.addItem(label, key)
        self.status = QLabel("Logger idle")
        self.status.setWordWrap(True)
        self.playback_status = QLabel("Playback idle")
        self.playback_status.setWordWrap(True)
        bar = QHBoxLayout()
        for widget in (self.log, self.save, self.replay, QLabel("Recorded bus"), self.source_bus,
                       self.direction, QLabel("Playback: 1x recorded timing")):
            bar.addWidget(widget)
        bar.addStretch()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(bar)
        layout.addWidget(self.status)
        layout.addWidget(self.playback_status)
        # Direct capture is thread-safe and independent of the trace UI's queue.
        service.traffic.connect(self.recorder.capture, Qt.ConnectionType.DirectConnection)
        service.connected.connect(self._refresh)
        service.disconnected.connect(self._refresh)
        service.about_to_disconnect.connect(self._on_disconnect)
        self.recorder.changed.connect(self._refresh)
        self.recorder.finalized.connect(self._on_finalized)
        self._refresh()

    @property
    def playback_busy(self):
        return self.playback is not None

    @Slot()
    def _refresh(self, *_):
        recording = self.recorder.recording
        self.log.setText("Stop Logging" if recording else "Start Logging")
        self.log.setEnabled(recording or (self.service.is_connected and not self.recorder.busy
                                         and self.recorder.pending_path is None))
        self.save.setEnabled(self.recorder.pending_path is not None and not self.recorder.busy and not self._saving)
        self.replay.setEnabled((self.service.is_connected and getattr(self.service, "can_replay", True)) or self.playback_busy)
        self.replay.setText("Stop Playback" if self.playback_busy else
                            self.replay_label() if self.replay_label else "Playback Log as TX")
        self.source_bus.setEnabled(not self.playback_busy)
        self.direction.setEnabled(not self.playback_busy)
        if recording:
            self.status.setText(f"Logging RX + TX: {self.recorder.count} events written (trace filters/pause do not affect recording)")
        elif self.recorder.busy:
            self.status.setText("Finalizing MDF4 recording...")
        elif self.recorder.pending_path:
            self.status.setText(f"Unsaved MDF4 recording ({self.recorder.count} frames): {self.recorder.pending_path}")

    def _toggle_logging(self):
        if self.recorder.recording:
            self.recorder.stop()
        elif self.service.is_connected:
            try:
                self.recorder.start(self.service.session_info, self.database_path)
            except Exception as exc:
                QMessageBox.warning(self, "Logging failed", str(exc))
        self._refresh()

    @Slot(str, str)
    def _on_finalized(self, path, warning):
        if warning:
            QMessageBox.warning(self, "Recording incomplete", warning)
        self._refresh()
        if path:
            self._save_log()
        else:
            self.status.setText(warning or "No MDF4 recording was produced.")
            self._closing = False

    def _save_log(self):
        if not self.recorder.pending_path or self._saving:
            return
        self._saving = True
        try:
            default = f"SignalExplorer-{datetime.now():%Y%m%d-%H%M%S}.mf4"
            path, _ = QFileDialog.getSaveFileName(self, "Save MDF4 recording", default, "ASAM MDF4 (*.mf4)")
            if not path:
                self._closing = False
                self._refresh()
                return
            normalized = Path(path)
            if normalized.suffix.lower() != ".mf4":
                normalized = Path(str(normalized) + ".mf4")
                if normalized.exists() and QMessageBox.question(
                        self, "Replace existing recording?", f"Replace {normalized}?",
                        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                        QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
                    self._closing = False
                    return
            path = str(normalized)
            if self.playback_busy and Path(path).resolve() == Path(self.playback.path).resolve():
                raise ValueError("Choose a different file while this recording is being played.")
            saved = self.recorder.save_as(path)
            self.status.setText(f"Saved {self.recorder.count} frames: {saved}")
        except Exception as exc:
            self._closing = False
            QMessageBox.warning(self, "Save failed - log retained", str(exc))
        finally:
            self._saving = False
            self._refresh()
        self._continue_close()

    def _toggle_playback(self):
        if self.playback_busy:
            self.playback.stop()
            self.playback_status.setText("Stopping playback...")
            return
        if not self.service.is_connected:
            return
        path, _ = QFileDialog.getOpenFileName(self, f"Select {self.recorder.codec.protocol} MDF4 log to replay", "", "ASAM MDF4 (*.mf4)")
        if not path:
            return
        self.start_playback(path)

    def start_playback(self, path, bus_channel=None, direction=None, configure=None):
        """Shared entry point for panel and file-viewer playback ownership."""
        if self.playback_busy:
            raise RuntimeError("Playback is already running for this protocol. Stop it first.")
        if not self.service.is_connected or not getattr(self.service, "can_replay", True):
            raise RuntimeError("Connect a replay-capable session before starting playback.")
        self.playback = MF4Playback(
            path, lambda msg: self.service.send_message(msg, report_errors=False), self.service.fd_enabled,
            self.source_bus.value() if bus_channel is None else bus_channel,
            self.direction.currentData() if direction is None else direction, self,
            codec=self.playback_codec_factory() if self.playback_codec_factory else None,
            tx_options=self.tx_options() if self.tx_options else None)
        self.playback.progress.connect(self.playback_status.setText)
        self.playback.failed.connect(self._playback_failed)
        self.playback.completed.connect(self.playback_status.setText)
        self.playback.finished.connect(self._playback_finished)
        if configure:
            configure(self.playback)
        self.playback_changed.emit(True)
        self._refresh()
        self.playback.start()
        return self.playback

    @Slot(str)
    def _playback_failed(self, text):
        self.playback_status.setText(text)
        QMessageBox.warning(self, "Playback failed", text)

    @Slot()
    def _playback_finished(self):
        worker = self.playback
        if worker is None:
            return
        if worker.cancel.is_set():
            self.playback_status.setText(f"Playback stopped: {worker.sent} {worker.tx_options.get('operation_label', 'TX operations')}")
        self.playback = None
        worker.deleteLater()
        self.playback_changed.emit(False)
        self._refresh()
        self._continue_close()

    def _on_disconnect(self):
        if self.playback_busy:
            self.playback.stop()
        # Stop acceptance before the bus is shut down. Drain queued log events.
        if self.recorder.recording:
            self.recorder.stop()

    def request_close(self):
        """Return False while a worker or unsaved recording needs attention."""
        if self.playback_busy:
            self._closing = True
            self.playback.stop()
        if self.recorder.busy:
            self._closing = True
            self.recorder.stop()
            return False
        if self.recorder.pending_path:
            self._closing = True
            self._save_log()
            return False
        return not self.playback_busy

    def _continue_close(self):
        if self._closing and not self.recorder.busy and not self.recorder.pending_path and not self.playback_busy:
            self._closing = False
            QTimer.singleShot(0, self.close_ready.emit)
