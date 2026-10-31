import os
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
from unittest.mock import patch
from PySide6.QtWidgets import QApplication
from signal_explorer.ui.adapter_preferences import AdapterPreference, ethernet_key
from signal_explorer.ui.main_window import MainWindow
from signal_explorer.ui.lin_panel import LinPanel
from signal_explorer.ui.ethernet_panel import EthernetPanel
from signal_explorer.ethernet.discovery import EthernetAdapter, virtual_adapter

app = QApplication.instance() or QApplication([])


def assert_sorted(combo):
    labels = [combo.itemText(i) for i in range(combo.count())]
    assert labels == sorted(labels, key=str.casefold)


def test_first_launch_virtual_even_with_canable_plugged_in():
    with patch('signal_explorer.ui.main_window.list_serial_ports', return_value=[('COM3', 'CANable')]), \
         patch('signal_explorer.ui.main_window.preferred_slcan_port', return_value='COM3'):
        window = MainWindow()
        try:
            assert window._backend.currentData() == 'virtual'
            assert window._lin.adapter.currentData() == 'virtual'
            assert window._ethernet._selected().backend == 'virtual'
            assert not window._service.is_connected
            assert_sorted(window._backend)
            assert_sorted(window._lin.adapter)
            assert window._backend.currentIndex() > 0
        finally:
            window.close()


def test_can_selection_restored_without_connecting_and_unknown_key_falls_back(adapter_settings):
    with patch('signal_explorer.ui.main_window.discover_channels', return_value=([], 'Mock scan')):
        first = MainWindow()
        first._backend.setCurrentIndex(first._backend.findData('nixnet'))
        first.close()
        assert adapter_settings.value('adapters/can/last_selected') == 'nixnet'
        second = MainWindow()
        try:
            assert second._backend.currentData() == 'nixnet'
            assert second._channel_value() == 'CAN1'
            assert not second._service.is_connected
            assert not second._lin.service.is_connected
        finally:
            second.close()
        AdapterPreference('can').save('removed-backend')
        third = MainWindow()
        assert third._backend.currentData() == 'virtual'
        third.close()


def test_lin_preference_is_independent_of_can():
    AdapterPreference('can').save('slcan')
    with patch('signal_explorer.lin.peak.discover_peak', return_value=([], 'No hardware')):
        first = LinPanel()
        first.adapter.setCurrentIndex(first.adapter.findData('peak'))
        first.close()
        second = LinPanel()
        try:
            assert second.adapter.currentData() == 'peak'
            assert not second.service.is_connected
            assert AdapterPreference('can').load() == 'slcan'
        finally:
            second.close()


def test_ethernet_identity_restored_after_sort_refresh_and_missing_device():
    native = EthernetAdapter('PC Ethernet | Bench B', 'pcap', dict(name='nic-b', mac='02:00:00:00:00:03'))
    other = EthernetAdapter('Intrepid | Bench A', 'neovi', dict(serial='1234', channel=1))
    options = [virtual_adapter(), native, other]
    with patch('signal_explorer.ui.ethernet_panel.discover_adapters', return_value=(options, 'Mock scan')):
        first = EthernetPanel()
        first._scan_adapters()
        assert first._selected().backend == 'virtual'
        assert_sorted(first.adapter)
        first.adapter.setCurrentIndex(next(i for i in range(first.adapter.count()) if first.adapter.itemData(i)==native))
        first.close()
        second = EthernetPanel()
        try:
            second._scan_adapters()
            assert second._selected().identity == native.identity
            assert not second.service.is_connected
            with patch('signal_explorer.ui.ethernet_panel.discover_adapters', return_value=([virtual_adapter()], 'Unplugged')):
                second._scan_adapters()
            assert second._selected().backend == 'virtual'
            assert AdapterPreference('ethernet').load() == ethernet_key(native)
            second._scan_adapters()
            assert second._selected().identity == native.identity
            unavailable = EthernetAdapter(native.label, native.backend, native.config, False, 'Missing driver')
            with patch('signal_explorer.ui.ethernet_panel.discover_adapters', return_value=([unavailable, virtual_adapter()], 'No driver')):
                second._scan_adapters()
            assert second._selected().backend == 'virtual'
            second.adapter.activated.emit(second.adapter.currentIndex())
            second._scan_adapters()
            assert second._selected().backend == 'virtual'
            assert AdapterPreference('ethernet').load() == ethernet_key(virtual_adapter())
        finally:
            second.close()
