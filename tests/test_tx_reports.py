import os
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
from copy import copy, deepcopy
import json
from queue import Queue, Empty
import time
from unittest.mock import patch

import can
from asammdf import MDF
import pytest
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt
from signal_explorer.bus.backends import BACKENDS
from signal_explorer.bus.frames import snapshot_message
from signal_explorer.bus.service import BusService
from signal_explorer.bus.timestamps import SessionClock, HardwareClock
from signal_explorer.recording.recorder import MF4Recorder
from signal_explorer.ui.main_window import MainWindow
from signal_explorer.ui.trace_model import TraceModel
from signal_explorer.scripting.transport import CanScriptTransport
from signal_explorer.scripting.runtime import CanFrame

app = QApplication.instance() or QApplication([])


def wait_for(predicate):
    end = time.monotonic()+4
    while time.monotonic() < end:
        app.processEvents()
        if predicate(): return True
        time.sleep(.005)
    return predicate()


class ReportBus:
    protocol = can.CanProtocol.CAN_FD
    def __init__(self):
        self.received = Queue()
        self.sent = []
    def recv(self, timeout=None):
        try: return self.received.get(timeout=timeout)
        except Empty: return None
    def send(self, message, timeout=None):
        self.sent.append(copy(message))
    def shutdown(self): pass


@pytest.mark.parametrize('interface,channel', [('vector','0'),('neovi','1'),('pcan','PCAN_USBBUS1'),('kvaser','0'),('nixnet','CAN1')])
def test_native_reports_only_no_synthetic_duplicates(interface, channel):
    service = BusService(); wire = ReportBus(); traffic = []
    service.traffic.connect(traffic.append)
    backend = next(b for b in BACKENDS if b.interface == interface)
    try:
        with patch('signal_explorer.bus.service.can.Bus', return_value=wire) as factory, \
             patch('signal_explorer.bus.service.DeviceLease'):
            service.connect_bus(backend, channel, 500000, False, fd=True, driver_tx_reports=True)
        assert service.is_connected
        assert factory.call_args.kwargs['receive_own_messages'] is True
        if interface == 'kvaser': assert factory.call_args.kwargs['single_handle'] is False
        accepted = service.send_message(can.Message(arbitration_id=0x321, data=bytes(12), is_fd=True,
                                                    bitrate_switch=True, is_extended_id=False), return_timestamp=True)
        assert isinstance(accepted, float) and not traffic
        assert len(wire.sent) == 1
        native_time = 1167609600 if interface == 'neovi' else time.time()
        report = copy(wire.sent[0]); report.is_rx = False; report.timestamp = native_time
        reply = copy(report); reply.is_rx = True; reply.arbitration_id = 0x221; reply.timestamp += .012
        wire.received.put(report); wire.received.put(reply)
        assert wait_for(lambda: len(traffic) == 2)
        assert [m.is_rx for m in traffic] == [False, True]
        assert abs(traffic[1].timestamp-traffic[0].timestamp-.012) < .00001
        assert traffic[0].raw_timestamp == native_time
        assert 'driver report' in traffic[0].tx_confirmation
        assert traffic[0].is_fd and traffic[0].bitrate_switch
        encoded = CanScriptTransport(service).encode(traffic[0])
        encoded.pop('protocol'); encoded['data'] = bytes.fromhex(encoded['data'])
        assert CanFrame(**encoded).timestamp_source == traffic[0].timestamp_source
        model = TraceModel(); model.append_messages(traffic)
        assert 'driver report' in model.index(0, 5).data(Qt.ItemDataRole.ToolTipRole)
        assert service.session_info['driver_tx_reports'] is True
    finally:
        service.disconnect_bus()


def test_default_fallback_is_single_estimate_and_rejects_driver_echo():
    service = BusService(); wire = ReportBus(); traffic = []
    service.traffic.connect(traffic.append)
    try:
        with patch('signal_explorer.bus.service.can.Bus', return_value=wire) as factory, \
             patch('signal_explorer.bus.service.DeviceLease'):
            service.connect_bus(next(b for b in BACKENDS if b.interface == 'vector'), '0', 500000, False)
        assert factory.call_args.kwargs['receive_own_messages'] is False
        accepted = service.send_message(can.Message(arbitration_id=0x123, data=[1]), return_timestamp=True)
        assert len(traffic) == 1 and traffic[0].timestamp == accepted
        assert traffic[0].tx_confirmation == 'local acceptance only'
        report = copy(wire.sent[0]); report.is_rx = False; report.timestamp = accepted
        assert service._capture_received(report) is None
        assert len(traffic) == 1
    finally: service.disconnect_bus()


