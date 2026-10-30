"""Exercise real python-can NI conversion with fake native sessions, never hardware."""
import os
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
from enum import Enum, auto
from types import SimpleNamespace as NS, ModuleType
import sys
import time
from unittest.mock import Mock, patch

import can
import pytest
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt
from signal_explorer.recording.recorder import MF4Recorder
from signal_explorer.bus.backends import BACKENDS
from signal_explorer.bus.discovery import discover_channels, ChannelOption
from signal_explorer.bus.service import BusService
from signal_explorer.ui.main_window import MainWindow

app = QApplication.instance() or QApplication([])
NI = next(b for b in BACKENDS if b.key == 'nixnet')


class FrameType(Enum):
    CAN_DATA = auto()
    CAN_REMOTE = auto()
    CAN_BUS_ERROR = auto()
    CANFD_DATA = auto()
    CANFDBRS_DATA = auto()


def native_api():
    api = ModuleType('nixnet')
    api.constants = NS(CanTcvrCap=NS(HS='HS', LS='LS'), CanTermCap=NS(YES=True),
                       CanTerm=NS(ON='on', OFF='off'), FrameType=FrameType,
                       TIMEOUT_INFINITE=-1)
    api.errors = NS(XnetError=type('XnetError', (Exception,), {}))
    api.types = NS(CanIdentifier=lambda identifier, extended: NS(identifier=identifier, extended=extended),
                   CanFrame=lambda identifier, **kw: NS(identifier=identifier, **kw))
    tx = Mock(intf=NS())
    rx = Mock()
    rx.frames.read.return_value = []
    api.session = NS(FrameOutStreamSession=Mock(return_value=tx),
                     FrameInStreamSession=Mock(return_value=rx))
    return api, tx, rx


class Port:
    def __init__(self, name, num, termination=True, cap='HS'):
        self.name, self.port_num = name, num
        self.can_term_cap, self.can_tcvr_cap = termination, cap

    def __str__(self):
        return self.name


def test_discovery_ports_capabilities_and_no_sessions():
    api, tx, rx = native_api()
    ports = [Port('CAN7', 1), Port('CAN8', 2, False), Port('LIN1', 3, cap='LS')]
    system = Mock()
    system.__enter__ = Mock(return_value=NS(
        dev_refs=[NS(product_name='USB-8502', ser_num=1234, intf_refs=ports)],
        intf_refs_can=ports[:2]))
    system.__exit__ = Mock(return_value=False)
    module = ModuleType('nixnet.system')
    module.System = Mock(return_value=system)
    with patch.dict(sys.modules, {'nixnet': api, 'nixnet.system': module}):
        options, note = discover_channels('nixnet')
    assert [o.config['channel'] for o in options] == ['CAN7', 'CAN8']
    assert options[0].config['supports_fd']
    assert not options[1].config['can_term_available']
    assert 'USB-8502' in options[0].label and '1234' in options[0].label
    assert '2 NI-XNET' in note
    api.session.FrameOutStreamSession.assert_not_called()
    api.session.FrameInStreamSession.assert_not_called()
    system.__exit__.assert_called_once()


def test_missing_package_driver_and_empty_scan_are_distinct():
    with patch.dict(sys.modules, {'nixnet': None}):
        options, note = discover_channels('nixnet')
    assert not options and '.[nixnet]' in note
    api, _, _ = native_api()
    module = ModuleType('nixnet.system')
    module.System = Mock(side_effect=OSError('nixnet.dll missing'))
    with patch.dict(sys.modules, {'nixnet': api, 'nixnet.system': module}):
        options, note = discover_channels('nixnet')
        assert not options and 'nixnet.dll missing' in note and 'NI MAX' in note
        module.System.side_effect = None
        module.System.return_value = Mock()
        module.System.return_value.__enter__ = Mock(return_value=NS(dev_refs=[], intf_refs_can=[]))
        module.System.return_value.__exit__ = Mock()
        options, note = discover_channels('nixnet')
    assert not options and 'API loaded; no' in note


@pytest.mark.parametrize('fd,termination', [(False, False), (True, False), (True, True)])
def test_factory_mapping_and_missing_driver_message(fd, termination):
    service = BusService()
    errors = []
    service.error.connect(errors.append)
    with patch('signal_explorer.bus.service.can.Bus', side_effect=RuntimeError('missing driver')) as factory:
        service.connect_bus(NI, 'CAN7', 250000, False,
                            dict(can_termination=termination), fd=fd, data_bitrate=2000000)
    args = factory.call_args.kwargs
    assert args['channel'] == 'CAN7' and args['bitrate'] == 250000
    assert args['can_termination'] == termination
    assert not args['receive_own_messages'] and args['ignore_config']
    assert 'data_bitrate' not in args
    assert args.get('fd_bitrate') == (2000000 if fd else None)
    assert not service.is_connected and 'NI MAX' in errors[0]


