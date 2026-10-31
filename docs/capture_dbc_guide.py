"""Capture the DBC editor and CAN view using only virtual traffic."""
import os
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
from pathlib import Path
import tempfile
import time
from unittest.mock import patch
from PySide6.QtCore import QSettings
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import QApplication
from signal_explorer.ui.main_window import MainWindow
from signal_explorer.bus.demo import demo_frames

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'docs/user_guide_assets'
app = QApplication([])
for name in ('arial.ttf', 'arialbd.ttf', 'consola.ttf', 'consolab.ttf'):
    QFontDatabase.addApplicationFont('C:/Windows/Fonts/' + name)
app.setFont(QFont('Arial', 9))
with tempfile.TemporaryDirectory() as folder:
    settings = QSettings(str(Path(folder) / 'figures.ini'), QSettings.Format.IniFormat)
    with patch('signal_explorer.ui.adapter_preferences.QSettings', return_value=settings):
        window = MainWindow()
        window.resize(1400, 900)
        window._fd.setChecked(True)
        with patch('signal_explorer.ui.main_window.QFileDialog.getOpenFileName', return_value=(str(ROOT/'examples/demo.dbc'), '')):
            window._load_dbc()
        window.show()
        window._toggle_connection()
        deadline = time.monotonic() + .6
        while time.monotonic() < deadline:
            app.processEvents()
            time.sleep(.01)
        window.grab().save(str(OUT/'can.png'))
        editor = window._open_dbc_editor()
        editor.messages.setCurrentRow(next(i for i,m in enumerate(editor.document.database.messages) if m.frame_id == 0x200))
        editor.signals.setCurrentCell(0, 0)
        editor.payload.setText(next(m for m in demo_frames(42, False) if m.arbitration_id == 0x200).data.hex(' '))
        app.processEvents()
        editor.grab().save(str(OUT/'dbc-editor.png'))
        window.close()
        app.processEvents()
print('Captured virtual CAN and offline DBC editor figures.')
