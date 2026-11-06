import os
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
import time
from threading import Event
from unittest.mock import patch
import can
import pytest
from PySide6.QtWidgets import QApplication
from signal_explorer.bus.backends import BACKENDS
from signal_explorer.bus.service import BusService
from signal_explorer.bus.timing import TimingSettings
from signal_explorer.bus.bitrate_scan import scan_rates
from signal_explorer.bus.transmit import TransmitEngine, TxEntry
from signal_explorer.ui.main_window import MainWindow
from signal_explorer.bus.listen_only import open_bus
from types import SimpleNamespace
from unittest.mock import Mock

app = QApplication.instance() or QApplication([])


@pytest.mark.parametrize('backend', ['vector','kvaser','pcan','nixnet'])
@pytest.mark.parametrize('fd', [False,True])
def test_custom_timing_calculates_exact_rates_and_requested_sjw(backend,fd):
    request = TimingSettings(500000,2000000,True,80,2,75,2)
    timing = request.build(backend,fd)
    if fd:
        assert timing.nom_bitrate==500000 and timing.data_bitrate==2000000
        assert timing.nom_sjw==2 and timing.data_sjw==2
    else:
        assert timing.bitrate==500000 and timing.sjw==2


@pytest.mark.parametrize('timing_request', [TimingSettings(0),TimingSettings(2000000),
    TimingSettings(500000,250000,True),TimingSettings(500000,2000000,True,80,10000)])
def test_invalid_timing_rejected(timing_request):
    with pytest.raises(ValueError): timing_request.build('vector',True)


def test_listen_only_blocks_shared_send_sender_and_replay_but_preserves_rx():
    service=BusService(); seen=[]; service.traffic.connect(seen.append)
    virtual=next(b for b in BACKENDS if b.interface=='virtual')
    service.connect_bus(virtual,'silent-test',500000,False,listen_only=True)
    peer=can.Bus(interface='virtual',channel='silent-test',ignore_config=True)
    engine=TransmitEngine(service)
    try:
        assert service.is_connected and not service.can_send and not service.can_replay
        assert service.session_info['listen_only'] is True
        assert service.send_message(can.Message(arbitration_id=1,data=[1]),False) is False
        assert peer.recv(.02) is None
        engine.add(TxEntry())
        with pytest.raises(ValueError,match='listen-only'): engine.start([engine.entries()[0].uid])
        peer.send(can.Message(arbitration_id=2,data=[2]))
        end=time.monotonic()+1
        while not seen and time.monotonic()<end: app.processEvents();time.sleep(.005)
        assert len(seen)==1 and seen[0].is_rx
    finally:
        engine.shutdown();peer.shutdown();service.disconnect_bus()


@pytest.mark.parametrize('interface', ['neovi','slcan','gs_usb'])
def test_unsupported_silent_setup_fails_before_any_open(interface):
    service=BusService(); errors=[];service.error.connect(errors.append)
    with patch('signal_explorer.bus.service.open_bus') as factory:
        service.connect_bus(next(b for b in BACKENDS if b.interface==interface),'0',500000,False,listen_only=True)
    assert not service.is_connected and errors and not factory.called


def test_scan_requires_unique_passive_traffic_and_cleans_up_on_cancel():
    opened=[]; cancelled=Event()
    class FakeBus:
        def __init__(self, rate):self.rate=rate;self.closed=False
        def recv(self,timeout):
            if not timeout:return None
            time.sleep(.01)
            if self.rate==500000:return can.Message(arbitration_id=1,data=[1])
        def shutdown(self):self.closed=True
        def send(self,*args,**kwargs):raise AssertionError('Scanner must never send')
    def factory(**kwargs):
        assert kwargs['monitor_only'] and not kwargs['receive_own_messages']
        bus=FakeBus(kwargs['bitrate']);opened.append(bus);return bus
    found=scan_rates(dict(interface='vector',channel=0),[250000,500000],.1,5,cancelled,lambda text:None,factory)
    assert found==[500000] and all(bus.closed for bus in opened)
    def cancel_report(text):cancelled.set()
    assert scan_rates(dict(interface='vector',channel=0),[250000,500000],.1,5,cancelled,cancel_report,factory)==[]
    assert all(bus.closed for bus in opened)


