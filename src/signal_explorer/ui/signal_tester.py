"""Check the latest physical values of selected CAN DBC signals."""

from dataclasses import dataclass
from datetime import datetime
import json
from math import isfinite
from pathlib import Path
from sys import float_info

from cantools.database.errors import DecodeError
from PySide6.QtCore import QIODevice, QSaveFile
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
    QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

from signal_explorer.paths import save_path
from signal_explorer.ui.signal_test_report import SignalResult, SignalTestReport, range_fault


@dataclass(eq=False)
class SignalCheck:
    frame: tuple[int, bool]
    name: str
    unit: str
    minimum: float | None = None
    maximum: float | None = None
    value: float | None = None


class SignalLimitsDialog(QDialog):
    def __init__(self, title, check, parent):
        super().__init__(parent)
        self.setWindowTitle(f"Test limits — {title}")
        self.limits = (check.minimum, check.maximum)
        self.minimum = QLineEdit("" if check.minimum is None else str(check.minimum))
        self.maximum = QLineEdit("" if check.maximum is None else str(check.maximum))
        self.minimum.setPlaceholderText("No minimum")
        self.maximum.setPlaceholderText("No maximum")
        self.error = QLabel()
        self.error.setWordWrap(True)
        form = QFormLayout(self)
        unit = f" ({check.unit})" if check.unit else ""
        form.addRow(f"Minimum{unit}", self.minimum)
        form.addRow(f"Maximum{unit}", self.maximum)
        form.addRow(self.error)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def accept(self):
        try:
            lower, upper = (float(field.text()) if field.text().strip() else None
                            for field in (self.minimum, self.maximum))
            if any(bound is not None and not isfinite(bound) for bound in (lower, upper)):
                raise ValueError
        except ValueError:
            self.error.setText("Enter finite numbers or leave a limit blank.")
            return
        if lower is not None and upper is not None and lower > upper:
            self.error.setText("Minimum must be less than or equal to maximum.")
            return
        self.limits = lower, upper
        super().accept()


