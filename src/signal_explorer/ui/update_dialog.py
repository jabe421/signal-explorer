"""Manual update checks and downloads without blocking the GUI."""

from collections import deque
import logging
from queue import Empty, SimpleQueue
from threading import Thread

from PySide6.QtCore import QSettings, QTimer, Qt
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QHBoxLayout, QLabel, QPlainTextEdit, QProgressBar, QPushButton,
    QVBoxLayout,
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
        self._closing_for_install = False
        self._results = SimpleQueue()
        self._download_progress = deque(maxlen=1)
        self._timer = QTimer(self, interval=100)
        self._timer.timeout.connect(self._receive_result)
        self._beta = QCheckBox("Include beta releases")
        self._beta.setChecked(QSettings().value("updates/include_beta", False, type=bool))
        self._beta.toggled.connect(self.check)
        self._status = QLabel()
        self._status.setTextFormat(Qt.TextFormat.PlainText)
        self._status.setWordWrap(True)
        self._progress = QProgressBar()
        self._progress.setAccessibleName("Installer download progress")
        self._progress.hide()
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
        layout.addWidget(self._progress)
        layout.addWidget(self._notes)
        layout.addLayout(buttons)

    @property
    def busy(self):
        return self._timer.isActive()

    def reject(self):
        if not self.busy and not self._closing_for_install:
            super().reject()

    def check(self):
        if self.busy or self._closing_for_install:
            return
        beta = self._beta.isChecked()
        QSettings().setValue("updates/include_beta", beta)
        self._update = None
        self._progress.hide()
        self._notes.clear()
        self._latest.setText("Newest available version: Checking…")
        self._start("Checking GitHub Releases…", lambda: find_update(self._version, beta))

    def _download_selected(self):
        update = self._update
        if self.busy or self._closing_for_install or self._install is None or update is None:
            return
        self._download_progress.clear()
        self._progress.setValue(0)
        self._progress.show()
        self._start(f"Downloading version {update.version}…",
                    lambda: download_update(update, progress=self._report_download),
                    installing=True)

    def _report_download(self, received, total):
        self._download_progress.append(received * 100 // total)

    def finish_install_shutdown(self, *, cancelled):
        self._closing_for_install = False
        if cancelled:
            self._status.setText("Update canceled. Signal Explorer is still open.")
            self._progress.hide()
            self._set_idle_controls()
        else:
            self.accept()

    def _set_idle_controls(self):
        for control in (self._beta, self._check, self._later):
            control.setEnabled(True)
        self._download.setEnabled(self._install is not None and self._update is not None)

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
            percent = self._download_progress.pop()
        except IndexError:
            pass
        else:
            self._progress.setValue(percent)
            if percent == 100:
                self._status.setText("Verifying the downloaded installer…")
        try:
            installing, result, error = self._results.get_nowait()
        except Empty:
            return
        self._timer.stop()
        self._download_progress.clear()
        if error is not None:
            self._progress.hide()
            if not installing:
                self._latest.setText("Newest available version: Unavailable")
            self._status.setText(f"Update request failed. {error}")
        elif installing:
            self._progress.setValue(100)
            self._closing_for_install = True
            self._status.setText("Installer verified. Closing Signal Explorer before starting Windows Setup…")
            self.repaint()
            QTimer.singleShot(0, lambda: self._install(result))
            return
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
        self._set_idle_controls()
