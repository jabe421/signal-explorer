from __future__ import annotations
import logging
import os

from PySide6.QtCore import QSettings, QTimer, Qt
from PySide6.QtGui import QAction, QActionGroup
from signal_explorer.ui.adapter_preferences import AdapterPreference, CanChannelPreferences, can_channel_key, can_device_key
from pathlib import Path
from signal_explorer.bus.discovery import discover_channels
from signal_explorer.ui.can_channels import CanChannelsDialog, device_key, channel_label
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
    QTreeView,
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
from signal_explorer.bus.strategies import strategy_for
from signal_explorer.bus.service import TX_REPORT_INTERFACES
from signal_explorer.bus.canfd import FD_INTERFACES, DATA_BITRATES, validate_frame
from signal_explorer.ui.trace_model import SignalTreeModel, IdFilter
from signal_explorer.ui.trace_timestamps import TIMESTAMP_FORMATS
from signal_explorer.ui.signal_tree_view import SignalTreeView
from signal_explorer.ui.recording_panel import RecordingPanel
from signal_explorer.ui.lin_panel import LinPanel
from signal_explorer.ui.ethernet_panel import EthernetPanel
from signal_explorer.scripting.transport import CanScriptTransport
from signal_explorer.scripting.controller import ScriptController
from signal_explorer.ui.script_window import ScriptWindow
from signal_explorer.ui.log_window import LogWindow
from signal_explorer.bus.timing import SILENT_INTERFACES, TimingSettings
from signal_explorer.ui.timing_window import TimingWindow
from signal_explorer.ui.bus_health_window import BusHealthWindow
import can
import cantools

