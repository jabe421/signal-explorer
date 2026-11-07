"""Replay timing controls and host-side measurements, outside the main trace."""

import json
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QDialog,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)


class ReplayTimingWindow(QDialog):
    def __init__(self, panels, parent=None):
        super().__init__(parent)
        self.panels = panels
        self.setWindowTitle("Signal Explorer — Replay timing")
        self.resize(1120, 350)
        self.mode = QComboBox()
        self.mode.addItem("Balanced — no busy-wait (default)", "balanced")
        self.mode.addItem(
            "Precision — up to 0.5 ms final busy-wait per event", "precision"
        )
        self.mode.setCurrentIndex(
            self.mode.findData(next(iter(panels.values())).replay_timing_mode)
        )
        self.mode.currentIndexChanged.connect(self._set_mode)
        self.scope = QLabel(
            "Applies to the next replay in all protocol tabs and File Viewer. Stop active replay before changing mode."
        )
        self.scope.setWordWrap(True)
        explanation = QLabel(
            "Scheduler lateness is measured before send; send duration is time inside the callback; return lateness is measured afterward. "
            "These are host measurements, not bus-wire timing or ECU acknowledgement. Precision may increase CPU use. "
            "CPU counters are sampled by the OS; short runs can read zero. Percentiles use the last 4096 attempts; means/maxima cover the run."
        )
        explanation.setWordWrap(True)
        self.table = QTableWidget(len(panels), 8)
        self.table.setHorizontalHeaderLabels(
            [
                "Protocol",
                "State",
                "Sent",
                "Scheduler p95 (ms)",
                "Scheduler max (ms)",
                "Send max (ms)",
                "Return max (ms)",
                "Worker CPU (ms)",
            ]
        )
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents
        )
        self.table.verticalHeader().hide()
        self.backend = QLabel()
        self.backend.setWordWrap(True)
        copy = QPushButton("Copy timing report")
        copy.clicked.connect(self.copy_report)
        close = QPushButton("Close")
        close.clicked.connect(self.close)
        buttons = QHBoxLayout()
        buttons.addWidget(copy)
        buttons.addStretch()
        buttons.addWidget(close)
        layout = QVBoxLayout(self)
        for widget in (self.mode, self.scope, explanation, self.table, self.backend):
            layout.addWidget(widget)
        layout.addLayout(buttons)
        self._timer = QTimer(self)
        self._timer.setInterval(250)
        self._timer.timeout.connect(self.refresh)
        self.refresh()

    def _set_mode(self):
        if any(panel.playback_busy for panel in self.panels.values()):
            return
        for panel in self.panels.values():
            panel.replay_timing_mode = self.mode.currentData()

    def report(self):
        return {
            name: panel.playback.timing_snapshot()
            if panel.playback_busy
            else panel.last_replay_timing
            for name, panel in self.panels.items()
        }

    def refresh(self):
        self.mode.setEnabled(
            not any(panel.playback_busy for panel in self.panels.values())
        )
        reports = self.report()
        backends = []

        def number(value):
            return "n/a" if value is None else f"{value:.3f}"

        for row, (name, report) in enumerate(reports.items()):
            values = [name, "Not replayed", "0", "n/a", "n/a", "n/a", "n/a", "n/a"]
            if report:
                values = [
                    name,
                    report["state"],
                    str(report["sent"]),
                    number(report["scheduler_lateness"]["p95_ms"]),
                    number(report["scheduler_lateness"]["max_ms"]),
                    number(report["send_duration"]["max_ms"]),
                    number(report["send_return_lateness"]["max_ms"]),
                    number(report["worker_cpu_ms"]),
                ]
                backends.append(f"{name}: {report['wait_backend']}")
            for col, text in enumerate(values):
                item = self.table.item(row, col)
                if item is None:
                    self.table.setItem(row, col, QTableWidgetItem(text))
                elif item.text() != text:
                    item.setText(text)
        self.backend.setText(
            "Wait method: " + "; ".join(backends)
            if backends
            else "Wait method will appear after replay starts. Windows uses a high-resolution timer when available."
        )

    def copy_report(self):
        QApplication.clipboard().setText(json.dumps(self.report(), indent=2))

    def showEvent(self, event):
        self.refresh()
        self._timer.start()
        super().showEvent(event)

    def hideEvent(self, event):
        self._timer.stop()
        super().hideEvent(event)
