from __future__ import annotations

from PySide6.QtCore import QSettings, QTimer, Qt
from PySide6.QtGui import QAction
from signal_explorer.ui.adapter_preferences import AdapterPreference
from pathlib import Path
from signal_explorer.bus.discovery import discover_channels
from PySide6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QLineEdit,
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
    QTabWidget,
    QStackedWidget,
    QSizePolicy,
)

from signal_explorer.bus.backends import (
    BACKENDS,
    BITRATES,
    BackendSpec,
    list_serial_ports,
    preferred_slcan_port,
)
from signal_explorer.bus.service import BusService
from signal_explorer.bus.canfd import FD_INTERFACES, DATA_BITRATES, validate_frame
from signal_explorer.ui.trace_model import TraceModel, IdFilter
from signal_explorer.ui.recording_panel import RecordingPanel
from signal_explorer.ui.lin_panel import LinPanel
from signal_explorer.ui.ethernet_panel import EthernetPanel
from signal_explorer.scripting.transport import CanScriptTransport
from signal_explorer.scripting.controller import ScriptController
from signal_explorer.ui.script_window import ScriptWindow
from signal_explorer.ui.log_window import LogWindow
import can
import cantools


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Signal Explorer")
        self.resize(1400, 720)

        self._service = BusService(self)
        self._service.connected.connect(self._on_connected)
        self._service.disconnected.connect(self._on_disconnected)
        self._service.message.connect(self._on_message)
        self._service.error.connect(self._on_error)
        self._script_transport = CanScriptTransport(self._service, self)
        self._script_controller = ScriptController(self._script_transport, self)
        self._script_windows = []
        self._dbc_windows = []

        self._exploring = False
        self._model = TraceModel()
        self._repeat_message = None
        self._tx_timer = QTimer(self)
        self._tx_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._tx_timer.timeout.connect(self._send_repeated_message)
        self._adapter_preference = AdapterPreference("can")
        self._build()
        self._backend.blockSignals(True)
        index = self._backend.findData(self._adapter_preference.load())
        self._backend.setCurrentIndex(index if index >= 0 else self._backend.findData("virtual"))
        self._backend.blockSignals(False)
        self._apply_backend(self._current_backend())

    def _build(self) -> None:
        scripts = self.menuBar().addMenu('File Viewer')
        for title, callback in (('New Python Script', lambda:self._open_script(new=True)),
                                ('Open Python Script...', self._open_script),
                                ('Open Logfile', self._open_logfile),
                                ('Open CAN Simulation Example', self._open_demo_script)):
            action=QAction(title,self);action.triggered.connect(callback);scripts.addAction(action)
        self._backend = QComboBox()
        for spec in sorted(BACKENDS, key=lambda spec: spec.label.casefold()):
            self._backend.addItem(spec.label, spec.key)
        self._backend.setCurrentIndex(self._backend.findData("virtual"))
        self._backend.currentIndexChanged.connect(lambda _: self._apply_backend(self._current_backend()))
        self._backend.currentIndexChanged.connect(lambda _: self._adapter_preference.save(self._current_backend().key))

        self._channel = QComboBox()
        self._channel.setEditable(True)
        self._channel.setMinimumWidth(140)
        self._bitrate = QComboBox()
        for rate in BITRATES:
            self._bitrate.addItem(f"{rate // 1000} kbit/s", rate)
        self._bitrate.setCurrentIndex(2)  # 500 k

        self._fd = QCheckBox("CAN FD")
        self._termination = QCheckBox("NI internal termination (120 ohm)")
        self._termination.setToolTip("Enable only when this NI port is at a bus end and needs termination. Default: off.")
        self._channel.currentIndexChanged.connect(self._sync_ni_controls)
        self._channel.editTextChanged.connect(self._sync_ni_controls)
        self._data_bitrate = QComboBox()
        for rate in DATA_BITRATES:
            self._data_bitrate.addItem(f"{rate / 1000000:g} Mbit/s", rate)
        self._data_bitrate.setCurrentIndex(2)
        self._fd.toggled.connect(self._sync_fd_controls)

        self._demo = QCheckBox("Inject demo traffic")
        self._demo.setChecked(True)
        self._demo.setToolTip("Simulates engine and oxygen sensor traffic every 100 ms. CAN FD adds a 32-byte frame.")

        self._connect = QPushButton("Connect")
        self._connect.clicked.connect(self._toggle_connection)
        self._clear = QPushButton("Clear")
        self._clear.clicked.connect(self._clear_trace)

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
        self._explore = QPushButton("Start Exploring")
        self._explore.setEnabled(False)
        self._explore.clicked.connect(self._toggle_exploring)
        bar.addWidget(self._explore)
        self._refresh = QPushButton("Refresh devices")
        self._refresh.clicked.connect(lambda: self._apply_backend(self._current_backend()))
        bar.addWidget(self._refresh)
        self._vector_dll = QPushButton("Vector DLL")
        self._vector_dll.clicked.connect(self._choose_vector_dll)
        bar.addWidget(self._vector_dll)
        bar.addStretch()

        self._table = QTableView()
        self._proxy = IdFilter(self)
        self._proxy.setSourceModel(self._model)
        self._table.setModel(self._proxy)
        self._table.setAlternatingRowColors(True)
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.setColumnWidth(0, 165)
        self._table.setColumnWidth(7, 210)
        self._table.setColumnWidth(8, 160)

        root = QVBoxLayout()
        # Adapter controls are stacked above the protocol tabs.
        fd_bar = QHBoxLayout()
        fd_bar.addWidget(self._fd)
        fd_bar.addWidget(QLabel("Data bitrate (FD)"))
        fd_bar.addWidget(self._data_bitrate)
        fd_bar.addWidget(self._termination)
        fd_bar.addWidget(QLabel("Bitrate above is the arbitration rate. Virtual mode simulates frames, not physical bus timing."))
        fd_bar.addStretch()
        root.addLayout(fd_bar)
        root.addWidget(self._notes)
        filters = QHBoxLayout()
        self._filter = QLineEdit()
        self._filter.setPlaceholderText("Hex IDs: 100, 200-2FF (blank shows all)")
        apply_filter = QPushButton("Apply filter")
        apply_filter.clicked.connect(self._apply_filter)
        self._filter.returnPressed.connect(self._apply_filter)
        clear_filter = QPushButton("Clear filter")
        clear_filter.clicked.connect(self._clear_filter)
        load_dbc = QPushButton("Load DBC")
        load_dbc.clicked.connect(self._load_dbc)
        unload_dbc = QPushButton("Unload DBC")
        unload_dbc.clicked.connect(self._unload_dbc)
        edit_dbc = QPushButton("Edit DBC")
        edit_dbc.clicked.connect(self._open_dbc_editor)
        self._dbc_label = QLabel("No DBC loaded")
        for widget in (QLabel("ID filter"), self._filter, apply_filter, clear_filter, load_dbc, unload_dbc, edit_dbc, self._dbc_label):
            filters.addWidget(widget)
        self._auto_scroll = QCheckBox("Auto-scroll (all messages)")
        self._auto_scroll.setChecked(True)
        self._auto_scroll.setToolTip("Checked: timestamped message history with auto-scroll. Unchecked: latest data per CAN ID without scrolling.")
        self._auto_scroll.toggled.connect(self._toggle_trace_mode)
        filters.addWidget(self._auto_scroll)
        root.addLayout(filters)
        root.addWidget(self._table)

        send_bar = QHBoxLayout()
        self._tx_id = QLineEdit("100")
        self._tx_id.setMaximumWidth(120)
        self._tx_extended = QCheckBox("Extended ID")
        self._tx_data = QLineEdit("01 02 03 04 05 06 07 08")
        self._tx_data.setPlaceholderText("0-8 hexadecimal bytes, e.g. DE AD BE EF")
        self._tx_fd = QCheckBox("FD frame")
        self._tx_brs = QCheckBox("BRS")
        self._tx_fd.toggled.connect(self._sync_fd_controls)
        self._tx_interval = QLineEdit()
        self._tx_interval.setMaximumWidth(130)
        self._tx_interval.setPlaceholderText("Blank = once")
        self._tx_interval.setToolTip("Repeat interval in whole milliseconds (1-2147483647). Leave blank to send once.")
        self._send = QPushButton("Send")
        self._send.setEnabled(False)
        self._send.clicked.connect(self._send_message)
        for widget in (QLabel("Send ID (hex)"), self._tx_id, self._tx_extended, self._tx_fd, self._tx_brs,
                       QLabel("Data (hex)"), self._tx_data,
                       QLabel("Interval (ms)"), self._tx_interval, self._send):
            send_bar.addWidget(widget)
        root.addLayout(send_bar)
        self._recording_panel = RecordingPanel(self._service, self)
        self._recording_panel.playback_changed.connect(self._on_playback_changed)
        self._recording_panel.close_ready.connect(self.close)
        root.addWidget(self._recording_panel)
        root.addWidget(QLabel("TX indicates a local send accepted by the adapter; it does not confirm reception by another device."))

        container = QWidget()
        container.setLayout(root)
        self._lin = LinPanel(self)
        self._lin.recording.close_ready.connect(self.close)
        self._ethernet = EthernetPanel(self)
        self._ethernet.recording.close_ready.connect(self.close)
        self._tabs = QTabWidget()
        self._tabs.addTab(container, "CAN / CAN FD")
        self._tabs.addTab(self._lin, "LIN")
        self._tabs.addTab(self._ethernet, "Ethernet")
        can_adapter_widget = QWidget(); can_adapter_widget.setLayout(bar)
        self._adapter_stack = QStackedWidget()
        self._adapter_stack.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._adapter_stack.addWidget(can_adapter_widget)
        self._adapter_stack.addWidget(self._lin.adapter_widget)
        self._adapter_stack.addWidget(self._ethernet.adapter_widget)
        outer = QWidget(); outer_layout = QVBoxLayout(outer)
        outer_layout.addWidget(self._adapter_stack); outer_layout.addWidget(self._tabs, 1)
        self._tabs.currentChanged.connect(self._protocol_tab_changed)
        self.setCentralWidget(outer)

        status = QStatusBar()
        self._status = QLabel("Disconnected")
        self._count = QLabel("0 frames")
        status.addWidget(self._status)
        status.addPermanentWidget(self._count)
        self.setStatusBar(status)
        self._frame_count = 0
        self._rx_count = 0
        self._tx_count = 0
        self._update_counts()

    def _protocol_tab_changed(self, index):
        self._adapter_stack.setCurrentIndex(index)
        self._status.setVisible(index == 0)
        self._count.setVisible(index == 0)

    def _open_script(self, path=None, new=False):
        window=ScriptWindow(self._script_controller,self)
        if not new and not window.open_script(path):
            window.close();return None
        self._script_windows.append(window)
        window.closed.connect(self._script_closed)
        window.open_log_requested.connect(self._open_logfile)
        window.close_main_ready.connect(self.close)
        window.show();window.raise_();window.activateWindow()
        return window

    def _open_logfile(self, path=None):
        window=LogWindow({'CAN':self._recording_panel, 'LIN':self._lin.recording,
                          'ETHERNET':self._ethernet.recording},self)
        if not window.open_log(path):
            window.close();return None
        self._script_windows.append(window)
        window.closed.connect(self._script_closed)
        window.close_main_ready.connect(self.close)
        window.show();window.raise_();window.activateWindow()
        return window

    def _open_demo_script(self):
        example=Path(__file__).resolve().parents[1]/'scripting'/'demo_can.py'
        return self._open_script(example)

    def _script_closed(self, window):
        if window in self._script_windows:self._script_windows.remove(window)

    def _current_backend(self) -> BackendSpec:
        key = self._backend.currentData()
        for spec in BACKENDS:
            if spec.key == key:
                return spec
        return BACKENDS[0]

    def _fill_channel(self, spec: BackendSpec) -> None:
        self._channel.blockSignals(True)
        self._channel.clear()
        self._discovery_note = ""
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
        elif spec.interface in {"vector", "neovi", "kvaser", "pcan", "nixnet"}:
            options, self._discovery_note = discover_channels(spec.interface)
            for option in options:
                self._channel.addItem(option.label, option.config)
            if not options:
                self._channel.setEditText(spec.default_channel)
        else:
            self._channel.addItem(spec.default_channel, spec.default_channel)
            self._channel.setEditText(spec.default_channel)
        self._channel.blockSignals(False)

    def _channel_value(self) -> str:
        data = self._channel.currentData()
        if isinstance(data, dict) and self._channel.currentText() == self._channel.itemText(self._channel.currentIndex()):
            return str(data["channel"])
        if self._channel.currentText() == self._channel.itemText(self._channel.currentIndex()) and isinstance(data, str) and data:
            return data
        return self._channel.currentText().split("—")[0].strip()

    def _apply_backend(self, spec: BackendSpec) -> None:
        self._fill_channel(spec)
        self._notes.setText(spec.notes + " " + self._discovery_note)
        self._vector_dll.setVisible(spec.interface == "vector")
        virtual = spec.interface == "virtual"
        self._demo.setEnabled(virtual)
        self._bitrate.setEnabled(not virtual)
        if spec.interface not in FD_INTERFACES:
            self._fd.setChecked(False)
        self._sync_fd_controls()
        self._sync_ni_controls()

    def _sync_ni_controls(self, *_):
        ni = self._current_backend().interface == "nixnet"
        self._termination.setVisible(ni)
        selected = self._channel.currentData()
        detected = isinstance(selected, dict) and self._channel.currentText() == self._channel.itemText(self._channel.currentIndex())
        available = not detected or selected.get("can_term_available", True)
        if not ni or not available:
            self._termination.setChecked(False)
        self._termination.setEnabled(ni and available and not self._service.is_connected)

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
        selected = self._channel.currentData()
        config = selected if isinstance(selected, dict) and self._channel.currentText() == self._channel.itemText(self._channel.currentIndex()) else None
        if spec.interface == "nixnet":
            config = dict(config or {}, can_termination=self._termination.isChecked())
        self._service.connect_bus(spec, channel, bitrate, inject, device_config=config,
                                  fd=self._fd.isChecked(), data_bitrate=int(self._data_bitrate.currentData()))

    def _on_connected(self, label: str) -> None:
        self._sync_ni_controls()
        self._explore.setEnabled(True)
        self._set_exploring(True)
        self._refresh.setEnabled(False)
        self._vector_dll.setEnabled(False)
        self._send.setEnabled(True)
        self._connect.setText("Disconnect")
        self._backend.setEnabled(False)
        self._channel.setEnabled(False)
        self._bitrate.setEnabled(False)
        self._demo.setEnabled(False)
        self._sync_fd_controls()
        self._status.setText(f"Connected · {label}")

    def _on_disconnected(self) -> None:
        self._set_exploring(False)
        self._explore.setEnabled(False)
        self._stop_repeated_send()
        self._refresh.setEnabled(True)
        self._vector_dll.setEnabled(True)
        self._send.setEnabled(False)
        self._connect.setText("Connect")
        self._backend.setEnabled(True)
        self._channel.setEnabled(True)
        self._apply_backend(self._current_backend())
        self._status.setText("Disconnected")

    def _on_message(self, msg) -> None:
        if not self._exploring:
            return
        self._model.append_message(msg)
        self._frame_count += 1
        if msg.is_rx:
            self._rx_count += 1
        else:
            self._tx_count += 1
        self._update_counts()
        if self._auto_scroll.isChecked():
            self._table.scrollToBottom()


    def _set_exploring(self, enabled):
        self._exploring = enabled
        self._explore.setText("Pause Exploring" if enabled else "Start Exploring")
        self._update_counts()

    def _toggle_exploring(self):
        if self._service.is_connected:
            self._set_exploring(not self._exploring)

    def _toggle_trace_mode(self, checked):
        self._model.set_history_mode(checked)
        self._update_counts()
        if checked:
            self._table.scrollToBottom()

    def _choose_vector_dll(self):
        path, _ = QFileDialog.getOpenFileName(self, "Select installed Vector XL API library", "", "Vector XL API (vxlapi64.dll)")
        if path:
            QSettings().setValue("drivers/vector_dll", path)
            QMessageBox.information(self, "Vector library saved", "Restart Signal Explorer to load this Vector XL library.")

    def _clear_trace(self):
        self._model.clear()
        self._frame_count = 0
        self._rx_count = 0
        self._tx_count = 0
        self._update_counts()

    def _update_counts(self):
        filtered = " | ID filter active" if self._proxy.ranges else ""
        self._count.setText(f"RX {self._rx_count} | TX {self._tx_count} | {self._proxy.rowCount()} visible rows{filtered}"
                            f" | {'Exploring' if self._exploring else 'Monitoring stopped'}")

    def _clear_filter(self):
        self._filter.clear()
        self._proxy.set_expression("")
        self._update_counts()

    def _apply_filter(self):
        try:
            self._proxy.set_expression(self._filter.text())
            self._update_counts()
        except ValueError as exc:
            QMessageBox.warning(self, "Invalid ID filter", str(exc))

    def _load_dbc(self):
        path, _ = QFileDialog.getOpenFileName(self, "Load DBC", "", "DBC files (*.dbc)")
        if not path:
            return
        try:
            database = cantools.database.load_file(path)
        except Exception as exc:
            QMessageBox.warning(self, "DBC load failed", str(exc))
            return
        self._model.set_database(database)
        self._recording_panel.database_path = path
        self._dbc_label.setText(f"{path.rsplit('/', 1)[-1]} ({len(database.messages)} messages)")
        self._dbc_label.setToolTip(path)

    def _unload_dbc(self):
        self._recording_panel.database_path = None
        self._model.set_database(None)
        self._dbc_label.setText("No DBC loaded")
        self._dbc_label.setToolTip("")

    def _open_dbc_editor(self):
        from signal_explorer.ui.dbc_editor import DbcEditorWindow

        path = self._recording_panel.database_path
        # Reuse the editor for this file so independent copies cannot overwrite one another.
        resolved = Path(path).resolve() if path else None
        for window in self._dbc_windows:
            if window.path == resolved:
                window.showNormal(); window.raise_(); window.activateWindow()
                return window
        window = DbcEditorWindow(self._model.database, path, self)
        self._dbc_windows.append(window)
        window.closed.connect(lambda closed: self._dbc_windows.remove(closed))
        window.saved.connect(self._dbc_editor_saved)
        window.show(); window.raise_(); window.activateWindow()
        return window

    def _dbc_editor_saved(self, path, database):
        active = self._recording_panel.database_path
        if active is not None and Path(active).resolve() == Path(path).resolve():
            self._model.set_database(database)
            self._recording_panel.database_path = path
            self._dbc_label.setText(f"{Path(path).name} ({len(database.messages)} messages)")
            self._dbc_label.setToolTip(path)

    def _send_message(self):
        if self._recording_panel.playback_busy:
            return
        if self._tx_timer.isActive():
            self._stop_repeated_send()
            return
        try:
            interval_text = self._tx_interval.text().strip()
            interval = None
            if interval_text:
                if not interval_text.isascii() or not interval_text.isdecimal():
                    raise ValueError("Interval must be a positive whole number of milliseconds, or blank to send once.")
                interval = int(interval_text)
                if not 1 <= interval <= 2147483647:
                    raise ValueError("Interval must be between 1 and 2147483647 milliseconds.")
            identifier = int(self._tx_id.text().strip(), 16)
            data = bytes.fromhex(self._tx_data.text())
            msg = can.Message(arbitration_id=identifier, is_extended_id=self._tx_extended.isChecked(),
                              data=data, is_fd=self._tx_fd.isChecked(),
                              bitrate_switch=self._tx_brs.isChecked(), check=True)
            validate_frame(msg, self._service.fd_enabled)
        except ValueError as exc:
            QMessageBox.warning(self, "Invalid CAN message", str(exc))
            return
        if not self._service.send_message(msg):
            return
        if interval is not None and self._service.is_connected:
            self._repeat_message = msg
            self._tx_timer.start(interval)
            self._set_repeat_controls(True)

    def _sync_fd_controls(self, *_):
        connected = self._service.is_connected
        panel = getattr(self, "_recording_panel", None)
        running = self._tx_timer.isActive() or (panel is not None and panel.playback_busy)
        supported = self._current_backend().interface in FD_INTERFACES
        self._fd.setEnabled(not connected and supported)
        self._data_bitrate.setEnabled(not connected and supported and self._fd.isChecked())
        enabled = connected and self._service.fd_enabled
        if not enabled:
            self._tx_fd.setChecked(False)
        self._tx_fd.setEnabled(enabled and not running)
        if not self._tx_fd.isChecked():
            self._tx_brs.setChecked(False)
        self._tx_brs.setEnabled(enabled and self._tx_fd.isChecked() and not running)
        self._tx_data.setPlaceholderText("0-64 bytes; FD lengths: 0-8,12,16,20,24,32,48,64" if self._tx_fd.isChecked()
                                         else "0-8 hexadecimal bytes, e.g. DE AD BE EF")

    def _set_repeat_controls(self, running):
        for widget in (self._tx_id, self._tx_extended, self._tx_data, self._tx_interval):
            widget.setEnabled(not running)
        self._sync_fd_controls()
        self._send.setText("Stop" if running else "Send")
        self._send.setEnabled((running or self._service.is_connected) and not self._recording_panel.playback_busy)

    def _on_playback_changed(self, running):
        if running:
            self._stop_repeated_send()
        self._set_repeat_controls(self._tx_timer.isActive())

    def _send_repeated_message(self):
        if self._repeat_message is None or not self._service.is_connected:
            self._stop_repeated_send()
            return
        if not self._service.send_message(self._repeat_message):
            self._stop_repeated_send()

    def _stop_repeated_send(self):
        self._tx_timer.stop()
        self._repeat_message = None
        self._set_repeat_controls(False)

    def _on_error(self, text: str) -> None:
        self._stop_repeated_send()
        self._status.setText(text)
        QMessageBox.warning(self, "Signal Explorer", text)

    def closeEvent(self, event) -> None:  # noqa: N802
        for window in list(self._dbc_windows):
            if not window.close():
                event.ignore(); return
        for window in list(self._script_windows):
            if not window.request_close(main=True):
                event.ignore();return
        self._stop_repeated_send()
        can_ready = self._recording_panel.request_close()
        lin_ready = self._lin.request_close()
        ethernet_ready = self._ethernet.request_close()
        if not can_ready or not lin_ready or not ethernet_ready:
            event.ignore()
            return
        self._service.disconnect_bus()
        self._lin.service.disconnect_bus()
        self._ethernet.service.disconnect_bus()
        super().closeEvent(event)
