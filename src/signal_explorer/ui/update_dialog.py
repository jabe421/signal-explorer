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
    def __init__(self, version, install, parent, *, development=False):
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
        self._latest = QLabel("Newest available version: Checking…")
        self._latest.setTextFormat(Qt.TextFormat.PlainText)
        self._notes = QPlainTextEdit(readOnly=True)
        self._notes.setAccessibleName("Release notes")
        self._notes.setPlaceholderText("Release notes for a newer version will appear here.")
        self._check = QPushButton("Check Again")
        self._check.clicked.connect(self.check)
        self._download = QPushButton("Update")
        self._download.setEnabled(False)
        self._download.clicked.connect(self._download_selected)
        self._later = QPushButton("Later")
        self._later.clicked.connect(self.reject)
        buttons = QHBoxLayout()
        for button in (self._check, self._download, self._later):
            buttons.addWidget(button)
        layout = QVBoxLayout(self)
        current = version or "Unavailable"
        if development:
            current += " (development build)"
        layout.addWidget(QLabel(f"Current version: {current}"))
        layout.addWidget(self._latest)
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
        self._latest.setText("Newest available version: Checking…")
        self._start("Checking GitHub Releases…", lambda: find_update(self._version, beta))

    def _download_selected(self):
        update = self._update
        if self.busy or self._install is None or update is None:
            return
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
            if not installing:
                self._latest.setText("Newest available version: Unavailable")
            self._status.setText(f"Update request failed. {error}")
        elif installing:
            self._status.setText("Installer verified.")
            self.accept()
            self._install(result)
        else:
            latest = result.latest_version or "No published release found"
            self._latest.setText(f"Newest available version: {latest}")
            self._update = result.update
            notes = []
            if result.latest_notes:
                notes.append(f"Release notes for {latest}\n\n{result.latest_notes}")
            if self._update and self._update.version != latest and self._update.notes:
                notes.append(f"Release notes for {self._update.version}\n\n{self._update.notes}")
            self._notes.setPlainText("\n\n".join(notes))
            if self._install is None:
                self._status.setText("To install an update from this dialog, open it in the installed Windows app.")
            elif self._update is None:
                self._status.setText("No newer Windows installer found for the selected releases.")
            else:
                self._status.setText(f"Windows installer {self._update.version} is available.")
        self._download.setEnabled(self._install is not None and self._update is not None)
