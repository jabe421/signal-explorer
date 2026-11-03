import os
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
from copy import copy
from queue import Queue, Empty
from types import SimpleNamespace
import time
from unittest.mock import patch, Mock
import can
import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication
from signal_explorer.bus.backends import BACKENDS
from signal_explorer.bus.discovery import ChannelOption, discover_channels
from signal_explorer.bus.service import BusService
from signal_explorer.bus.multichannel import open_channels
from signal_explorer.recording.recorder import MF4Recorder
from signal_explorer.ui.can_channels import CanChannelsDialog
from signal_explorer.ui.main_window import MainWindow

app = QApplication.instance() or QApplication([])
VECTOR = next(b for b in BACKENDS if b.interface == 'vector')


class LoopBus:
    protocol = can.CanProtocol.CAN_FD
    def __init__(self):
        self.queue = Queue(); self.sent = []; self.closed = False
    def recv(self, timeout=None):
        try: return self.queue.get(timeout=timeout)
        except Empty: return None
    def send(self, msg, timeout=None):
        self.sent.append(copy(msg))
        received = copy(msg); received.channel = 1; received.is_rx = True; received.timestamp = time.time()
        self.queue.put(received)
    def shutdown(self): self.closed = True


def configs(indices=(0,1)):
    return [dict(channel=i, serial=123, channel_index=i, app_name=None, supports_fd=True,
                 channel_label=f'CAN{i+1}', device_name='VN1630A', transceiver='CANpiggy 1057Gcap') for i in indices]


def wait_for(predicate):
    deadline=time.monotonic()+3
    while time.monotonic()<deadline:
        app.processEvents()
        if predicate(): return True
        time.sleep(.005)
    return False


@pytest.mark.parametrize('fd', [False, True])
def test_selective_native_session_tx_rx_trace_and_mf4(fd, tmp_path):
    window = MainWindow(); wire = LoopBus()
    selected = configs()
    options = [ChannelOption(c['channel_label'], c) for c in configs(range(4))]
    recorder = MF4Recorder(temp_directory=tmp_path)
    try:
        with patch('signal_explorer.ui.main_window.discover_channels', return_value=(options, 'Four CAN channels')):
            window._backend.setCurrentIndex(window._backend.findData('vector'))
        dialog=Mock();dialog.exec.return_value=True;dialog.selected_configs=selected
        with patch('signal_explorer.ui.main_window.CanChannelsDialog',return_value=dialog):
            window._channels_button.click()
        window._fd.setChecked(fd)
        with patch('signal_explorer.bus.service.can.Bus', return_value=wire) as factory:
            window._toggle_connection()
        args = factory.call_args.kwargs
        assert args['channel'] == [0,1] and args['serial'] == 123
        assert 'channel_index' not in args and not args['receive_own_messages']
        assert bool(args.get('fd')) == fd
        if fd: assert args['data_bitrate'] == 2000000
        assert window._tx_channel.count() == 2 and not window._channels_button.isEnabled()
        recorder.start(window._service.session_info)
        window._service.traffic.connect(recorder.capture, Qt.ConnectionType.DirectConnection)
        payload=bytes(range(32 if fd else 8))
        assert window._service.send_message(can.Message(arbitration_id=0x321, is_extended_id=False,
                                                        data=payload, is_fd=fd, bitrate_switch=fd))
        assert wait_for(lambda: window._rx_count >= 1)
        assert wire.sent[0].channel == 0
        labels={window._model.data(window._model.index(i,2)) for i in range(window._model.rowCount())}
        assert labels == {'CAN1','CAN2'}
        window._auto_scroll.setChecked(False)
        assert window._model.rowCount() == 2
        assert not window._service.send_message(can.Message(channel=2, data=b'1'), report_errors=False)
        assert len(wire.sent) == 1
        window._tx_channel.setCurrentIndex(1)
        assert window._service.tx_channel == 1
        recorder.stop(); recorder._thread.join(10)
        assert not recorder.problem
        with can.MF4Reader(recorder.pending_path) as reader:
            saved=list(reader)
        assert {m.channel for m in saved} == {1,2}
        assert len(saved)==2 and all(m.data==payload and m.is_fd==fd and m.bitrate_switch==fd for m in saved)
    finally:
        window._service.disconnect_bus(); window.close()
        recorder.stop()
        if recorder._thread: recorder._thread.join(10)
    assert wire.closed


def test_noncontiguous_channels_and_fd_guard_before_open():
    service=BusService(); wire=LoopBus()
    selection=configs((0,3))
    try:
        with patch('signal_explorer.bus.service.can.Bus', return_value=wire) as factory:
            service.connect_bus(VECTOR,'0',500000,False,dict(selection[0], channels=selection),fd=True)
        assert factory.call_args.kwargs['channel']==[0,3]
        service.select_tx_channel('CAN4')
        assert service.send_message(can.Message(data=bytes(12),is_fd=True))
        assert wire.sent[-1].channel==3
    finally: service.disconnect_bus()
    selection[1]['supports_fd']=False
    with patch('signal_explorer.bus.service.can.Bus') as factory:
        service.connect_bus(VECTOR,'0',500000,False,dict(selection[0],channels=selection),fd=True)
    factory.assert_not_called()


