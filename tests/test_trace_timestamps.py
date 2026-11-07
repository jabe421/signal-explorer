"""Display-clock changes must not alter capture, ordering or stored timestamps."""

import os

os.environ["QT_QPA_PLATFORM"] = "offscreen"

from datetime import datetime, timezone
import time
from unittest.mock import patch
import uuid

import can
import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from signal_explorer.bus.backends import BACKENDS
from signal_explorer.ui.main_window import MainWindow
from signal_explorer.ui.trace_model import SignalTreeModel, IdFilter
from signal_explorer.ui.trace_timestamps import TIMESTAMP_FORMATS, format_timestamp

app = QApplication.instance() or QApplication([])
BASE = datetime(2026, 11, 6, 17, 0, tzinfo=timezone.utc).timestamp()


def frame(offset, identifier=0x100, channel=0, extended=False, rx=True):
    return can.Message(
        timestamp=BASE + offset,
        arbitration_id=identifier,
        channel=channel,
        is_extended_id=extended,
        is_rx=rx,
        data=[1, 2],
    )


def displayed(model, mode):
    model.set_timestamp_mode(mode)
    return [model.index(i, 0).data() for i in range(model.rowCount())]


def wait_for(predicate, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return True
        time.sleep(0.005)
    return False


@pytest.mark.parametrize("history", [True, False], ids=["history", "latest-per-ID"])
def test_all_formats_and_changes_preserve_payload_and_raw_timestamp(history):
    model = SignalTreeModel(history_mode=history)
    model.start_session(BASE)
    original = frame(1.125)
    model.append_message(original)
    expected = {
        "epoch": f"{BASE + 1.125:.6f}",
        "absolute": "2026-11-06 17:00:01.125000Z",
        "relative": "1.125000",
        "delta": "n/a",
        "delta_id": "n/a",
    }
    for mode, text in expected.items():
        assert displayed(model, mode) == [text]
        assert model.index(0, 7).data() == "01 02"
        assert str(BASE + 1.125) in model.index(0, 0).data(Qt.ItemDataRole.ToolTipRole)
    assert original.timestamp == BASE + 1.125 and original.data == bytearray([1, 2])
    assert model._rows[0].frame.timestamp == original.timestamp


def test_deltas_across_batches_channels_formats_and_directions_survive_trimming():
    model = SignalTreeModel(history_mode=True, max_history=4)
    model.start_session(BASE)
    frames = [
        frame(1),
        frame(1.25, 0x200),
        frame(1.5, channel=1),
        frame(2, rx=False),
        frame(2.25, extended=True),
        frame(3),
    ]
    model.append_messages(frames[:3])
    model.append_messages(frames[3:])
    assert displayed(model, "delta") == ["0.250000", "0.500000", "0.250000", "0.750000"]
    assert displayed(model, "delta_id") == ["n/a", "1.000000", "n/a", "1.000000"]
    model.set_history_mode(False)
    assert displayed(model, "relative") == [
        "3.000000",
        "1.250000",
        "1.500000",
        "2.250000",
    ]
    assert displayed(model, "delta") == ["0.750000", "0.250000", "0.250000", "0.250000"]
    assert displayed(model, "delta_id") == ["1.000000", "n/a", "n/a", "n/a"]
    assert (
        model.index(0, 1).data() == "3"
    )  # Includes RX and TX, keeping existing counts.
    model.set_history_mode(True)
    assert displayed(model, "delta_id") == ["n/a", "1.000000", "n/a", "1.000000"]


@pytest.mark.parametrize("history", [True, False])
def test_id_filters_do_not_change_timing_baselines(history):
    model = SignalTreeModel(history_mode=history)
    model.append_messages([frame(1), frame(1.5, 0x200), frame(2), frame(2.5, 0x200)])
    proxy = IdFilter()
    proxy.setSourceModel(model)
    model.set_timestamp_mode("delta")
    proxy.set_expression("100")
    assert proxy.index(proxy.rowCount() - 1, 0).data() == "0.500000"
    model.set_timestamp_mode("delta_id")
    assert proxy.index(proxy.rowCount() - 1, 0).data() == "1.000000"
    proxy.set_expression("")
    assert proxy.index(proxy.rowCount() - 1, 0).data() == "1.000000"


def test_clear_reconnect_and_retained_rows_use_correct_connection_origins():
    model = SignalTreeModel(history_mode=True)
    model.start_session(BASE)
    model.append_messages([frame(1), frame(2)])
    model.clear()
    model.append_message(frame(10))
    assert displayed(model, "relative") == ["10.000000"]
    assert displayed(model, "delta_id") == ["n/a"]
    model.start_session(BASE + 100)
    model.append_message(frame(102))
    assert displayed(model, "relative") == ["10.000000", "2.000000"]
    assert displayed(model, "delta") == ["n/a", "n/a"]
    # A model without a connection anchor uses its first valid monitored frame.
    standalone = SignalTreeModel()
    standalone.append_messages([frame(1), frame(2)])
    assert displayed(standalone, "relative") == ["0.000000", "1.000000"]
    standalone.clear()
    standalone.append_message(frame(100))
    assert displayed(standalone, "relative") == ["0.000000"]


@pytest.mark.parametrize("invalid", [None, float("nan"), float("inf")])
def test_invalid_times_do_not_poison_the_next_baseline(invalid):
    model = SignalTreeModel()
    bad = frame(0)
    bad.timestamp = invalid
    model.append_messages([bad, frame(1), frame(2)])
    assert displayed(model, "relative") == ["n/a", "0.000000", "1.000000"]
    assert displayed(model, "delta") == ["n/a", "n/a", "1.000000"]
    assert displayed(model, "delta_id") == ["n/a", "n/a", "1.000000"]
    assert displayed(model, "absolute")[0] == "n/a"
    with pytest.raises(ValueError, match="finite"):
        model.start_session(invalid)


def test_negative_deltas_and_unrepresentable_dates_are_not_silently_corrected():
    model = SignalTreeModel()
    model.append_messages([frame(2), frame(1.75)])
    assert displayed(model, "delta") == ["n/a", "-0.250000"]
    assert displayed(model, "delta_id") == ["n/a", "-0.250000"]
    assert model._rows[1].frame.timestamp == BASE + 1.75
    assert format_timestamp("absolute", 1e30, None, None, None) == "n/a"
    with pytest.raises(ValueError, match="Unknown"):
        model.set_timestamp_mode("invented")


def test_time_format_is_cached_per_record_and_invalidated_without_model_reset():
    model = SignalTreeModel(history_mode=False)
    model.append_message(frame(1))
    resets = []
    model.modelReset.connect(lambda: resets.append(True))
    with patch(
        "signal_explorer.ui.trace_model.format_timestamp", wraps=format_timestamp
    ) as formatter:
        for _ in range(20):
            model.index(0, 0).data()
        assert formatter.call_count == 1
        model.set_timestamp_mode("absolute")
        for _ in range(20):
            model.index(0, 0).data()
        assert formatter.call_count == 2
        model.append_message(frame(2))
        assert model.index(0, 0).data().endswith("02.000000Z")
        assert formatter.call_count == 3
    assert not resets


def test_expanded_dbc_signal_names_and_values_survive_format_changes():
    import cantools

    model = SignalTreeModel(history_mode=False)
    model.set_database(cantools.database.load_file("examples/demo.dbc"))
    message = frame(1)
    message.data = bytearray([5, 65, 0, 0, 0, 0, 0, 0])
    message.dlc = 8
    model.append_message(message)
    parent = model.index(0, 0)
    model.set_expanded(parent, True)
    child = model.index(0, 0, parent)
    name = child.data()
    for mode, *_ in TIMESTAMP_FORMATS:
        model.set_timestamp_mode(mode)
        assert child.data() == name and model.parent(child) == parent
        assert model.index(0, 9).data() == ""


def test_menu_pause_clear_reconnect_and_recording_keep_original_times(tmp_path):
    window = MainWindow()
    panel = window._recording_panel
    panel.recorder.temp_directory = tmp_path
    output = tmp_path / "timestamp-display.mf4"
    with patch(
        "signal_explorer.ui.recording_panel.QFileDialog.getSaveFileName",
        return_value=(str(output), ""),
    ):
        try:
            window._service.connect_bus(BACKENDS[0], str(uuid.uuid4()), 500000, False)
            anchor = window._service.session_started_at
            assert window._model._session_start == anchor
            panel.log.click()
            messages = [frame(1), frame(2)]
            for message in messages:
                window._service.traffic.emit(message)
                window._on_message(message)
            trace = next(
                action.menu()
                for action in window.menuBar().actions()
                if action.text() == "Trace"
            )
            menu = trace.actions()[0].menu()
            assert menu.title() == "Timestamp format"
            for action in menu.actions():
                action.trigger()
                assert action.isChecked()
                assert sum(a.isChecked() for a in menu.actions()) == 1
                assert window._model.timestamp_mode == action.data()
                assert window._service.is_connected
            assert window._model.timestamp_mode == "delta_id"
            assert window._model.index(0, 0).data() == "1.000000"
            # Paused traffic still reaches the recorder, but not the monitor.
            window._explore.click()
            window._service.traffic.emit(frame(3))
            window._on_message(frame(3))
            window._explore.click()
            window._service.traffic.emit(frame(5))
            window._on_message(frame(5))
            assert window._model.index(0, 0).data() == "3.000000"
            window._set_timestamp_format("relative")
            window._clear_trace()
            window._on_message(frame(6))
            assert float(window._model.index(0, 0).data()) == pytest.approx(
                BASE + 6 - anchor, abs=1e-6
            )
            panel.log.click()
            assert wait_for(lambda: output.exists())
            with can.MF4Reader(output) as reader:
                saved = list(reader)
            assert [m.timestamp for m in saved] == pytest.approx(
                [BASE + 1, BASE + 2, BASE + 3, BASE + 5], abs=1e-5
            )
            window._service.disconnect_bus()
            window._service.connect_bus(BACKENDS[0], str(uuid.uuid4()), 500000, False)
            new_anchor = window._service.session_started_at
            window._on_message(frame(10))
            assert float(window._model.index(0, 0).data()) == pytest.approx(
                BASE + 10 - new_anchor, abs=1e-6
            )
            assert window._model.timestamp_mode == "relative"
            assert window._table.columnWidth(0) >= 250
        finally:
            if panel.recorder.busy:
                panel.recorder.stop()
                panel.recorder._thread.join(5)
                app.processEvents()
            window.close()
