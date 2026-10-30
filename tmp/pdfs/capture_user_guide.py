"""Capture only simulated traffic for the manual; never open physical buses."""
import os
os.environ['QT_QPA_PLATFORM']='offscreen'
from pathlib import Path
from unittest.mock import patch
import time
import cantools
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QFontDatabase,QFont
from signal_explorer.ui.main_window import MainWindow

app=QApplication([])
QFontDatabase.addApplicationFont('C:/Windows/Fonts/arial.ttf')
app.setFont(QFont('Arial',9))
with patch('signal_explorer.ui.main_window.preferred_slcan_port',return_value=None):
    window=MainWindow()
window.resize(1600,980)
window._backend.setCurrentIndex(window._backend.findData('virtual'))
window._fd.setChecked(True)
window._model.set_database(cantools.database.load_file('examples/demo.dbc'))
window._dbc_label.setText('demo.dbc (3 messages)')
window._toggle_connection()
window.show()
deadline=time.monotonic()+.7
while time.monotonic()<deadline:
    app.processEvents();time.sleep(.02)
window.grab().save('tmp/pdfs/manual-can.png')
window._service.disconnect_bus()
window._tabs.setCurrentIndex(1)
panel=window._lin
panel.role.setCurrentIndex(panel.role.findData('master'))
panel._toggle_connection()
panel._toggle_schedule()
deadline=time.monotonic()+.3
while time.monotonic()<deadline:
    app.processEvents();time.sleep(.02)
panel.service.stop_schedule()
panel.grab().save('tmp/pdfs/manual-lin-panel.png')
window.grab().save('tmp/pdfs/manual-lin.png')
panel.service.disconnect_bus()
window.close()
print('Captured CAN FD and LIN simulation screenshots.')
