"""Launch the user's extracted asammdf GUI."""
from pathlib import Path
import os
import subprocess
import sys

from PySide6.QtCore import QSettings, QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout, QLabel, QLineEdit,
    QMessageBox, QPushButton, QVBoxLayout,
)


DOWNLOAD_URL = 'https://canlogger.csselectronics.com/files/asammdf/v890dev10/asammdfgui-v8.9.0dev10-windows.zip'
LOCATION_KEY = 'tools/asammdf_executable'


class AsammdfLocationDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('asammdf location')
        self.resize(620, 180)
        layout = QVBoxLayout(self)
        description = QLabel('Select the asammdf GUI executable in its extracted folder. '
                             'If needed, download the ZIP and extract it first.')
        description.setWordWrap(True)
        layout.addWidget(description)
        self.path = QLineEdit(QSettings().value(LOCATION_KEY, '', type=str))
        self.path.setPlaceholderText('Path to asammdfgui.exe')
        self.path.setAccessibleName('asammdf executable path')
        row = QHBoxLayout()
        row.addWidget(self.path)
        browse = QPushButton('Browse...')
        browse.clicked.connect(self.browse)
        row.addWidget(browse)
        layout.addLayout(row)
        download = QPushButton('Download asammdf')
        download.clicked.connect(self.download)
        layout.addWidget(download)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def browse(self):
        path, _ = QFileDialog.getOpenFileName(
            self, 'Find asammdf GUI', self.path.text(), 'Windows executable (*.exe)')
        if path:
            self.path.setText(path)

    def download(self):
        if not QDesktopServices.openUrl(QUrl(DOWNLOAD_URL)):
            QMessageBox.warning(self, 'Download unavailable', 'Could not open the download in your browser.')

    def accept(self):
        path = Path(self.path.text().strip().strip('"'))
        if not path.is_file() or path.suffix.lower() != '.exe':
            QMessageBox.warning(self, 'Select asammdf', 'Browse to the extracted asammdf GUI executable.')
            return
        QSettings().setValue(LOCATION_KEY, str(path.resolve()))
        super().accept()


def configure_asammdf(parent):
    return AsammdfLocationDialog(parent).exec() == QDialog.DialogCode.Accepted


def launch_asammdf(executable, recording):
    env = os.environ.copy()
    command = [str(executable), '--measurements', str(recording)]
    bundle = getattr(sys, '_MEIPASS', None)
    if sys.platform != 'win32' or not bundle:
        return subprocess.Popen(command, cwd=executable.parent, env=env)

    # The frozen app's DLL search path otherwise carries into the other GUI.
    import ctypes

    root = Path(bundle).resolve()
    for key in ('PATH', 'QT_PLUGIN_PATH', 'QT_QPA_PLATFORM_PLUGIN_PATH', 'QML2_IMPORT_PATH'):
        if key in env:
            entries = [entry for entry in env[key].split(os.pathsep)
                       if entry and not Path(entry.strip('"')).resolve().is_relative_to(root)]
            if entries:
                env[key] = os.pathsep.join(entries)
            else:
                del env[key]
    env['PYINSTALLER_RESET_ENVIRONMENT'] = '1'
    set_dll_directory = ctypes.windll.kernel32.SetDllDirectoryW
    if not set_dll_directory(None):
        raise ctypes.WinError()
    try:
        return subprocess.Popen(command, cwd=executable.parent, env=env)
    finally:
        set_dll_directory(str(bundle))


def open_in_asammdf(parent, path=None):
    executable = QSettings().value(LOCATION_KEY, '', type=str)
    if not executable or not Path(executable).is_file():
        if not configure_asammdf(parent):
            return
        executable = QSettings().value(LOCATION_KEY, '', type=str)
    if path is None:
        path, _ = QFileDialog.getOpenFileName(
            parent, 'Open in asammdf', '', 'MDF recordings (*.mf4 *.mdf *.dat);;All files (*)')
        if not path:
            return
    if not Path(path).is_file():
        QMessageBox.warning(parent, 'Recording unavailable', 'The selected recording could not be found.')
        return
    try:
        process = launch_asammdf(Path(executable), Path(path).resolve())
    except OSError as exc:
        QMessageBox.warning(parent, 'Could not open asammdf', str(exc))
        return

    def check_startup(attempts_left=20):
        status = process.poll()
        if status is None and attempts_left:
            QTimer.singleShot(500, parent, lambda: check_startup(attempts_left - 1))
        elif status:
            QMessageBox.warning(parent, 'asammdf exited during startup',
                                f'asammdf exited with code {status}. Try opening the executable directly '
                                'from its extracted folder to see any startup error.')

    check_startup()
