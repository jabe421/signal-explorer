"""Capture the Ethernet guide figure with synthetic frames and mocked discovery.

Run using the project virtual environment (PySide6 required). No hardware opens.
"""
import os
os.environ['QT_QPA_PLATFORM']='offscreen'
from pathlib import Path
from unittest.mock import patch
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QFontDatabase,QFont
from signal_explorer.ethernet.discovery import virtual_adapter
from signal_explorer.ui.main_window import MainWindow

app=QApplication([])
if Path('C:/Windows/Fonts/arial.ttf').is_file():
    QFontDatabase.addApplicationFont('C:/Windows/Fonts/arial.ttf')
    app.setFont(QFont('Arial',9))
with patch('signal_explorer.ui.ethernet_panel.discover_adapters',return_value=([virtual_adapter()],'Simulation figure - hardware discovery disabled.')):
    window=MainWindow();window.resize(1400,900);window._tabs.setCurrentIndex(2);window.show()
    app.processEvents();panel=window._ethernet
    panel.demo.setChecked(False);panel.connect_button.click()
    for _ in range(2):
        for index in range(panel.test.count()):panel.service.inject_test(panel.test.itemText(index))
    panel.test.setCurrentText('VLAN UDP');panel.load_test.click();panel.send.click()
    panel.table.selectRow(8);app.processEvents()
    output=Path(__file__).parent/'user_guide_assets'/'ethernet.png'
    window.grab().save(str(output));window.close()
    print(output)