def test_device_picker_does_not_enable_other_channels_or_virtual_devices():
    physical=[ChannelOption(c['channel_label'],c) for c in configs(range(4))]
    virtual=ChannelOption('Vector virtual',dict(channel=0,serial=100))
    dialog=CanChannelsDialog(physical+[virtual],'vector',[physical[0].config])
    assert dialog.device.count()==2 and dialog.channels.count()==4
    assert dialog.channels.item(0).checkState()==Qt.CheckState.Checked
    assert dialog.channels.item(2).checkState()==Qt.CheckState.Unchecked
    dialog.channels.item(1).setCheckState(Qt.CheckState.Checked)
    dialog.accept()
    assert [c['channel'] for c in dialog.selected_configs]==[0,1]
    dialog.close()


def test_fire3_network_inventory_and_single_handle_including_high_network_ids():
    import ics
    device=SimpleNamespace(Name='neoVI FIRE 3',DeviceType=ics.NEODEVICE_FIRE3,SerialNumber=1879029185)
    with patch('ics.find_devices',return_value=[device]):
        options,note=discover_channels('neovi')
    assert len(options)==16 and all(c.config['lin_channel_count']==8 for c in options)
    assert options[-1].config['network_id']==ics.NETID_DWCAN_16
    selection=[options[0].config,options[-1].config]
    factory=Mock(return_value=Mock())
    open_channels(factory,'neovi',selection,dict(interface='neovi',bitrate=500000,fd=True,data_bitrate=2000000))
    factory.assert_called_once()
    assert factory.call_args.kwargs['channel']==[ics.NETID_DWCAN_01,ics.NETID_DWCAN_16]
    dialog=CanChannelsDialog(options,'neovi',[selection[0]])
    assert 'LIN8' in dialog.inventory.text()
    dialog.close()


def test_partial_open_rolls_back_and_cross_device_native_open_is_rejected():
    first=Mock()
    factory=Mock(side_effect=[first,RuntimeError('port busy')])
    with pytest.raises(RuntimeError,match='port busy'):
        open_channels(factory,'pcan',[dict(channel='PCAN_USBBUS1'),dict(channel='PCAN_USBBUS2')],dict(interface='pcan'))
    first.shutdown.assert_called_once()
    selection=configs();selection[1]['serial']=456
    factory=Mock()
    with pytest.raises(ValueError,match='one identified'):
        open_channels(factory,'vector',selection,dict(interface='vector'))
    factory.assert_not_called()


def test_multiple_independent_handles_route_to_one_channel_and_report_receive_failures():
    from signal_explorer.bus.multichannel import ChannelMux
    first,second=LoopBus(),LoopBus()
    bus=ChannelMux({'CAN1':first,'CAN2':second})
    try:
        bus.send(can.Message(channel='CAN2',data=b'2'))
        assert not first.sent and len(second.sent)==1
        assert bus.recv(.5).channel=='CAN2'
        with pytest.raises(ValueError,match='not enabled'):
            bus.send(can.Message(channel='CAN3'))
    finally:bus.shutdown()
    assert first.closed and second.closed
    failing=Mock();failing.recv.side_effect=RuntimeError('disconnected')
    bus=ChannelMux({'CAN1':failing})
    try:
        with pytest.raises(RuntimeError,match='disconnected'):
            bus.recv(.5)
    finally:bus.shutdown()


def test_script_routes_and_filters_explicit_physical_channels():
    from signal_explorer.scripting.transport import CanScriptTransport
    service=BusService();wire=LoopBus()
    selected=configs()
    try:
        with patch('signal_explorer.bus.service.can.Bus',return_value=wire):
            service.connect_bus(VECTOR,'0',500000,False,dict(selected[0],channels=selected),fd=True)
        transport=CanScriptTransport(service)
        transport.send(dict(arbitration_id=0x321,data=bytes(32).hex(),fd=True,brs=True,channel='CAN2'))
        assert wire.sent[-1].channel==1 and wire.sent[-1].bitrate_switch
        encoded=transport.encode(can.Message(channel=1,arbitration_id=0x321,data=bytes(32),is_fd=True))
        assert transport.matches(encoded,dict(channel='CAN2',arbitration_id=0x321))
        assert not transport.matches(encoded,dict(channel='CAN1',arbitration_id=0x321))
        with pytest.raises(RuntimeError):
            transport.send(dict(arbitration_id=0x321,data='00',channel='CAN3'))
    finally:service.disconnect_bus()