class SignalTester(QWidget):
    def __init__(self, parent=None, *, flush_frames, dropped_frames):
        super().__init__(parent)
        self._definitions = {}
        self._checks = []
        self._by_frame = {}
        self._flush_frames = flush_frames
        self._dropped_frames = dropped_frames
        self._drop_base = 0
        self._exploring = False
        self._run = None
        self._pending_report = None
        self._preferred_channel = None

        self.channel = QComboBox()
        self.direction = QComboBox()
        self.direction.addItem("Received (RX)", True)
        self.direction.addItem("Sent (TX)", False)
        self.message = QComboBox()
        self.signal = QComboBox()
        form = QFormLayout()
        for label, picker in (("Channel", self.channel), ("Direction", self.direction),
                              ("Message", self.message), ("Signal", self.signal)):
            picker.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
            form.addRow(label, picker)

        self.add_button = QPushButton("Add signal")
        self.remove_button = QPushButton("Remove selected")
        self.open_list_button = QPushButton("Open List")
        self.save_list_button = QPushButton("Save List")
        self.record_button = QPushButton("Record Test")
        self.save_button = QPushButton("Save Report…")
        self.save_button.hide()
        buttons = QHBoxLayout()
        for button in (self.add_button, self.remove_button, self.open_list_button,
                       self.save_list_button, self.record_button, self.save_button):
            buttons.addWidget(button)
        self.hint = QLabel()
        self.hint.setWordWrap(True)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(("Signal", "Reading", "Minimum", "Maximum", "Status"))
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.verticalHeader().hide()
        self.table.setColumnWidth(0, 175)
        self.table.horizontalHeader().setStretchLastSection(True)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addLayout(buttons)
        layout.addWidget(self.hint)
        layout.addWidget(self.table, 1)

        self.message.currentIndexChanged.connect(self._update_signals)
        self.signal.currentIndexChanged.connect(self._update_buttons)
        self.table.itemSelectionChanged.connect(self._update_buttons)
        self.table.cellDoubleClicked.connect(self._edit_limits)
        self.add_button.clicked.connect(self._add_signal)
        self.remove_button.clicked.connect(self._remove_signal)
        self.record_button.clicked.connect(self._toggle_record)
        self.save_button.clicked.connect(self._save_report)
        self.open_list_button.clicked.connect(self._open_signal_list)
        self.save_list_button.clicked.connect(self._save_signal_list)
        self.channel.currentIndexChanged.connect(self._channel_changed)
        self.direction.currentIndexChanged.connect(self._clear_readings)
        self.set_database(None)

    def set_database(self, database):
        self.stop_recording("DBC changed")
        self._definitions.clear()
        self._replace_checks(())
        self.message.blockSignals(True)
        self.message.clear()
        if database is not None:
            for definition in sorted(database.messages, key=lambda item: item.name.casefold()):
                if definition.is_container or not definition.signals:
                    continue
                key = (definition.frame_id, definition.is_extended_frame)
                self._definitions[key] = definition
                self.message.addItem(definition.name, key)
        self.message.blockSignals(False)
        self.hint.setText("Double-click a signal to adjust its limits." if self._definitions
                          else "Load a CAN DBC to add signals.")
        self._update_signals()

    def set_channels(self, channels):
        previous = self._preferred_channel
        self.channel.blockSignals(True)
        self.channel.clear()
        for channel in channels:
            self.channel.addItem(channel["label"], str(channel["native_channel"]))
        index = self.channel.findData(previous)
        if previous is not None:
            self.channel.setCurrentIndex(index)
        self.channel.blockSignals(False)
        self._clear_readings()
        self._update_buttons()

    def _channel_changed(self):
        self._preferred_channel = self.channel.currentData()
        self._clear_readings()
        self._update_buttons()

    @property
    def recording(self):
        return self._run is not None

    def set_exploring(self, enabled):
        if not enabled:
            self.stop_recording("Exploring stopped")
        self._exploring = enabled
        self._update_buttons()

    def _update_signals(self):
        self.signal.clear()
        definition = self._definitions.get(self.message.currentData())
        if definition is not None:
            self.signal.addItems(sorted(signal.name for signal in definition.signals))
        self._update_buttons()

    def _update_buttons(self):
        frame = self.message.currentData()
        name = self.signal.currentText()
        self.add_button.setEnabled(not self.recording and bool(name) and
                                   not any(check.name == name for _, check in self._by_frame.get(frame, ())))
        self.remove_button.setEnabled(not self.recording and bool(self.table.selectedItems()))
        self.open_list_button.setEnabled(not self.recording and bool(self._definitions))
        self.save_list_button.setEnabled(not self.recording and bool(self._checks))
        self.record_button.setEnabled(self.recording or
                                      (self._exploring and bool(self._checks) and
                                       self.channel.currentData() is not None))

    def _add_signal(self):
        frame = self.message.currentData()
        name = self.signal.currentText()
        definition = self._definitions[frame]
        check = SignalCheck(frame, name, definition.get_signal_by_name(name).unit or "")
        self._insert_check(check)
        self._update_buttons()

    def _replace_checks(self, checks):
        self._checks.clear()
        self._by_frame.clear()
        self.table.setRowCount(0)
        for check in checks:
            self._insert_check(check)
        self._update_buttons()

    def _insert_check(self, check):
        row = len(self._checks)
        self._checks.append(check)
        self._by_frame.setdefault(check.frame, []).append((row, check))
        self.table.insertRow(row)
        label = f"{self._definitions[check.frame].name}.{check.name}"
        label_item = QTableWidgetItem(label)
        label_item.setToolTip(label)
        self.table.setItem(row, 0, label_item)
        for column in range(1, 5):
            self.table.setItem(row, column, QTableWidgetItem())
        self._refresh_row(row, check)

    def _save_signal_list(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Save signal list", save_path("SignalList.json"), "Signal lists (*.json)",
        )
        if not path:
            return
        if not Path(path).suffix:
            path += ".json"
        payload = {
            "format": "signal-explorer-signal-list",
            "version": 1,
            "channel": self.channel.currentData() or self._preferred_channel,
            "direction": "rx" if self.direction.currentData() else "tx",
            "signals": [
                {"id": check.frame[0], "extended": check.frame[1],
                 "message": self._definitions[check.frame].name, "signal": check.name,
                 "minimum": check.minimum, "maximum": check.maximum}
                for check in self._checks
            ],
        }
        data = (json.dumps(payload, indent=2) + "\n").encode("utf-8")
        if self._write_file(path, data, "Could not save signal list"):
            self.hint.setText(f"Signal list saved to {Path(path).name}")

    def _open_signal_list(self):
        path, _ = QFileDialog.getOpenFileName(self, "Open signal list", "", "Signal lists (*.json)")
        if not path:
            return
        try:
            payload = json.loads(Path(path).read_text(encoding="utf-8-sig"))
            channel, direction, checks = self._read_signal_list(payload)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Could not open signal list", str(exc))
            return
        self.channel.setCurrentIndex(0 if channel is None else self.channel.findData(channel))
        self._preferred_channel = channel
        self.direction.setCurrentIndex(self.direction.findData(direction == "rx"))
        self._replace_checks(checks)
        if channel is not None and self.channel.currentIndex() < 0:
            self.hint.setText("Signal list loaded. Connect or select the saved CAN channel.")
        else:
            self.hint.setText("Signal list loaded. Double-click a signal to adjust its limits.")

    def _read_signal_list(self, payload):
        if (not isinstance(payload, dict) or payload.get("format") != "signal-explorer-signal-list"
                or type(payload.get("version")) is not int or payload["version"] != 1
                or not isinstance(payload.get("signals"), list)):
            raise ValueError("This file is not a supported Signal Explorer signal list.")
        channel, direction = payload.get("channel"), payload.get("direction")
        if (channel is not None and not isinstance(channel, str)) or direction not in ("rx", "tx"):
            raise ValueError("Invalid CAN channel or direction.")
        checks = []
        seen = set()
        for entry in payload["signals"]:
            if (not isinstance(entry, dict) or type(entry.get("id")) is not int
                    or type(entry.get("extended")) is not bool or not isinstance(entry.get("signal"), str)):
                raise ValueError("Invalid signal entry.")
            frame, name = (entry["id"], entry["extended"]), entry["signal"]
            definition = self._definitions.get(frame)
            if definition is None or definition.name != entry.get("message"):
                raise ValueError(f"Load the matching DBC. Message {entry.get('message')} is missing.")
            try:
                signal = definition.get_signal_by_name(name)
            except KeyError:
                raise ValueError(f"Load the matching DBC. Signal {definition.name}.{name} is missing.") from None
            if (frame, name) in seen:
                raise ValueError(f"Duplicate signal {definition.name}.{name}.")
            lower, upper = entry.get("minimum"), entry.get("maximum")
            if (any(bound is not None and (type(bound) not in (int, float) or
                                          abs(bound) > float_info.max or not isfinite(bound))
                    for bound in (lower, upper))
                    or (lower is not None and upper is not None and lower > upper)):
                raise ValueError(f"Invalid limits for {definition.name}.{name}.")
            seen.add((frame, name))
            checks.append(SignalCheck(frame, name, signal.unit or "", lower, upper))
        return channel, direction, checks

    def _remove_signal(self):
        row = self.table.currentRow()
        if not self.table.selectedItems():
            return
        self._checks.pop(row)
        self._by_frame.clear()
        for index, check in enumerate(self._checks):
            self._by_frame.setdefault(check.frame, []).append((index, check))
        self.table.removeRow(row)
        self._update_buttons()

    def _edit_limits(self, row, _column):
        if self.recording:
            return
        check = self._checks[row]
        dialog = SignalLimitsDialog(self.table.item(row, 0).text(), check, self)
        if dialog.exec() == QDialog.DialogCode.Accepted and check in self._checks:
            check.minimum, check.maximum = dialog.limits
            self.table.clearSelection()
            self._refresh_row(self._checks.index(check), check)

    def _toggle_record(self):
        if self.recording:
            self.stop_recording("Stopped by user")
            self._save_report()
            return
        if self._pending_report is not None and not self._confirm_report():
            return
        self._flush_frames()
        self._drop_base = self._dropped_frames()
        self._run = SignalTestReport(self.channel.currentText(), self.direction.currentText(), [
            SignalResult(self.table.item(row, 0).text(), check.unit, check.minimum, check.maximum)
            for row, check in enumerate(self._checks)
        ])
        self._set_recording_controls()
        self.hint.setText("Recording test…")

    def stop_recording(self, reason):
        if not self.recording:
            return
        self._flush_frames()
        report = self._run
        self._run = None
        report.stopped = datetime.now().astimezone()
        report.reason = reason
        report.dropped = max(0, self._dropped_frames() - self._drop_base)
        self._pending_report = report
        self.save_button.show()
        self._set_recording_controls()
        self.hint.setText("Test complete. Save the report to keep the results.")

    def _set_recording_controls(self):
        self.record_button.setText("Stop Test" if self.recording else "Record Test")
        for picker in (self.channel, self.direction, self.message, self.signal):
            picker.setEnabled(not self.recording)
        self._update_buttons()

    def _save_report(self):
        report = self._pending_report
        if report is None:
            return True
        default = save_path(f"SignalTest-{report.started:%Y%m%d-%H%M%S}.csv")
        path, _ = QFileDialog.getSaveFileName(self, "Save signal test report", default, "CSV (*.csv)")
        if not path:
            return False
        if not Path(path).suffix:
            path += ".csv"
        data = report.to_csv().encode("utf-8-sig")
        if not self._write_file(path, data, "Could not save test report"):
            return False
        self._pending_report = None
        self.save_button.hide()
        self.hint.setText(f"Report saved to {Path(path).name}")
        return True

    def _write_file(self, path, data, error_title):
        output = QSaveFile(path)
        if output.open(QIODevice.OpenModeFlag.WriteOnly) and output.write(data) == len(data) and output.commit():
            return True
        QMessageBox.warning(self, error_title, output.errorString())
        return False

    def _confirm_report(self):
        buttons = QMessageBox.StandardButton
        choice = QMessageBox.question(self, "Unsaved signal test report",
                                      "Save the signal test report?", buttons.Save | buttons.Discard | buttons.Cancel,
                                      buttons.Save)
        if choice == buttons.Save:
            return self._save_report()
        if choice == buttons.Discard:
            self._pending_report = None
            self.save_button.hide()
            return True
        return False

    def request_close(self):
        self.stop_recording("Application closing")
        return self._pending_report is None or self._confirm_report()

    def _clear_readings(self):
        for row, check in enumerate(self._checks):
            check.value = None
            self._refresh_row(row, check)

    def append_frames(self, frames):
        channel = self.channel.currentData()
        if channel is None or not self._by_frame:
            return
        direction = self.direction.currentData()
        changed = {}
        for frame in frames:
            key = (frame.arbitration_id, frame.is_extended_id)
            checks = self._by_frame.get(key)
            if (not checks or frame.is_error_frame or frame.is_remote_frame
                    or frame.is_rx != direction or str(frame.channel) != channel):
                continue
            try:
                values = self._definitions[key].decode(frame.data, decode_choices=False)
            except DecodeError:
                continue
            for row, check in checks:
                value = values.get(check.name)
                if not isinstance(value, (int, float)) or not isfinite(value):
                    continue
                if self.recording:
                    self._run.results[row].observe(value)
                if check.value != value:
                    check.value = value
                    changed[row] = check
        for row, check in changed.items():
            self._refresh_row(row, check)

    def _refresh_row(self, row, check):
        value = check.value
        fault = range_fault(value, check.minimum, check.maximum) if value is not None else ""
        if value is None:
            status = "Waiting for data"
        elif fault:
            status = fault
        elif check.minimum is None and check.maximum is None:
            status = "No limits"
        else:
            status = "In range"
        unit = f" {check.unit}" if check.unit else ""
        for column, number in enumerate((value, check.minimum, check.maximum), 1):
            self.table.item(row, column).setText("—" if number is None else f"{number:.10g}{unit}")
        reading = self.table.item(row, 1)
        reading.setBackground(QColor("#ffd9d9") if fault else self.table.palette().base())
        reading.setForeground(QColor("#631818") if fault else self.table.palette().text())
        self.table.item(row, 4).setText(status)
