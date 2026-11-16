import csv
from pathlib import Path

import can
import cantools
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from signal_explorer.ui.main_window import MainWindow
from signal_explorer.ui.signal_test_report import SignalResult


def test_signal_tester_checks_demo_board_voltage_and_edits_limits():
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    window.show()
    try:
        tester = window._signal_tester
        action = next(action for action in window._view_menu.actions()
                      if action.text() == "Signal Tester")
        action.trigger()
        assert not window._signal_tester_dock.isHidden()
        path = Path(__file__).resolve().parents[1] / "examples/demo_board.dbc"
        database = cantools.database.load_file(path)
        definition = database.get_message_by_name("DemoBoard_Potentiometer")
        window._recording_panel.database_path = str(path)
        window._dbc_editor_saved(str(path), database)
        tester.set_channels([{"label": "CAN 1", "native_channel": 1}])
        tester.message.setCurrentText(definition.name)
        tester.add_button.click()
        assert tester.table.rowCount() == 1

        def edit_limits():
            dialog = app.activeModalWidget()
            dialog.minimum.setText("4")
            dialog.maximum.setText("1")
            dialog.accept()
            assert "Minimum must be" in dialog.error.text()
            dialog.minimum.setText("1")
            dialog.maximum.setText("4")
            dialog.accept()

        QTimer.singleShot(0, edit_limits)
        tester.table.cellDoubleClicked.emit(0, 0)
        assert tester.table.item(0, 2).text() == "1 V"
        assert tester.table.item(0, 3).text() == "4 V"
        window._set_exploring(True)

        def receive(voltage, channel=1):
            window._on_messages([can.Message(
                channel=channel, arbitration_id=definition.frame_id, is_extended_id=False,
                data=definition.encode({"Potentiometer": voltage}),
            )])

        for voltage, status in ((0, "Below minimum"), (5, "Above maximum"), (2, "In range")):
            receive(voltage)
            assert tester.table.item(0, 4).text() == status
            assert (tester.table.item(0, 1).background().color().name() == "#ffd9d9") == (voltage != 2)
        receive(5, channel=2)
        assert tester.table.item(0, 4).text() == "In range"
        window._set_exploring(False)
        receive(5)
        assert tester.table.item(0, 4).text() == "In range"
        window._unload_dbc()
        assert tester.table.rowCount() == 0
    finally:
        window.close()
        app.processEvents()


def test_signal_test_report_sorts_failures_first_and_counts_transient_faults(monkeypatch, tmp_path):
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    tester = window._signal_tester
    database = cantools.database.load_file(Path(__file__).resolve().parents[1] / "examples/demo_board.dbc")
    target = tmp_path / "report.csv"
    monkeypatch.setattr("signal_explorer.ui.signal_tester.QFileDialog.getSaveFileName",
                        lambda *args: ("", ""))
    try:
        tester.set_database(database)
        tester.set_channels([{"label": "CAN 1", "native_channel": 1}])
        window._set_exploring(True)
        names = ("DemoBoard_Button1", "DemoBoard_Potentiometer", "DemoBoard_Button2")
        for name in names:
            tester.message.setCurrentText(name)
            tester.add_button.click()
        for check in tester._checks:
            check.minimum, check.maximum = (1, 4) if check.name == "Potentiometer" else (0, 1)
        assert tester.record_button.isEnabled()
        tester.record_button.click()
        assert tester.record_button.text() == "Stop Test"
        assert not tester.channel.isEnabled()

        def frame(message, value):
            definition = database.get_message_by_name(message)
            return can.Message(channel=1, arbitration_id=definition.frame_id, is_extended_id=False,
                               data=definition.encode({definition.signals[0].name: value}))

        window._on_messages([frame(names[0], 1), frame(names[1], 5), frame(names[1], 2)])
        assert tester.table.item(1, 4).text() == "In range"
        tester.record_button.click()
        assert not tester.save_button.isHidden()
        monkeypatch.setattr("signal_explorer.ui.signal_tester.QFileDialog.getSaveFileName",
                            lambda *args: (str(target), ""))
        tester.save_button.click()
        with target.open(encoding="utf-8-sig", newline="") as stream:
            rows = list(csv.DictReader(stream))
        assert [row["Result"] for row in rows] == ["FAIL", "NO DATA", "PASS"]
        assert rows[0]["Samples"] == "2"
        assert rows[0]["Out of range samples"] == "1"
        assert rows[0]["First failing value"] == "5.0"
        assert tester.save_button.isHidden()
    finally:
        window.close()
        app.processEvents()


def test_signal_result_with_dropped_frames_cannot_pass():
    result = SignalResult("Test", "V", 1, 4)
    result.observe(1)
    result.observe(4)
    assert result.outcome(0) == "PASS"
    assert result.outcome(1) == "INCOMPLETE"
    result.observe(5)
    assert result.outcome(1) == "FAIL"


def test_signal_list_round_trip_and_rejects_mismatched_dbc(monkeypatch, tmp_path):
    import json

    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    tester = window._signal_tester
    database = cantools.database.load_file(Path(__file__).resolve().parents[1] / "examples/demo_board.dbc")
    target = tmp_path / "bench.json"
    dialogs = "signal_explorer.ui.signal_tester.QFileDialog"
    monkeypatch.setattr(f"{dialogs}.getSaveFileName", lambda *args: (str(target.with_suffix("")), ""))
    monkeypatch.setattr(f"{dialogs}.getOpenFileName", lambda *args: (str(target), ""))
    warnings = []
    monkeypatch.setattr("signal_explorer.ui.signal_tester.QMessageBox.warning",
                        lambda parent, title, message: warnings.append(message))
    try:
        tester.set_database(database)
        channels = [{"label": f"CAN {number}", "native_channel": number} for number in (1, 2)]
        tester.set_channels(channels)
        tester.channel.setCurrentIndex(1)
        tester.direction.setCurrentIndex(1)
        tester.message.setCurrentText("DemoBoard_Potentiometer")
        tester.add_button.click()
        tester._checks[0].minimum = 1
        tester._checks[0].maximum = 4
        tester.save_list_button.click()
        tester.set_database(database)
        tester.set_channels(())
        tester.open_list_button.click()
        assert tester.channel.currentIndex() == -1
        tester.set_channels(channels)
        assert tester.channel.currentData() == "2" and not tester.direction.currentData()
        assert tester.table.item(0, 2).text() == "1 V"
        assert tester.table.item(0, 3).text() == "4 V"
        payload = json.loads(target.read_text(encoding="utf-8"))
        payload["signals"][0]["message"] = "Missing"
        target.write_text(json.dumps(payload), encoding="utf-8")
        tester.open_list_button.click()
        assert "matching DBC" in warnings[0]
        assert tester.table.rowCount() == 1
        assert tester.table.item(0, 2).text() == "1 V"
    finally:
        window.close()
        app.processEvents()
