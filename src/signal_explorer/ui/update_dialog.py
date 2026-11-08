"""Manual update checks and downloads without blocking the GUI."""

import logging
from queue import Empty, SimpleQueue
from threading import Thread

from PySide6.QtCore import QSettings, QTimer, Qt
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QVBoxLayout,
)

from signal_explorer.updates import download_update, find_update


logger = logging.getLogger(__name__)


class UpdateDialog(QDialog):
    def __init__(self, version, install, parent):
        super().__init__(parent)
        self.setWindowTitle("Check for Updates")
        self.resize(520, 330)
        self._version = version
        self._install = install
        self._update = None
        self._results = SimpleQueue()
        self._timer = QTimer(self, interval=100)
        self._timer.timeout.connect(self._receive_result)
        self._beta = QCheckBox("Include beta releases")
        self._beta.setChecked(QSettings().value("updates/include_beta", False, type=bool))
        self._beta.toggled.connect(self.check)
        self._status = QLabel()
        self._status.setTextFormat(Qt.TextFormat.PlainText)
        self._status.setWordWrap(True)
        self._notes = QPlainTextEdit(readOnly=True)
        self._check = QPushButton("Check Again")
        self._check.clicked.connect(self.check)
        self._download = QPushButton("Download and Install")
        self._download.clicked.connect(self._download_selected)
        self._later = QPushButton("Later")
        self._later.clicked.connect(self.reject)
        buttons = QHBoxLayout()
        for button in (self._check, self._download, self._later):
            buttons.addWidget(button)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f"Installed version {version}"))
        layout.addWidget(self._beta)
        layout.addWidget(self._status)
        layout.addWidget(self._notes)
        layout.addLayout(buttons)

    @property
    def busy(self):
        return self._timer.isActive()

    def reject(self):
        if not self.busy:
            super().reject()

    def check(self):
        if self.busy:
            return
        beta = self._beta.isChecked()
        QSettings().setValue("updates/include_beta", beta)
        self._update = None
        self._notes.clear()
        self._start("Checking GitHub Releases…", lambda: find_update(self._version, beta))

    def _download_selected(self):
        update = self._update
        self._start(f"Downloading and verifying version {update.version}…",
                    lambda: download_update(update), installing=True)

    def _start(self, message, operation, installing=False):
        self._status.setText(message)
        for control in (self._beta, self._check, self._download, self._later):
            control.setEnabled(False)

        def run():
            try:
                self._results.put((installing, operation(), None))
            except Exception as exc:
                logger.warning("Signal Explorer update failed", exc_info=True)
                self._results.put((installing, None, str(exc)))

        self._timer.start()
        Thread(target=run, name="update-request", daemon=True).start()

    def _receive_result(self):
        try:
            installing, result, error = self._results.get_nowait()
        except Empty:
            return
        self._timer.stop()
        for control in (self._beta, self._check, self._later):
            control.setEnabled(True)
        if error is not None:
            self._status.setText(f"Update request failed. {error}")
        elif installing:
            self._status.setText("Installer verified.")
            self.accept()
            self._install(result)
        elif result is None:
            self._status.setText("No newer Windows installer found for the selected releases.")
        else:
            self._update = result
            self._status.setText(f"Version {result.version} is available.")
            self._notes.setPlainText(result.notes)
        self._download.setEnabled(self._update is not None)
