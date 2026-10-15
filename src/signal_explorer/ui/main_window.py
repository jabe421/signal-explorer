from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QStatusBar,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from signal_explorer.bus.backends import (
    BACKENDS,
    BITRATES,
    BackendSpec,
    list_serial_ports,
    preferred_slcan_port,
)
from signal_explorer.bus.service import BusService
from signal_explorer.ui.trace_model import TraceModel


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Signal Explorer")
        self.resize(1100, 640)

        self._service = BusService(self)
        self._service.connected.connect(self._on_connected)
        self._service.disconnected.connect(self._on_disconnected)
        self._service.message.connect(self._on_message)
        self._service.error.connect(self._on_error)

        self._model = TraceModel()
        self._build()
        self._select_canable_if_present()
        self._apply_backend(self._current_backend())

    def _build(self) -> None:
        self._backend = QComboBox()
        for spec in BACKENDS:
            self._backend.addItem(spec.label, spec.key)
        self._backend.currentIndexChanged.connect(lambda _: self._apply_backend(self._current_backend()))

        self._channel = QComboBox()
        self._channel.setEditable(True)
        self._channel.setMinimumWidth(140)
        self._bitrate = QComboBox()
        for rate in BITRATES:
            self._bitrate.addItem(f"{rate // 1000} kbit/s", rate)
        self._bitrate.setCurrentIndex(2)  # 500 k

        self._demo = QCheckBox("Inject demo traffic")
        self._demo.setChecked(True)
        self._demo.setToolTip("Sends a 100 ms frame on the virtual bus so you can test without hardware.")

        self._connect = QPushButton("Connect")
        self._connect.clicked.connect(self._toggle_connection)
        self._clear = QPushButton("Clear")
        self._clear.clicked.connect(self._model.clear)

        self._notes = QLabel()
        self._notes.setWordWrap(True)
        self._notes.setStyleSheet("color: #666;")

        bar = QHBoxLayout()
        for label, widget in (
            ("Adapter", self._backend),
            ("Channel", self._channel),
            ("Bitrate", self._bitrate),
        ):
            bar.addWidget(QLabel(label))
            bar.addWidget(widget)
        bar.addWidget(self._demo)
        bar.addWidget(self._connect)
        bar.addWidget(self._clear)
        bar.addStretch()

        self._table = QTableView()
        self._table.setModel(self._model)
        self._table.setAlternatingRowColors(True)
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self._table.horizontalHeader().setStretchLastSection(True)

        root = QVBoxLayout()
        root.addLayout(bar)
        root.addWidget(self._notes)
        root.addWidget(self._table)

        container = QWidget()
        container.setLayout(root)
        self.setCentralWidget(container)

        status = QStatusBar()
        self._status = QLabel("Disconnected")
        self._count = QLabel("0 frames")
        status.addWidget(self._status)
        status.addPermanentWidget(self._count)
        self.setStatusBar(status)
        self._frame_count = 0

    def _current_backend(self) -> BackendSpec:
        key = self._backend.currentData()
        for spec in BACKENDS:
            if spec.key == key:
                return spec
        return BACKENDS[0]

    def _select_canable_if_present(self) -> None:
        port = preferred_slcan_port()
        if not port:
            return
        for index in range(self._backend.count()):
            if self._backend.itemData(index) == "slcan":
                self._backend.setCurrentIndex(index)
                break

    def _fill_channel(self, spec: BackendSpec) -> None:
        self._channel.blockSignals(True)
        self._channel.clear()
        if spec.needs_serial:
            ports = list_serial_ports()
            for device, description in ports:
                self._channel.addItem(f"{device} — {description}", device)
            preferred = preferred_slcan_port() or spec.default_channel
            match = self._channel.findData(preferred)
            if match < 0:
                self._channel.setEditText(preferred)
            else:
                self._channel.setCurrentIndex(match)
        else:
            self._channel.addItem(spec.default_channel, spec.default_channel)
            self._channel.setEditText(spec.default_channel)
        self._channel.blockSignals(False)

    def _channel_value(self) -> str:
        data = self._channel.currentData()
        if isinstance(data, str) and data:
            return data
        return self._channel.currentText().split("—")[0].strip()

    def _apply_backend(self, spec: BackendSpec) -> None:
        self._fill_channel(spec)
        self._notes.setText(spec.notes)
        virtual = spec.interface == "virtual"
        self._demo.setEnabled(virtual)
        self._bitrate.setEnabled(not virtual)

    def _toggle_connection(self) -> None:
        if self._service.is_connected:
            self._service.disconnect_bus()
            return
        spec = self._current_backend()
        bitrate = int(self._bitrate.currentData())
        channel = self._channel_value()
        if not channel:
            QMessageBox.warning(self, "Signal Explorer", "Channel is required (COM port or index).")
            return
        inject = self._demo.isChecked() and spec.interface == "virtual"
        self._service.connect_bus(spec, channel, bitrate, inject)

    def _on_connected(self, label: str) -> None:
        self._connect.setText("Disconnect")
        self._backend.setEnabled(False)
        self._channel.setEnabled(False)
        self._bitrate.setEnabled(False)
        self._demo.setEnabled(False)
        self._status.setText(f"Connected · {label}")

    def _on_disconnected(self) -> None:
        self._connect.setText("Connect")
        self._backend.setEnabled(True)
        self._channel.setEnabled(True)
        self._apply_backend(self._current_backend())
        self._status.setText("Disconnected")

    def _on_message(self, msg) -> None:
        self._model.append_message(msg)
        self._frame_count += 1
        self._count.setText(f"{self._frame_count} frames")
        if self._frame_count % 20 == 0:
            self._table.scrollToBottom()

    def _on_error(self, text: str) -> None:
        self._status.setText(text)
        QMessageBox.warning(self, "Signal Explorer", text)

    def closeEvent(self, event) -> None:  # noqa: N802
        self._service.disconnect_bus()
        super().closeEvent(event)