def test_host_session_clock_ignores_wall_clock_changes_and_native_clock_shares_anchor():
    monotonic = [100.0]; wall = [1800000000.0]
    host = SessionClock(wall=lambda: wall[0], monotonic=lambda: monotonic[0])
    device = HardwareClock(host.now)
    assert device.to_unix(50) == host.now()
    wall[0] += 300
    monotonic[0] += .25
    assert host.now() == 1800000000.25
    assert device.to_unix(50.25) == host.now()
    monotonic[0] += 1
    assert device.to_unix(0) == host.now() and device.resets == 1


def test_provenance_and_payload_survive_snapshot_and_deepcopy():
    message = snapshot_message(can.Message(data=[1]))
    message.timestamp_source = 'driver epoch timestamp'; message.raw_timestamp = 42.0
    message.tx_confirmation = 'driver report'
    cloned = deepcopy(message); message.data[0] = 2
    assert bytes(cloned.data) == b'\x01' and cloned.raw_timestamp == 42
    assert cloned.timestamp_source == message.timestamp_source
    assert copy(message).tx_confirmation == 'driver report'


def test_recorder_reorders_small_arrival_skew_without_changing_timestamps(tmp_path):
    recorder = MF4Recorder(temp_directory=tmp_path)
    original = time.time()
    frames = [snapshot_message(can.Message(timestamp=original+gap, arbitration_id=0x100+i, data=[i],
                                            is_extended_id=False, is_rx=i != 1))
              for i, gap in enumerate((0, .03, .01, .04))]
    for frame in frames:
        frame.timestamp_source = 'driver epoch timestamp'
        frame.tx_confirmation = 'driver report'
    recorder.start(dict(driver_tx_reports=True, tx_semantics='Driver TX reports only'))
    for frame in frames: recorder.capture(frame)
    recorder.stop(); recorder._thread.join(8)
    assert not recorder.busy and not recorder.problem
    with can.MF4Reader(recorder.pending_path) as reader: recorded = list(reader)
    assert [m.arbitration_id for m in recorded] == [0x100,0x102,0x101,0x103]
    assert [m.timestamp for m in recorded] == sorted(m.timestamp for m in frames)
    with MDF(recorder.pending_path) as mdf: meta = json.loads(mdf.extract_attachment(0)[0])
    assert meta['timestamp_corrections'] == 0 and meta['timestamp_reordered_frames'] == 1
    assert meta['tx_semantics'] == 'Driver TX reports only'
    assert meta['timestamp_source_counts']['driver epoch timestamp'] == 4
    assert meta['tx_evidence_counts']['driver report'] == 1


def test_recorder_explains_large_clamps_and_invalid_stamps(tmp_path):
    recorder = MF4Recorder(temp_directory=tmp_path)
    now = time.time()
    recorder.start()
    for timestamp in (now, now-2, None):
        recorder.capture(can.Message(timestamp=timestamp, data=[1]))
    recorder.stop(); recorder._thread.join(8)
    assert not recorder.busy and not recorder.problem
    with MDF(recorder.pending_path) as mdf: meta = json.loads(mdf.extract_attachment(0)[0])
    assert meta['timestamp_corrections'] == 2
    assert meta['timestamp_correction_reasons'] == {'invalid':1, 'late_out_of_order':1}
    assert meta['timestamp_correction_samples'][0]['original'] == now-2


def test_gui_reports_option_is_opt_in_capability_checked_and_locked():
    with patch('signal_explorer.ui.main_window.discover_channels', return_value=([], 'No hardware scanned')):
        window = MainWindow()
        try:
            assert not window._driver_tx_reports.isChecked() and not window._driver_tx_reports.isEnabled()
            window._backend.setCurrentIndex(window._backend.findData('vector'))
            assert window._driver_tx_reports.isEnabled()
            window._driver_tx_reports.setChecked(True)
            window._backend.setCurrentIndex(window._backend.findData('virtual'))
            assert not window._driver_tx_reports.isChecked()
        finally: window.close()