logger = logging.getLogger(__name__)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Signal Explorer")
        self.resize(1400, 720)

        self._service = BusService(self)
        self._service.connected.connect(self._on_connected)
        self._service.disconnected.connect(self._on_disconnected)
        self._service.messages.connect(self._on_messages)
        self._service.error.connect(self._on_error)
        self._service.connection_state_changed.connect(self._on_connection_state)
        self._close_after_disconnect = False
        self._main_tx_future = None
        self._main_tx_poll = QTimer(self)
        self._main_tx_poll.setInterval(10)
        self._main_tx_poll.timeout.connect(self._poll_main_tx)
        self._script_transport = CanScriptTransport(self._service, self)
        self._script_controller = ScriptController(self._script_transport, self)
        self._script_windows = []
        self._dbc_windows = []
        self._transmit_window = None
        self._timing_window = None
        self._replay_timing_window = None
        self._bus_health_window = None
        self._diagnostics_window = None
        self._timing_scan_busy = False
        self._timing_settings = {}

        self._exploring = False
        self._model = SignalTreeModel(history_mode=False)
        self._repeat_message = None
        self._tx_timer = QTimer(self)
        self._tx_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._tx_timer.timeout.connect(self._send_repeated_message)
        self._adapter_preference = AdapterPreference("can")
        self._build()
        self._counts_dirty = False
        self._display_drop_base = 0
        self._display_drops_seen = 0
        self._counts_timer = QTimer(self)
        self._counts_timer.setInterval(100)
        self._counts_timer.timeout.connect(self._refresh_counts)
        self._counts_timer.start()
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
        transmit = self.menuBar().addMenu('Transmit')
        sender_action = QAction('Multi-message sender...', self)
        sender_action.triggered.connect(self._open_transmit_window)
        transmit.addAction(sender_action)
        timing_menu = self.menuBar().addMenu('Timing')
        timing_action = timing_menu.addAction('CAN timing and bitrate detection...')
        timing_action.triggered.connect(self._open_timing_window)
        timing_menu.addAction('Replay timing...').triggered.connect(self._open_replay_timing)
        analysis = self.menuBar().addMenu('Analysis')
        analysis.addAction('CAN Bus Health...').triggered.connect(self._open_bus_health)
        trace_menu = self.menuBar().addMenu('Trace')
        timestamp_menu = trace_menu.addMenu('Timestamp format')
        self._timestamp_actions = QActionGroup(self)
        self._timestamp_actions.setExclusive(True)
        for mode, title, heading, explanation in TIMESTAMP_FORMATS:
            action = timestamp_menu.addAction(title)
            action.setData(mode)
            action.setCheckable(True)
            action.setChecked(mode == 'epoch')
            action.setStatusTip(f'CAN/CAN FD trace: {explanation}')
            self._timestamp_actions.addAction(action)
        self._timestamp_actions.triggered.connect(lambda action: self._set_timestamp_format(action.data()))
        help_menu = self.menuBar().addMenu('Help')
        help_menu.addAction('Diagnostics...').triggered.connect(self._open_diagnostics)
        self._backend = QComboBox()
        for spec in sorted(BACKENDS, key=lambda spec: spec.label.casefold()):
            self._backend.addItem(spec.label, spec.key)
        self._backend.setCurrentIndex(self._backend.findData("virtual"))
        self._backend.currentIndexChanged.connect(lambda _: self._apply_backend(self._current_backend()))
        self._backend.currentIndexChanged.connect(lambda _: self._adapter_preference.save(self._current_backend().key))

        self._channel = QComboBox()
        self._channel.setEditable(True)
        self._channel.setMinimumWidth(140)
        self._channel.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self._channel.setMinimumContentsLength(18)
        self._channel.setMaximumWidth(360)
        self._enabled_can_configs = []
        self._saved_channels_unavailable = False
        self._channel_options = []
        self._channel.currentIndexChanged.connect(self._reset_channel_selection)
        self._channel.currentIndexChanged.connect(lambda _: self._channel.lineEdit().setCursorPosition(0))
        self._channel.editTextChanged.connect(self._reset_channel_selection)
        self._channels_button = QPushButton('Enable channels...')
        self._channels_button.clicked.connect(self._choose_can_channels)
        self._bitrate = QComboBox()
        for rate in BITRATES:
            self._bitrate.addItem(f"{rate // 1000} kbit/s", rate)
        self._bitrate.setCurrentIndex(2)  # 500 k

        self._fd = QCheckBox("CAN FD")
        self._listen_only = QCheckBox('Listen-only')
        self._listen_only.setToolTip('Configure hardware silent mode before going online. All CAN transmission paths are blocked. Disconnect to change mode.')
        self._driver_tx_reports = QCheckBox('Use driver TX reports')
        self._driver_tx_reports.setToolTip('Use driver-reported TX events/timestamps instead of synthetic host echoes. '
                                          'Requires compatible drivers. A report is not proof of intended-ECU receipt. '
                                          'Send still returns local acceptance time; wait for direction="tx" in scripts for the report.')
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
        bar.addWidget(self._channels_button)
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

        self._table = SignalTreeView()
        self._proxy = IdFilter(self)
        self._proxy.setSourceModel(self._model)
        self._table.setModel(self._proxy)
        self._proxy.filteringAboutToChange.connect(self._table.collapseAll)
        self._table.setUniformRowHeights(True)
        self._table.expanded.connect(lambda index: self._model.set_expanded(self._proxy.mapToSource(index), True))
        self._table.collapsed.connect(lambda index: self._model.set_expanded(self._proxy.mapToSource(index), False))
        self._table.setAlternatingRowColors(True)
        self._table.setSelectionBehavior(QTreeView.SelectionBehavior.SelectRows)
        self._table.header().setStretchLastSection(True)
        self._table.setColumnWidth(0, 165)
        self._table.setColumnWidth(3, 175)
        self._table.setColumnWidth(7, 210)
        self._table.setColumnWidth(8, 160)

        root = QVBoxLayout()
        # Adapter controls are stacked above the protocol tabs.
        fd_bar = QHBoxLayout()
        fd_bar.addWidget(self._fd)
        fd_bar.addWidget(QLabel("Data bitrate (FD)"))
        fd_bar.addWidget(self._data_bitrate)
        fd_bar.addWidget(self._termination)
        fd_bar.addWidget(self._driver_tx_reports)
        fd_bar.addWidget(self._listen_only)
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
        self._auto_scroll.setChecked(False)
        self._auto_scroll.setToolTip("Checked: timestamped message history with auto-scroll. Unchecked: latest data per CAN ID without scrolling.")
        self._auto_scroll.toggled.connect(self._toggle_trace_mode)
        filters.addWidget(self._auto_scroll)
        root.addLayout(filters)
        root.addWidget(self._table)

        send_bar = QHBoxLayout()
        self._tx_id = QLineEdit("100")
        self._tx_channel = QComboBox()
        self._tx_channel.setMinimumWidth(90)
        self._tx_channel.setEnabled(False)
        self._tx_channel.currentIndexChanged.connect(self._select_tx_channel)
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
        for widget in (QLabel('TX channel'), self._tx_channel, QLabel("Send ID (hex)"), self._tx_id, self._tx_extended, self._tx_fd, self._tx_brs,
                       QLabel("Data (hex)"), self._tx_data,
                       QLabel("Interval (ms)"), self._tx_interval, self._send):
            send_bar.addWidget(widget)
        self._multi_send = QPushButton('Multi-message TX...')
        self._multi_send.clicked.connect(self._open_transmit_window)
        send_bar.addWidget(self._multi_send)
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
        self._channel_options = []
        self._enabled_can_configs = []
        self._saved_channels_unavailable = False
        self._channels_button.setText('Enable channels...')
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
        elif strategy_for(spec.interface).enumerates_channels:
            options, self._discovery_note = discover_channels(spec.interface)
            self._channel_options = options
            for option in options:
                self._channel.addItem(option.label, option.config)
            preferences = CanChannelPreferences(spec.interface)
            preferred_device = preferences.last_device()
            index = next((i for i, option in enumerate(options) if device_key(option) == preferred_device), 0)
            if options:
                self._channel.setCurrentIndex(index)
                self._restore_enabled_channels(options[index])
            if not options:
                self._channel.setEditText(spec.default_channel)
        else:
            self._channel.addItem(spec.default_channel, spec.default_channel)
            self._channel.setEditText(spec.default_channel)
        self._channel.blockSignals(False)
        self._channel.lineEdit().setCursorPosition(0)
        self._channel.setToolTip(self._channel.currentText())
        self._channels_button.setEnabled(bool(self._channel_options))

    def _restore_enabled_channels(self, option):
        saved = CanChannelPreferences(self._current_backend().interface).channels(device_key(option))
        if saved is None:
            return
        matches = [o for key in saved for o in self._channel_options
                   if device_key(o) == device_key(option) and can_channel_key(o.config) == key]
        self._enabled_can_configs = [dict(o.config, channel_label=channel_label(self._current_backend().interface, o.config)) for o in matches]
        self._saved_channels_unavailable = not matches
        if matches:
            self._channel.setCurrentIndex(self._channel_options.index(matches[0]))
            self._channels_button.setText(f'{len(matches)} channels enabled')
        else:
            self._channels_button.setText('Saved channels unavailable')

    def _reset_channel_selection(self, *_):
        self._enabled_can_configs = []
        self._saved_channels_unavailable = False
        self._channels_button.setText('Enable channels...')
        selected = self._channel.currentData()
        if isinstance(selected, dict) and self._channel.currentText() == self._channel.itemText(self._channel.currentIndex()):
            option = next((o for o in self._channel_options if o.config == selected), None)
            if option:
                CanChannelPreferences(self._current_backend().interface).save(device_key(option), [selected])

    def _choose_can_channels(self):
        selected = self._channel.currentData()
        current = self._enabled_can_configs or ([selected] if isinstance(selected, dict) else [])
        if self._saved_channels_unavailable:
            current = []
        dialog = CanChannelsDialog(self._channel_options, self._current_backend().interface, current, self)
        if dialog.exec():
            configs = dialog.selected_configs
            index = next((i for i in range(self._channel.count()) if isinstance(self._channel.itemData(i), dict)
                          and self._channel.itemData(i).get('channel') == configs[0]['channel']
                          and self._channel.itemData(i).get('serial') == configs[0].get('serial')), -1)
            if index >= 0:
                self._channel.blockSignals(True)
                self._channel.setCurrentIndex(index)
                self._channel.blockSignals(False)
            self._enabled_can_configs = configs
            self._saved_channels_unavailable = False
            option = next(o for o in self._channel_options if o.config['channel'] == configs[0]['channel']
                          and can_device_key(o.config) == can_device_key(configs[0]))
            CanChannelPreferences(self._current_backend().interface).save(device_key(option), configs)
            self._channels_button.setText(f'{len(configs)} channels enabled')
            self._channel.lineEdit().setCursorPosition(0)
            self._sync_ni_controls()

    def _open_transmit_window(self):
        if self._transmit_window is None:
            from signal_explorer.ui.transmit_window import TransmitWindow
            self._transmit_window = TransmitWindow(self._service, lambda:self._model.database,
                                                   lambda:self._recording_panel.playback_busy, self)
            self._recording_panel.playback_changed.connect(self._transmit_window.playback_changed)
        self._transmit_window.show()
        self._transmit_window.raise_()
        self._transmit_window.activateWindow()
        return self._transmit_window

    def _select_tx_channel(self, *_):
        if self._service.is_connected and self._tx_channel.currentData() is not None:
            self._service.select_tx_channel(self._tx_channel.currentData())

    def _channel_value(self) -> str:
        data = self._channel.currentData()
        if isinstance(data, dict) and self._channel.currentText() == self._channel.itemText(self._channel.currentIndex()):
            return str(data["channel"])
        if self._channel.currentText() == self._channel.itemText(self._channel.currentIndex()) and isinstance(data, str) and data:
            return data
        return self._channel.currentText().split("—")[0].strip()

    def _apply_backend(self, spec: BackendSpec) -> None:
        if self._timing_scan_busy:
            return
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

        settings = self._timing_settings.get(spec.interface)
        if settings is not None:
            self._apply_timing_settings(settings)
        else:
            for combo, rates in ((self._bitrate, BITRATES), (self._data_bitrate, DATA_BITRATES)):
                while combo.count() > len(rates): combo.removeItem(combo.count()-1)

    def _sync_ni_controls(self, *_):
        ni = strategy_for(self._current_backend().interface).supports_termination
        self._termination.setVisible(ni)
        selected = self._channel.currentData()
        detected = isinstance(selected, dict) and self._channel.currentText() == self._channel.itemText(self._channel.currentIndex())
        available = not detected or selected.get("can_term_available", True)
        if not ni or not available:
            self._termination.setChecked(False)
        self._termination.setEnabled(ni and available and not self._service.is_connected)

    def _toggle_connection(self) -> None:
        if self._timing_scan_busy:
            return
        if self._service.busy:
            self._service.cancel_connect()
            return
        if self._service.is_connected:
            self._service.disconnect_async()
            return
        spec = self._current_backend()
        if self._saved_channels_unavailable:
            QMessageBox.warning(self, 'CAN channels', 'The saved CAN channels are unavailable. Use Enable channels to choose available channels before connecting.')
            return
        bitrate = int(self._bitrate.currentData())
        channel = self._channel_value()
        if not channel:
            QMessageBox.warning(self, "Signal Explorer", "Channel is required (COM port or index).")
            return
        inject = self._demo.isChecked() and spec.interface == "virtual"
        selected = self._channel.currentData()
        config = selected if isinstance(selected, dict) and self._channel.currentText() == self._channel.itemText(self._channel.currentIndex()) else None
        if strategy_for(spec.interface).supports_termination:
            config = dict(config or {}, can_termination=self._termination.isChecked())
        if self._enabled_can_configs:
            configs = [dict(c) for c in self._enabled_can_configs]
            if strategy_for(spec.interface).supports_termination:
                for c in configs:
                    c['can_termination'] = self._termination.isChecked()
            config = dict(configs[0], channels=configs)
        self._service.connect_async(spec, channel, bitrate, inject, device_config=config,
                                  fd=self._fd.isChecked(), data_bitrate=int(self._data_bitrate.currentData()),
                                  driver_tx_reports=self._driver_tx_reports.isChecked(),
                                  listen_only=self._listen_only.isChecked(),
                                  timing_settings=self._timing_settings.get(spec.interface))

    def _on_connected(self, label: str) -> None:
        self._model.start_session(self._service.session_started_at)
        self._driver_tx_reports.setChecked(self._service.driver_tx_reports)
        self._listen_only.setChecked(self._service.listen_only)
        self._display_drop_base = 0
        selected = self._channel.currentData()
        option = next((o for o in self._channel_options if o.config == selected), None)
        if option:
            preferences = CanChannelPreferences(self._current_backend().interface)
            if preferences.channels(device_key(option)) is None:
                preferences.save(device_key(option), [selected])
            else:
                preferences.remember_device(device_key(option))
        self._channels_button.setEnabled(False)
        self._tx_channel.blockSignals(True)
        self._tx_channel.clear()
        for cfg in self._service.channels:
            self._tx_channel.addItem(cfg['label'], cfg['native_channel'])
        self._tx_channel.blockSignals(False)
        self._tx_channel.setEnabled(True)
        self._model.set_channel_labels({str(c['native_channel']): c['label'] for c in self._service.channels})
        self._sync_ni_controls()
        self._explore.setEnabled(True)
        self._set_exploring(True)
        self._refresh.setEnabled(False)
        self._vector_dll.setEnabled(False)
        self._send.setEnabled(self._service.can_send)
        self._connect.setText("Disconnect")
        self._backend.setEnabled(False)
        self._channel.setEnabled(False)
        self._bitrate.setEnabled(False)
        self._demo.setEnabled(False)
        self._sync_fd_controls()
        self._status.setText(f"Connected · {label}")

    def _on_connection_state(self, state):
        if state in ('connecting', 'cancelling', 'disconnecting'):
            for widget in (self._backend,self._channel,self._channels_button,self._bitrate,self._fd,
                           self._data_bitrate,self._listen_only,self._driver_tx_reports,self._termination,
                           self._refresh,self._vector_dll,self._demo,self._send):
                widget.setEnabled(False)
            self._connect.setText('Connecting… (Cancel)' if state=='connecting' else 'Waiting for driver…')
            self._connect.setEnabled(state=='connecting')
            self._status.setText({'connecting':'Connecting…','cancelling':'Cancelling connection; waiting for driver cleanup…',
                                  'disconnecting':'Disconnecting; waiting for outstanding driver calls…'}[state])
        else:
            self._connect.setEnabled(True)
            if state == 'disconnected' and not self._service.is_connected and self._connect.text() != 'Connect':
                self._on_disconnected()
        if self._timing_window is not None: self._timing_window.sync_controls()

    def _on_disconnected(self) -> None:
        self._tx_channel.setEnabled(False)
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
        if self._close_after_disconnect: QTimer.singleShot(0, self.close)

    def _on_message(self, msg) -> None:
        # Compatibility for explicit single-frame UI callers; service delivery
        # uses owned snapshots through _on_messages.
        self._on_messages([msg], owned=False)

    def _on_messages(self, messages, owned=True) -> None:
        if not self._exploring or not messages:
            return
        self._model.append_messages(messages, owned=owned)
        self._frame_count += len(messages)
        received = sum(msg.is_rx for msg in messages)
        self._rx_count += received
        self._tx_count += len(messages) - received
        self._counts_dirty = True
        if self._auto_scroll.isChecked():
            self._table.scrollToBottom()


    def _set_exploring(self, enabled):
        self._exploring = enabled
        self._explore.setText("Pause Exploring" if enabled else "Start Exploring")
        self._update_counts()

    def _toggle_exploring(self):
        if self._service.is_connected:
            self._service.advance_display_epoch()
            self._set_exploring(not self._exploring)

    def _toggle_trace_mode(self, checked):
        self._model.set_history_mode(checked)
        self._update_counts()
        if checked:
            self._table.scrollToBottom()

    def _set_timestamp_format(self, mode):
        self._model.set_timestamp_mode(mode)
        if mode == 'absolute':
            self._table.setColumnWidth(0, max(250, self._table.columnWidth(0)))

    def _choose_vector_dll(self):
        path, _ = QFileDialog.getOpenFileName(self, "Select installed Vector XL API library", "", "Vector XL API (vxlapi64.dll)")
        if path:
            QSettings().setValue("drivers/vector_dll", path)
            QMessageBox.information(self, "Vector library saved", "Restart Signal Explorer to load this Vector XL library.")

    def _clear_trace(self):
        self._service.advance_display_epoch()
        self._model.clear()
        self._frame_count = 0
        self._rx_count = 0
        self._tx_count = 0
        self._display_drop_base = self._service.ui_dropped
        self._update_counts()

    def _refresh_counts(self):
        if self._counts_dirty or self._display_drops_seen != self._service.ui_dropped:
            self._update_counts()

    def _update_counts(self):
        self._counts_dirty = False
        self._display_drops_seen = self._service.ui_dropped
        dropped = max(0, self._display_drops_seen - getattr(self, '_display_drop_base', 0))
        drops = f' | Display drops {dropped}' if dropped else ''
        filtered = " | ID filter active" if self._proxy.ranges else ""
        self._count.setText(f"RX {self._rx_count} | TX {self._tx_count} | {self._proxy.rowCount()} visible rows{filtered}"
                            f" | {'Exploring' if self._exploring else 'Monitoring stopped'}{drops}")

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
        if not self._queue_main_tx(msg):
            return
        if interval is not None and self._service.is_connected:
            self._repeat_message = msg
            self._tx_timer.start(interval)
            self._set_repeat_controls(True)

    def _sync_fd_controls(self, *_):
        connected = self._service.is_connected or self._service.busy or self._timing_scan_busy
        silent_supported = self._current_backend().interface in SILENT_INTERFACES
        if not connected and not silent_supported:
            self._listen_only.setChecked(False)
        self._listen_only.setEnabled(not connected and silent_supported)
        panel = getattr(self, "_recording_panel", None)
        running = self._tx_timer.isActive() or (panel is not None and panel.playback_busy)
        supported = self._current_backend().interface in FD_INTERFACES
        reports_supported = self._current_backend().interface in TX_REPORT_INTERFACES
        if not connected and not reports_supported:
            self._driver_tx_reports.setChecked(False)
        self._driver_tx_reports.setEnabled(not connected and reports_supported)
        self._fd.setEnabled(not connected and supported)
        self._data_bitrate.setEnabled(not connected and supported and self._fd.isChecked()
                                     and self._timing_settings.get(self._current_backend().interface) is None)
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
        self._tx_channel.setEnabled(self._service.is_connected and not running and not self._recording_panel.playback_busy)
        for widget in (self._tx_id, self._tx_extended, self._tx_data, self._tx_interval):
            widget.setEnabled(not running)
        self._sync_fd_controls()
        self._send.setText("Stop" if running else "Send")
        self._send.setEnabled((running or (self._service.can_send and self._main_tx_future is None))
                              and not self._recording_panel.playback_busy)

    def _on_playback_changed(self, running):
        if running:
            self._stop_repeated_send()
        self._set_repeat_controls(self._tx_timer.isActive())

    def _send_repeated_message(self):
        if self._repeat_message is None or not self._service.is_connected:
            self._stop_repeated_send()
            return
        if not self._queue_main_tx(self._repeat_message):
            self._stop_repeated_send()

    def _queue_main_tx(self, msg):
        if self._main_tx_future is not None:
            return True  # Coalesce missed periodic ticks; never build a backlog.
        self._main_tx_future = self._service.submit_message(msg)
        self._main_tx_poll.start()
        if not self._tx_timer.isActive(): self._send.setEnabled(False)
        return True

    def _poll_main_tx(self):
        future = self._main_tx_future
        if future is None or not future.done(): return
        self._main_tx_future = None
        self._main_tx_poll.stop()
        self._set_repeat_controls(self._tx_timer.isActive())
        if not future.cancelled():
            try: future.result()
            except Exception as exc: self._on_error(f'Send failed: {exc}')

    def _stop_repeated_send(self):
        self._tx_timer.stop()
        if self._main_tx_future is not None: self._main_tx_future.cancel()
        self._repeat_message = None
        self._set_repeat_controls(False)

    def _on_error(self, text: str) -> None:
        logger.error('CAN: %s', text)
        self._stop_repeated_send()
        self._status.setText(text)
        QMessageBox.warning(self, "Signal Explorer", text)

    def _diagnostics_context(self):
        def options(combo):
            return [dict(label=combo.itemText(i), config=combo.itemData(i)) for i in range(combo.count())]
        return dict(can_session=self._service.session_info,
                    can_connected=self._service.is_connected,
                    detected_can_channels=[dict(label=o.label, config=o.config) for o in self._channel_options],
                    can_discovery_status=self._notes.text(),
                    selected_can_backend=self._current_backend().interface,
                    selected_can_channel=self._channel.currentText(),
                    selected_can_bitrate=self._bitrate.currentData(),
                    selected_can_fd=self._fd.isChecked(),
                    enabled_can_channels=self._enabled_can_configs,
                    lin_session=self._lin.service.session_info,
                    lin_connected=self._lin.service.is_connected,
                    detected_lin_channels=options(self._lin.channel),
                    ethernet_session=self._ethernet.service.session_info,
                    ethernet_connected=self._ethernet.service.is_connected,
                    detected_ethernet_adapters=options(self._ethernet.adapter),
                    configured_vector_dll=QSettings().value('drivers/vector_dll', ''),
                    environment_vector_dll=os.getenv('SIGNAL_EXPLORER_VECTOR_DLL', ''),
                    can_ui_dropped=self._service.ui_dropped,
                    recording_loss=dict(can=self._recording_panel.recorder.loss_statistics(),
                                        lin=self._lin.recording.recorder.loss_statistics(),
                                        ethernet=self._ethernet.recording.recorder.loss_statistics()),
                    replay_timing={name: panel.playback.timing_snapshot() if panel.playback_busy else panel.last_replay_timing
                                   for name, panel in self._replay_panels().items()})

    def _replay_panels(self):
        return {'CAN': self._recording_panel, 'LIN': self._lin.recording, 'Ethernet': self._ethernet.recording}

    def _open_replay_timing(self):
        if self._replay_timing_window is None:
            from signal_explorer.ui.replay_timing_window import ReplayTimingWindow
            self._replay_timing_window = ReplayTimingWindow(self._replay_panels(), self)
        self._replay_timing_window.show()
        self._replay_timing_window.raise_()
        self._replay_timing_window.activateWindow()

    def _open_diagnostics(self):
        if self._diagnostics_window is None:
            from signal_explorer.ui.diagnostics_window import DiagnosticsWindow
            self._diagnostics_window = DiagnosticsWindow(self._diagnostics_context, self)
        self._diagnostics_window.refresh()
        self._diagnostics_window.show()
        self._diagnostics_window.raise_()
        self._diagnostics_window.activateWindow()

    def closeEvent(self, event) -> None:  # noqa: N802
        if self._service.busy:
            self._close_after_disconnect = True
            self._service.cancel_connect()
            event.ignore()
            return
        self._close_after_disconnect = False
        if self._timing_window is not None and self._timing_window.busy:
            self._timing_window.cancel_scan()
            event.ignore()
            return
        if self._transmit_window is not None and not self._transmit_window.request_close_for_main():
            event.ignore();return
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
        if self._transmit_window is not None:
            self._transmit_window.shutdown()
        if self._service.is_connected and self._service._interface != 'virtual':
            self._close_after_disconnect = True
            self._service.disconnect_async()
            event.ignore()
            return
        self._service.disconnect_bus()
        self._main_tx_poll.stop()
        self._main_tx_future = None
        self._lin.service.disconnect_bus()
        self._ethernet.service.disconnect_bus()
        self._counts_timer.stop()
        if self._timing_window is not None:
            self._timing_window.close()
        if self._bus_health_window is not None:
            self._bus_health_window.close()
        super().closeEvent(event)

    def _open_bus_health(self):
        if self._bus_health_window is None:
            def losses():
                recorder = self._recording_panel.recorder
                return dict(script_drops=self._script_controller._events.stats()['dropped'],
                            recorder=recorder.problem or ('Recording' if recorder.recording else 'Idle / no overflow reported'))
            self._bus_health_window = BusHealthWindow(self._service, losses, self)
        self._bus_health_window.show(); self._bus_health_window.raise_(); self._bus_health_window.activateWindow()
        return self._bus_health_window

    def _timing_context(self):
        from signal_explorer.bus.service import _typed_channel
        spec = self._current_backend()
        selected = self._channel.currentData()
        config = selected if isinstance(selected, dict) and self._channel.currentText() == self._channel.itemText(self._channel.currentIndex()) else {}
        args = dict(spec.extra_kwargs, interface=spec.interface,
                    channel=_typed_channel(spec.interface, self._channel_value()), ignore_config=True)
        for key in ('serial', 'channel_index', 'app_name'):
            if key in config: args[key] = config[key]
        if strategy_for(spec.interface).supports_termination: args['can_termination'] = self._termination.isChecked()
        return dict(interface=spec.interface, label=spec.label, channel=self._channel.currentText(),
                    bitrate=int(self._bitrate.currentData()), data_bitrate=int(self._data_bitrate.currentData()),
                    fd=self._fd.isChecked(), connected=self._service.is_connected or self._service.busy,
                    settings=self._timing_settings.get(spec.interface), scan_kwargs=args)

    def _open_timing_window(self):
        if self._timing_window is None:
            self._timing_window = TimingWindow(self._timing_context, self._apply_timing_settings, self)
            self._timing_window.scan_busy.connect(self._timing_scan_changed)
            self._backend.currentIndexChanged.connect(self._timing_window.refresh_context)
            self._fd.toggled.connect(self._timing_window.refresh_context)
            self._service.connected.connect(self._timing_window.sync_controls)
            self._service.disconnected.connect(self._timing_window.sync_controls)
        self._timing_window.refresh_context()
        self._timing_window.show(); self._timing_window.raise_(); self._timing_window.activateWindow()
        return self._timing_window

    def _apply_timing_settings(self, settings, detected=False):
        if self._service.is_connected:
            return
        self._timing_settings[self._current_backend().interface] = settings
        for combo, rates, value in ((self._bitrate, BITRATES, settings.bitrate if settings else 500000),
                                    (self._data_bitrate, DATA_BITRATES, settings.data_bitrate if settings else 2000000)):
            while combo.count() > len(rates): combo.removeItem(combo.count()-1)
            if combo.findData(value)<0: combo.addItem(f'{value} bit/s (custom)', value)
            combo.setCurrentIndex(combo.findData(value))
        if detected: self._listen_only.setChecked(True)
        self._bitrate.setEnabled(settings is None and self._current_backend().interface != 'virtual' and not self._timing_scan_busy)
        self._sync_fd_controls()

    def _timing_scan_changed(self, busy):
        self._timing_scan_busy = busy
        for widget in (self._connect,self._backend,self._channel,self._refresh,self._vector_dll,self._demo):
            widget.setEnabled(not busy)
        self._channels_button.setEnabled(not busy and bool(self._channel_options))
        self._bitrate.setEnabled(not busy and self._timing_settings.get(self._current_backend().interface) is None and self._current_backend().interface != 'virtual')
        self._sync_fd_controls(); self._sync_ni_controls()
        if busy: self._termination.setEnabled(False)
        self._demo.setEnabled(not busy and self._current_backend().interface == 'virtual')
