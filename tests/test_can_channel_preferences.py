import os
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
from unittest.mock import Mock, patch
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication
from signal_explorer.bus.discovery import ChannelOption
from signal_explorer.ui.adapter_preferences import CanChannelPreferences
from signal_explorer.ui.can_channels import CanChannelsDialog
from signal_explorer.ui.main_window import MainWindow
from test_multichannel import LoopBus

app = QApplication.instance() or QApplication([])


def options(serial=123, channels=(0,1,2,3)):
    return [ChannelOption(f'VN1630A | {serial} | CAN{i+1}',
                          dict(channel=i, serial=serial, channel_index=i, app_name=None,
                               device_name='VN1630A', channel_label=f'CAN{i+1}', supports_fd=True)) for i in channels]


def choose(window, selected):
    dialog=Mock();dialog.exec.return_value=True;dialog.selected_configs=[o.config for o in selected]
    with patch('signal_explorer.ui.main_window.CanChannelsDialog',return_value=dialog):
        window._choose_can_channels()


def test_subset_survives_disconnect_refresh_and_relaunch_without_connecting():
    available=options()
    with patch('signal_explorer.ui.main_window.discover_channels',return_value=(available,'Found')):
        first=MainWindow()
        try:
            first._backend.setCurrentIndex(first._backend.findData('vector'))
            choose(first,available[:2])
            with patch('signal_explorer.bus.service.can.Bus',return_value=LoopBus()) as factory:
                first._toggle_connection()
            assert factory.call_args.kwargs['channel']==[0,1]
            first._toggle_connection()
            assert [c['channel'] for c in first._enabled_can_configs]==[0,1]
            first._refresh.click()
            assert [c['channel'] for c in first._enabled_can_configs]==[0,1]
        finally:first.close()
        with patch('signal_explorer.bus.service.can.Bus') as factory:
            second=MainWindow()
        try:
            assert second._backend.currentData()=='vector'
            assert [c['channel'] for c in second._enabled_can_configs]==[0,1]
            assert second._channels_button.text()=='2 channels enabled'
            assert not second._service.is_connected
            factory.assert_not_called()
            dialog=CanChannelsDialog(available,'vector',second._enabled_can_configs)
            assert [dialog.channels.item(i).checkState()==Qt.CheckState.Checked for i in range(4)]==[True,True,False,False]
            dialog.close()
        finally:second.close()


def test_each_device_and_backend_retains_its_own_subset_and_new_device_defaults_to_first():
    prefs=CanChannelPreferences('vector')
    prefs.save('123',[options()[1].config])
    prefs.save('456',[options(456)[0].config,options(456)[2].config])
    other=CanChannelPreferences('neovi')
    assert other.channels('123') is None
    dialog=CanChannelsDialog(options()+options(456)+options(789),'vector')
    try:
        assert dialog.device.currentData()=='456'
        assert [dialog.channels.item(i).checkState()==Qt.CheckState.Checked for i in range(4)]==[True,False,True,False]
        dialog.device.setCurrentIndex(dialog.device.findData('123'))
        assert [dialog.channels.item(i).checkState()==Qt.CheckState.Checked for i in range(4)]==[False,True,False,False]
        dialog.device.setCurrentIndex(dialog.device.findData('789'))
        assert [dialog.channels.item(i).checkState()==Qt.CheckState.Checked for i in range(4)]==[True,False,False,False]
        dialog.reject()
        assert prefs.last_device()=='456'
    finally:dialog.close()


def test_restore_last_device_even_when_scan_order_changes_and_selection_is_noncontiguous():
    available=options()
    with patch('signal_explorer.ui.main_window.discover_channels',return_value=(available,'Found')):
        first=MainWindow()
        first._backend.setCurrentIndex(first._backend.findData('vector'))
        choose(first,[available[1],available[3]])
        first.close()
    reordered=options(456)+list(reversed(available))
    with patch('signal_explorer.ui.main_window.discover_channels',return_value=(reordered,'Found')):
        second=MainWindow()
    try:
        assert second._channel.currentData()['serial']==123
        assert second._channel_value()=='1'
        assert [c['channel'] for c in second._enabled_can_configs]==[1,3]
    finally:second.close()


def test_changed_hardware_restores_only_present_channels_and_preserves_missing_preferences():
    prefs=CanChannelPreferences('vector')
    prefs.save('123',[o.config for o in options()[:2]])
    with patch('signal_explorer.ui.main_window.discover_channels',return_value=(options(channels=(0,2,3)),'CAN2 removed')):
        window=MainWindow()
        window._backend.setCurrentIndex(window._backend.findData('vector'))
    try:
        assert [c['channel'] for c in window._enabled_can_configs]==[0]
        assert len(prefs.channels('123'))==2
        with patch('signal_explorer.ui.main_window.discover_channels',return_value=(options(),'CAN2 back')):
            window._refresh.click()
        assert [c['channel'] for c in window._enabled_can_configs]==[0,1]
        with patch('signal_explorer.ui.main_window.discover_channels',return_value=(options(channels=(2,3)),'Both removed')):
            window._refresh.click()
        assert window._saved_channels_unavailable
        with patch('signal_explorer.ui.main_window.QMessageBox.warning') as warning, patch.object(window._service,'connect_bus') as connect:
            window._toggle_connection()
        warning.assert_called_once();connect.assert_not_called()
    finally:window.close()


def test_kvaser_restore_uses_physical_label_after_driver_index_changes():
    prefs=CanChannelPreferences('kvaser')
    prefs.save('123',[dict(channel=0,device_serial='123',channel_label='CAN1')])
    changed=ChannelOption('U100 | 123 | CAN1',dict(channel=7,device_serial='123',channel_label='CAN1'))
    with patch('signal_explorer.ui.main_window.discover_channels',return_value=([changed],'Found')):
        window=MainWindow()
        window._backend.setCurrentIndex(window._backend.findData('kvaser'))
    try:
        assert window._enabled_can_configs[0]['channel']==7
    finally:window.close()


def test_cancel_and_corrupt_settings_do_not_replace_last_valid_selection(adapter_settings):
    prefs=CanChannelPreferences('vector')
    prefs.save('123',[options()[1].config])
    with patch('signal_explorer.ui.main_window.discover_channels',return_value=(options(),'Found')):
        window=MainWindow()
        window._backend.setCurrentIndex(window._backend.findData('vector'))
    try:
        before=prefs.channels('123')
        dialog=Mock();dialog.exec.return_value=False
        with patch('signal_explorer.ui.main_window.CanChannelsDialog',return_value=dialog):
            window._choose_can_channels()
        assert prefs.channels('123')==before
        for malformed in ('not json','[]','{"devices": []}'):
            adapter_settings.setValue(prefs.key,malformed)
            assert prefs.channels('123') is None
    finally:window.close()


def test_default_channel_on_first_connection_remembers_device_and_single_channel():
    with patch('signal_explorer.ui.main_window.discover_channels',return_value=(options(),'Found')):
        first=MainWindow()
        try:
            first._backend.setCurrentIndex(first._backend.findData('vector'))
            with patch('signal_explorer.bus.service.can.Bus',return_value=LoopBus()):
                first._toggle_connection()
            prefs=CanChannelPreferences('vector')
            assert prefs.last_device()=='123' and len(prefs.channels('123'))==1
        finally:first.close()
    with patch('signal_explorer.ui.main_window.discover_channels',return_value=(options(456)+options(),'Reordered')):
        second=MainWindow()
    try:
        assert second._channel.currentData()['serial']==123
        assert [c['channel'] for c in second._enabled_can_configs]==[0]
        assert not second._service.is_connected
    finally:second.close()