def test_ui_channel_and_termination_forwarding():
    window = MainWindow()
    try:
        options = [ChannelOption('USB-8502 | CAN7', dict(channel='CAN7', can_term_available=True)),
                   ChannelOption('NI | CAN8', dict(channel='CAN8', can_term_available=False))]
        with patch('signal_explorer.ui.main_window.discover_channels', return_value=(options, 'Found')):
            window._backend.setCurrentIndex(window._backend.findData('nixnet'))
        assert window._channel_value() == 'CAN7'
        assert window._fd.isEnabled() and not window._termination.isChecked()
        window._termination.setChecked(True)
        with patch.object(window._service, 'connect_bus') as connect:
            window._toggle_connection()
        assert connect.call_args.kwargs['device_config']['can_termination']
        window._channel.setCurrentIndex(1)
        assert not window._termination.isEnabled() and not window._termination.isChecked()
        window._channel.setEditText('CAN12')
        assert window._channel_value() == 'CAN12' and window._termination.isEnabled()
        window._backend.setCurrentIndex(window._backend.findData('virtual'))
        assert window._termination.isHidden()
    finally:
        window.close()


@pytest.mark.parametrize('config,fd', [
    (dict(can_term_available=False, can_termination=True), False),
    (dict(supports_fd=False), True),
])
def test_unavailable_capabilities_fail_before_native_open(config, fd):
    service = BusService()
    errors = []
    service.error.connect(errors.append)
    with patch('signal_explorer.bus.service.can.Bus') as factory:
        service.connect_bus(NI, 'CAN1', 500000, False, config, fd=fd)
    factory.assert_not_called()
    assert errors and not service.is_connected


@pytest.mark.parametrize('fd', [False, True])
def test_real_python_can_native_session_receive_send_and_close(fd):
    from can.interfaces import nixnet as backend
    api, tx, rx = native_api()
    epoch = 1793376000.25
    payload = bytes(range(12 if fd else 8))
    frame = NS(timestamp=int((epoch + 11644473600) * 10000000),
               type=FrameType.CANFDBRS_DATA if fd else FrameType.CAN_DATA,
               identifier=NS(identifier=0x321, extended=False), payload=payload, echo=False)
    rx.frames.read.side_effect = [[frame], []]
    with patch.object(backend, 'nixnet', api):
        bus = backend.NiXNETcanBus(channel='CAN7', bitrate=500000,
                                  fd=fd, fd_bitrate=2000000, can_termination=True, _testing=True)
        try:
            msg = bus.recv(.1)
            assert msg.timestamp == pytest.approx(epoch, abs=.00001)
            assert msg.channel == 'CAN7' and msg.is_rx and msg.data == payload
            assert msg.is_fd == fd and msg.bitrate_switch == fd
            assert tx.intf.baud_rate == 500000 and tx.intf.can_term == 'on'
            if fd:
                assert tx.intf.can_fd_baud_rate == 2000000
            bus.send(msg, timeout=.2)
            native = tx.frames.write.call_args.args[0][0]
            assert native.payload == payload and native.identifier.identifier == 0x321
            assert native.type == frame.type
            assert api.session.FrameOutStreamSession.call_args.kwargs['database_name'] == (':can_fd_brs:' if fd else ':memory:')
        finally:
            bus.shutdown()
    tx.close.assert_called_once()
    rx.close.assert_called_once()


def test_service_stream_and_tx_share_host_epoch_and_disable_demo(tmp_path):
    """Exercise the normal traffic path used by recorder/scripts, using a virtual wire."""
    wire = can.Bus(interface='virtual', channel='ni-mock', ignore_config=True)
    peer = can.Bus(interface='virtual', channel='ni-mock', ignore_config=True)
    service = BusService()
    traffic = []
    service.traffic.connect(traffic.append)
    recorder = MF4Recorder(temp_directory=tmp_path)
    try:
        with patch('signal_explorer.bus.service.can.Bus', return_value=wire):
            service.connect_bus(NI, 'CAN7', 500000, True, fd=True)
        assert service.is_connected and service._demo_bus is None
        assert service._rx_clock is None
        recorder.start(service.session_info)
        service.traffic.connect(recorder.capture, Qt.ConnectionType.DirectConnection)
        peer.send(can.Message(arbitration_id=0x221, data=bytes(12), is_fd=True, bitrate_switch=True))
        deadline = time.monotonic() + 2
        while not traffic and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(.01)
        assert traffic and traffic[0].is_rx
        assert abs(traffic[0].timestamp - time.time()) < 5
        assert service.send_message(can.Message(arbitration_id=0x321, data=bytes(12), is_fd=True, bitrate_switch=True))
        assert not traffic[-1].is_rx and traffic[-1].channel == 'CAN7'
        assert abs(traffic[-1].timestamp - traffic[0].timestamp) < 5
        assert peer.recv(.2).arbitration_id == 0x321
        assert not service.session_info['can_termination']
    finally:
        service.disconnect_bus()
        peer.shutdown()
        recorder.stop()
        if recorder._thread:
            recorder._thread.join(10)
    assert not service.is_connected
    assert not recorder.problem and recorder.pending_path
    with can.MF4Reader(recorder.pending_path) as reader:
        saved = list(reader)
    assert [m.is_rx for m in saved] == [True, False]
    assert all(m.is_fd and m.bitrate_switch and len(m.data) == 12 for m in saved)
    assert all(m.channel == 1 for m in saved)
    assert saved[1].timestamp - saved[0].timestamp < 5