def test_failed_scan_closes_bus_and_never_falls_back_to_active():
    class Broken:
        closed=False
        def recv(self,timeout):raise RuntimeError('receive error')
        def shutdown(self):self.closed=True
    bus=Broken()
    with pytest.raises(RuntimeError,match='receive error'):
        scan_rates(dict(interface='pcan'),[500000],.1,5,Event(),lambda text:None,lambda **kwargs:bus)
    assert bus.closed


def test_timing_window_applies_custom_profile_without_main_editor_clutter():
    with patch('signal_explorer.ui.main_window.discover_channels',return_value=([],'')):
        window=MainWindow()
        try:
            window._backend.setCurrentIndex(window._backend.findData('vector'))
            timing=window._open_timing_window()
            timing.custom.setChecked(True);timing.rate.setValue(100000);timing.advanced.setChecked(True)
            timing.apply()
            assert window._bitrate.currentData()==100000 and not window._bitrate.isEnabled()
            assert window._timing_settings['vector'].advanced
            assert window._listen_only.isEnabled()
            window._backend.setCurrentIndex(window._backend.findData('virtual'))
            assert window._timing_settings.get('virtual') is None
            assert not timing.scan_button.isEnabled()
            timing.custom.setChecked(False);timing.apply()
        finally: window.close()


def test_vector_without_initialization_access_never_activates():
    class Vector:
        online=False
        def __init__(self,**kwargs):
            self.permission_mask=1; self.mask=3; self.port_handle=42; self.xldriver=Mock()
            self._set_output_mode(self.mask,True)
            Vector.online=True
        def _set_output_mode(self,*args):raise AssertionError('Must reject before driver setup')
    with patch('can.interfaces.vector.canlib.VectorBus',Vector):
        with pytest.raises(can.CanInitializationError,match='initialization access'):
            open_bus(monitor_only=True,interface='vector',channel=[0,1])
    assert not Vector.online


def test_pcan_checks_preset_status_before_initialize():
    class Pcan:
        online=False
        @property
        def state(self):return self._state
        @state.setter
        def state(self,value):pass
        def __init__(self,state,**kwargs):
            self.m_PcanHandle=1;self.m_objPCANBasic=SimpleNamespace(SetValue=lambda *args:1)
            self.state=state
            Pcan.online=True
    with patch('can.interfaces.pcan.pcan.PcanBus',Pcan):
        with pytest.raises(can.CanInitializationError,match='preset'):
            open_bus(monitor_only=True,interface='pcan',channel='PCAN_USBBUS1')
    assert not Pcan.online


def test_kvaser_capability_rejection_occurs_before_open():
    with patch('can.interfaces.kvaser.canlib.canGetChannelData',lambda *args:None,create=True), \
         patch('signal_explorer.bus.listen_only.can.Bus') as factory:
        with pytest.raises(can.CanInitializationError,match='silent-mode capability'):
            open_bus(monitor_only=True,interface='kvaser',channel=0)
        factory.assert_not_called()


def test_ni_readback_failure_closes_session():
    bus=SimpleNamespace(_interface=SimpleNamespace(can_lstn_only=False),shutdown=Mock())
    with patch('signal_explorer.bus.listen_only.can.Bus',return_value=bus) as factory:
        with pytest.raises(can.CanInitializationError,match='confirm'):
            open_bus(monitor_only=True,interface='nixnet',channel='CAN1')
        assert factory.call_args.kwargs['listen_only'] is True
    bus.shutdown.assert_called_once()
    class Unreadable:
        @property
        def can_lstn_only(self): raise RuntimeError('property read failed')
    bus=SimpleNamespace(_interface=Unreadable(),shutdown=Mock())
    with patch('signal_explorer.bus.listen_only.can.Bus',return_value=bus):
        with pytest.raises(RuntimeError,match='property read failed'):
            open_bus(monitor_only=True,interface='nixnet',channel='CAN1')
    bus.shutdown.assert_called_once()
