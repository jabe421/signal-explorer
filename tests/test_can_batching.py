import os
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
from copy import deepcopy
import time
from unittest.mock import patch
from uuid import uuid4

import can
import cantools
from PySide6.QtCore import QPersistentModelIndex, Qt
from PySide6.QtTest import QAbstractItemModelTester
from PySide6.QtWidgets import QApplication

from signal_explorer.bus.display_queue import DisplayQueue
from signal_explorer.bus.backends import BACKENDS
from signal_explorer.bus.service import BusService, _Reader
from signal_explorer.recording.recorder import MF4Recorder
from signal_explorer.ui.main_window import MainWindow
from signal_explorer.ui.trace_model import SignalTreeModel, TraceModel, IdFilter

app = QApplication.instance() or QApplication([])


def frame(value=0, channel=0, identifier=0x200, **flags):
    return can.Message(arbitration_id=identifier, is_extended_id=False, channel=channel,
                       timestamp=1000+value*.001, data=bytes([value % 256])+bytes(7), **flags)


def test_batch_ranges_retention_counts_channels_and_mode_switches():
    model = SignalTreeModel(history_mode=True, max_history=7)
    model.set_database(cantools.database.load_file('examples/demo.dbc'))
    tester = QAbstractItemModelTester(model, QAbstractItemModelTester.FailureReportingMode.Fatal)
    inserted, removed = [], []
    model.rowsInserted.connect(lambda parent, first, last: inserted.append((parent.isValid(), first, last)))
    model.rowsRemoved.connect(lambda parent, first, last: removed.append((parent.isValid(), first, last)))
    model.append_messages([frame(i, i % 2) for i in range(5)])
    child = QPersistentModelIndex(model.index(0, 9, model.index(4, 0)))
    model.append_messages([frame(i, i % 2) for i in range(5, 10)])
    assert inserted == [(False, 0, 4), (False, 2, 6)]
    assert removed == [(False, 0, 2)]
    assert child.parent().row() == 1
    assert model.parent(model.index(0, 0, model.index(1, 0))).row() == 1
    assert [model.index(i, 0).data() for i in range(7)] == [f'{1000+i*.001:.6f}' for i in range(3, 10)]
    model.append_messages([frame(i, i % 2) for i in range(10, 30)])
    assert model.rowCount() == 7 and not child.isValid()
    model.set_history_mode(False)
    assert model.rowCount() == 2
    assert [model.index(i, 1).data() for i in range(2)] == ['15', '15']
    assert [model.index(i, 7).data().split()[0] for i in range(2)] == ['1C', '1D']
    proxy = IdFilter(); proxy.setSourceModel(model); proxy.set_expression('200')
    assert proxy.rowCount() == 2
    model.clear()
    assert model.rowCount() == 0
    model.set_history_mode(True)
    model.append_messages([frame()])
    assert model.parent(model.index(0, 0, model.index(0, 0))).row() == 0


def test_dbc_is_lazy_cached_shared_with_children_and_invalidated():
    model = SignalTreeModel(history_mode=False)
    database = cantools.database.load_file('examples/demo.dbc')
    model.set_database(database)
    definition = database.get_message_by_frame_id(0x200)
    with patch.object(definition, 'decode', wraps=definition.decode) as decode:
        model.append_messages([frame(i) for i in range(500)])
        for column in (0, 1, 2, 3, 4, 5, 6, 7, 10, 11, 12):
            model.index(0, column).data()
        assert decode.call_count == 0
        parent = model.index(0, 0)
        for _ in range(3):
            for column in range(model.columnCount()):
                model.index(0, column).data()
            for row in range(model.rowCount(parent)):
                model.index(row, 9, parent).data()
        assert decode.call_count == 1
        model.append_messages([frame(30)])
        model.index(0, 9).data()
        assert decode.call_count == 2
        model.set_database(None)
        assert model.index(0, 8).data() == ''
        model.set_database(database)
        model.index(0, 9).data()
        assert decode.call_count == 3
        bad = frame(); bad.data = bytearray([1]); bad.dlc = 1
        model.append_messages([bad])
        assert 'Decode error' in model.index(0, 9).data()
        assert 'Decode error' in model.index(0, 9, model.index(0, 0)).data()
        assert decode.call_count == 4


def test_expanded_latest_frame_and_remote_child_structure():
    model = SignalTreeModel(history_mode=False)
    model.set_database(cantools.database.load_file('examples/demo.dbc'))
    tester = QAbstractItemModelTester(model, QAbstractItemModelTester.FailureReportingMode.Fatal)
    model.append_messages([frame()])
    parent = model.index(0, 0)
    model.set_expanded(parent, True)
    child = QPersistentModelIndex(model.index(0, 9, parent))
    model.append_messages([frame(40), frame(80)])
    assert child.parent() == parent and child.data() == '20.0 rpm'
    assert model.index(0, 9).data() == ''
    remote = frame(); remote.is_remote_frame = True; remote.data = bytearray()
    model.append_messages([remote])
    assert not child.isValid() and model.rowCount(parent) == 0
    model.append_messages([frame(100)])
    assert model.rowCount(parent) == 4
    assert 'EngineRPM' in model.index(0, 9).data()


