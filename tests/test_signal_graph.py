from pathlib import Path

import can
import cantools
from PySide6.QtWidgets import QApplication

from signal_explorer.ui.main_window import MainWindow
from signal_explorer.ui.signal_graph import SignalHistory


def test_signal_graph_tabs_decode_live_frames_and_reset():
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    window.show()
    try:
        graph = window._signal_graph
        graph_dock = window._signal_graph_dock
        health_dock = window._bus_health_dock
        assert graph_dock.isHidden()
        window._view_menu.actions()[2].trigger()
        assert not graph_dock.isHidden()
        assert not graph.add_button.isEnabled()

        database = cantools.database.load_string(
            'VERSION ""\nNS_ :\nBS_:\nBU_: ECU\n'
            'BO_ 256 Demo: 1 ECU\n SG_ Speed : 0|8@1+ (0.5,-10) [-10|117.5] "km/h" ECU\n'
            'VAL_ 256 Speed 40 "Cruise";\n',
            database_format="dbc",
        )
        window._recording_panel.database_path = str(Path("demo.dbc"))
        window._dbc_editor_saved("demo.dbc", database)
        graph.set_channels([{"label": "CAN 1", "native_channel": 1}])
        graph.add_button.click()
        samples = graph.series[((256, False), "Speed")]
        assert graph.selection.item(0).text() == "Demo.Speed (km/h)"
        window._set_exploring(True)
        window._on_messages([
            can.Message(timestamp=100, channel=1, arbitration_id=256, is_extended_id=False, data=[40]),
            can.Message(timestamp=101, channel=1, arbitration_id=256, is_extended_id=False, data=[60]),
            can.Message(timestamp=102, channel=2, arbitration_id=256, is_extended_id=False, data=[80]),
            can.Message(timestamp=102, channel=1, arbitration_id=256, is_extended_id=True, data=[80]),
            can.Message(timestamp=102, channel=1, arbitration_id=256, is_extended_id=False, is_rx=False, data=[80]),
            can.Message(timestamp=102, channel=1, arbitration_id=256, is_extended_id=False, data=[]),
        ])
        assert list(samples) == [(100, 10), (101, 20)]
        assert not graph.plot.grab().isNull()

        window._open_bus_health()
        assert graph_dock in window.tabifiedDockWidgets(health_dock)
        assert not graph_dock.isHidden()
        window._on_messages([
            can.Message(timestamp=103, channel=1, arbitration_id=256, is_extended_id=False, data=[80]),
        ])
        assert list(samples)[-1] == (103, 30)
        window._set_exploring(False)
        next_frame = can.Message(timestamp=104, channel=1, arbitration_id=256, is_extended_id=False, data=[100])
        window._on_messages([next_frame])
        assert graph.latest == 103
        assert list(samples)[-1] == (103, 30)
        window._set_exploring(True)
        window._on_messages([next_frame])
        assert list(samples)[-1] == (104, 40)
        graph.direction.setCurrentIndex(1)
        assert not list(samples)
        window._unload_dbc()
        assert not graph.series and not graph.add_button.isEnabled()
        window._reset_can_layout()
        assert graph_dock.isHidden() and health_dock.isHidden()
        window._open_bus_health()
        window._view_menu.actions()[2].trigger()
        assert graph_dock in window.tabifiedDockWidgets(health_dock)
        app.processEvents()
    finally:
        window.close()


def test_signal_history_keeps_full_window_at_high_frame_rates():
    history = SignalHistory()
    for index in range(7000):
        history.append(100 + index / 100, index % 2)
    points = list(history)
    assert min(stamp for stamp, _ in points) <= 110
    assert max(stamp for stamp, _ in points) == 169.99
    assert len(points) <= 2 * SignalHistory.CAPACITY

    history.clear()
    for offset, value in [(0.001, 3), (0.002, -2), (0.003, 1), (0.004, 8)]:
        history.append(200 + offset, value)
    assert list(history) == [(200.002, -2), (200.004, 8)]
