"""Capture current CAN/File Viewer figures without opening physical adapters."""
import os
os.environ['QT_QPA_PLATFORM']='offscreen'
from pathlib import Path
import time
from unittest.mock import patch
import can
from PySide6.QtGui import QFont, QFontDatabase, QIcon
from PySide6.QtWidgets import QApplication
from signal_explorer.ui.main_window import MainWindow
from signal_explorer.ethernet.discovery import virtual_adapter

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'docs/user_guide_assets'
app=QApplication([])
for name in ('arial.ttf','consola.ttf','consolab.ttf'):
    QFontDatabase.addApplicationFont('C:/Windows/Fonts/'+name)
app.setFont(QFont('Arial',9))
app.setWindowIcon(QIcon(str(ROOT/'src/signal_explorer/assets/signal-explorer.ico')))

def wait_for(predicate):
    deadline=time.monotonic()+8
    while time.monotonic()<deadline:
        app.processEvents()
        if predicate():return
        time.sleep(.005)
    raise RuntimeError('Figure capture timed out')

with patch('signal_explorer.ui.main_window.list_serial_ports',return_value=[]), \
     patch('signal_explorer.ui.main_window.discover_channels',return_value=([],'Figure capture: virtual only.')), \
     patch('signal_explorer.ui.ethernet_panel.discover_adapters',return_value=([virtual_adapter()],'Figure capture: virtual only.')):
    window=MainWindow();window.resize(1400,900)
    window._backend.setCurrentIndex(window._backend.findData('virtual'))
    window._fd.setChecked(True);window.show()
    with patch('signal_explorer.ui.main_window.QFileDialog.getOpenFileName',return_value=(str(ROOT/'examples/demo.dbc'),'')):
        window._load_dbc()
    window._toggle_connection()
    editor=window._open_demo_script();editor._run()
    wait_for(lambda:window._script_controller.line>25)
    editor._pause();wait_for(lambda:window._script_controller.state=='paused')
    editor.grab().save(str(OUT/'scripting.png'))
    editor.controller.stop();wait_for(lambda:not editor.controller.active)
    editor.close();app.processEvents()
    window.grab().save(str(OUT/'can.png'))
    folder=ROOT/'tmp/pdfs';folder.mkdir(parents=True,exist_ok=True)
    path=folder/'viewer-figure.mf4'
    with can.MF4Writer(path) as writer:
        now=time.time()
        for index in range(12):
            writer.on_message_received(can.Message(timestamp=now+index*.15,
                channel=1,is_rx=index%2!=0,is_extended_id=False,
                arbitration_id=0x321 if index%2==0 else 0x221,
                data=bytes((v+index//2)&255 for v in range(8))))
    viewer=window._open_logfile(path);wait_for(lambda:not viewer.controller.active)
    viewer._run();wait_for(lambda:viewer.controller.line>=3)
    viewer._pause();wait_for(lambda:viewer.controller.state=='paused')
    viewer.grab().save(str(OUT/'log-replay.png'))
    viewer.controller.stop();wait_for(lambda:not viewer.controller.active)
    window.close();app.processEvents()
print('Captured can.png, scripting.png, log-replay.png')
