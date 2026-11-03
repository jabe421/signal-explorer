"""Capture November 2 CAN UI using virtual traffic and an illustrative inventory.

Run with the project's .venv. Temporary Qt settings protect user preferences.
No physical adapter is opened or transmitted on by this script.
"""
import os
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
from dataclasses import replace
from pathlib import Path
import tempfile
import time
from unittest.mock import patch
from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import QApplication
from signal_explorer.bus.discovery import ChannelOption
from signal_explorer.bus.transmit import TxEntry, dbc_entry, encode_signals
from signal_explorer.ui.can_channels import CanChannelsDialog
from signal_explorer.ui.main_window import MainWindow

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'docs/user_guide_assets'
app = QApplication([])
for name in ('arial.ttf', 'arialbd.ttf', 'consola.ttf', 'consolab.ttf'):
    QFontDatabase.addApplicationFont('C:/Windows/Fonts/' + name)
app.setFont(QFont('Arial', 9))

def wait(seconds):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.01)

with tempfile.TemporaryDirectory() as folder:
    settings = QSettings(str(Path(folder) / 'figures.ini'), QSettings.Format.IniFormat)
    with patch('signal_explorer.ui.adapter_preferences.QSettings', return_value=settings):
        window = MainWindow()
        window.resize(1440, 930)
        window._fd.setChecked(True)
        with patch('signal_explorer.ui.main_window.QFileDialog.getOpenFileName',
                   return_value=(str(ROOT/'examples/demo.dbc'), '')):
            window._load_dbc()
        window.show()
        window._toggle_connection()
        wait(.6)
        assert window._service.is_connected and not window._auto_scroll.isChecked()
        window.grab().save(str(OUT/'can.png'))
        sender = window._open_transmit_window()
        sender.resize(1540, 820)
        definition = window._model.database.get_message_by_frame_id(0x200)
        entry = replace(dbc_entry(definition), cyclic=True, period_ms=100)
        values = definition.decode(entry.data, decode_choices=False)
        values['EngineRPM'] = 2000
        entry = replace(entry, data=encode_signals(definition, entry.data, values))
        sender.engine.add(entry)
        fd = replace(dbc_entry(window._model.database.get_message_by_frame_id(0x300)),
                     cyclic=True, period_ms=250, brs=True)
        sender.engine.add(fd)
        sender.engine.add(TxEntry(name='Echo request', identifier=0x321, data=bytes(range(8))))
        sender.table.setCurrentCell(0, 1)
        sender.engine.start([entry.uid], True)
        wait(.35)
        sender.grab().save(str(OUT/'transmit-list.png'))
        sender.engine.stop_all()
        options = []
        for index in range(4):
            transceiver = 'CANpiggy 1057Gcap (Highspeed)' if index < 2 else 'On board CAN 1051cap (Highspeed)'
            config = dict(channel=index, channel_index=index, serial=604069,
                          device_name='VN1630A', channel_label=f'CAN{index+1}',
                          app_name=None, supports_fd=True, transceiver=transceiver)
            options.append(ChannelOption(f'VN1630A | CAN{index+1} | {transceiver}', config))
        dialog = CanChannelsDialog(options, 'vector', current=[o.config for o in options[:2]])
        dialog.resize(900, 350)
        dialog.show()
        wait(.1)
        dialog.grab().save(str(OUT/'can-channels.png'))
        dialog.reject()
        sender._dirty = False
        window.close()
        app.processEvents()
print('Captured virtual CAN, transmit list, and illustrative channel selection.')