def test_standard_extended_lookup_and_input_snapshot():
    database = cantools.database.load_file('examples/demo.dbc')
    extended = deepcopy(database.get_message_by_frame_id(0x200))
    extended.name = 'ExtendedEngine'; extended.is_extended_frame = True
    database.messages.append(extended)
    model = TraceModel(history_mode=False); model.set_database(database)
    original = frame(); other = frame(); other.is_extended_id = True
    model.append_messages([original, other])
    original.data[0] = 123
    assert model.index(0, 7).data().startswith('00')
    assert model.index(0, 8).data() == 'EngineData'
    assert model.index(1, 8).data() == 'ExtendedEngine'


def test_queue_overload_is_bounded_and_raw_traffic_precedes_display():
    service = BusService(); service._display = DisplayQueue(capacity=100)
    raw, batches = [], []
    service.traffic.connect(raw.append)
    service.messages.connect(batches.append)
    original = frame()
    for value in range(5000):
        original.data[0] = value % 256
        service._capture_received(original)
    assert len(raw) == 5000 and not batches
    assert service.ui_dropped == 4900
    service._flush_display()
    assert len(batches) == 1 and len(batches[0]) == 100
    assert raw[0].data[0] == 0 and raw[1].data[0] == 1
    assert batches[0][0] is raw[4900]
    assert batches[0][-1] is raw[-1]


def test_pause_resume_and_clear_discard_pending_display_not_raw_events():
    window = MainWindow(); raw = []
    window._service.traffic.connect(raw.append)
    try:
        window._set_exploring(True)
        with patch.object(window._service.__class__, 'is_connected', new=property(lambda _: True)):
            window._service._capture_received(frame(1))
            window._toggle_exploring()
            window._service._capture_received(frame(2))
            window._toggle_exploring()
            window._service._capture_received(frame(3))
            window._service._flush_display()
        assert len(raw) == 3 and window._rx_count == 1
        assert window._model.index(0, 7).data().startswith('03')
        window._service._capture_received(frame(4))
        window._clear_trace()
        window._service._capture_received(frame(5))
        window._service._flush_display()
        assert len(raw) == 5 and window._rx_count == 1
        assert window._model.index(0, 7).data().startswith('05')
    finally:
        window.close()


def test_ui_refresh_is_per_batch_and_counts_are_throttled():
    window = MainWindow()
    try:
        window._set_exploring(True)
        window._auto_scroll.setChecked(True)
        with patch.object(window._table, 'scrollToBottom') as scroll, patch.object(window._count, 'setText') as text:
            for _ in range(3):
                window._on_messages([frame(i) for i in range(500)])
            assert scroll.call_count == 3
            assert text.call_count == 0 and window._rx_count == 1500
            window._refresh_counts()
            assert text.call_count == 1
            window._refresh_counts()
            assert text.call_count == 1
    finally:
        window.close()


def test_reader_captures_without_per_frame_ui_signals():
    frames = [frame(i) for i in range(200)]
    class BurstBus:
        def recv(self, timeout):
            if not frames:
                reader.requestInterruption()
                return None
            return frames.pop(0)
    captured = []
    reader = _Reader(BurstBus(), captured.append)
    reader.start()
    assert reader.wait(3000)
    assert len(captured) == 200


def test_virtual_burst_batches_ui_and_records_every_frame(tmp_path):
    window = MainWindow()
    recorder = MF4Recorder(temp_directory=tmp_path)
    channel = 'batch-burst-'+uuid4().hex
    peer = can.Bus(interface='virtual', channel=channel, ignore_config=True)
    sizes = []
    window._service.messages.connect(lambda frames: sizes.append(len(frames)))
    window._service.traffic.connect(recorder.capture, Qt.ConnectionType.DirectConnection)
    try:
        window._auto_scroll.setChecked(True)
        window._model.max_history = 128
        window._service.connect_bus(next(b for b in BACKENDS if b.interface == 'virtual'),
                                    channel, 500000, False)
        recorder.start(window._service.session_info)
        for value in range(4000):
            peer.send(frame(value, identifier=0x200+value % 4))
        deadline = time.monotonic()+10
        while window._rx_count < 4000 and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(.002)
        assert window._rx_count == 4000 and window._tx_count == 0
        assert sum(sizes) == 4000 and len(sizes) < 4000
        assert max(sizes) <= 2000 and window._service.ui_dropped == 0
        assert window._model.rowCount() == 128
        recorder.stop(); recorder._thread.join(10)
        assert not recorder.busy and not recorder.problem
        with can.MF4Reader(recorder.pending_path) as reader:
            recorded = list(reader)
        assert len(recorded) == 4000
        assert [bytes(msg.data) for msg in recorded] == [bytes(frame(i).data) for i in range(4000)]
        window._auto_scroll.setChecked(False)
        assert window._model.rowCount() == 4
        assert [window._model.index(row, 1).data() for row in range(4)] == ['1000']*4
    finally:
        recorder.stop()
        if recorder._thread:
            recorder._thread.join(10)
        window.close()
        peer.shutdown()
