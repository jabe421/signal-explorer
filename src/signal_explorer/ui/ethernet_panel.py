"""Ethernet monitor/editor for simulation and discovered native adapters."""
from PySide6.QtCore import Qt,QTimer,Slot
from PySide6.QtWidgets import (QWidget,QVBoxLayout,QHBoxLayout,QLabel,QPushButton,QComboBox,QCheckBox,
                               QLineEdit,QSpinBox,QTableView,QPlainTextEdit,QMessageBox,QGroupBox)
from signal_explorer.ethernet.model import (LOCAL_MAC,PEER_MAC,PRESETS,preset,make_frame,decode)
from signal_explorer.ethernet.service import EthernetService
from signal_explorer.ethernet.codec import EthernetCodec
from signal_explorer.ethernet.discovery import discover_adapters,virtual_adapter
from signal_explorer.ui.ethernet_trace_model import EthernetTraceModel,EthernetFilter
from signal_explorer.ui.recording_panel import RecordingPanel


class EthernetPanel(QWidget):
    def __init__(self,parent=None):
        super().__init__(parent)
        self.service=EthernetService(self);self.exploring=False;self.rx=self.tx=0
        self._repeat_frame=None
        self._scanned=False
        self._repeat=QTimer(self);self._repeat.setTimerType(Qt.TimerType.PreciseTimer);self._repeat.timeout.connect(self._send_repeat)
        self._build()
        self.service.connected.connect(self._connected)
        self.service.disconnected.connect(self._disconnected)
        self.service.message.connect(self._message)
        self.service.error.connect(self._error)
        self.service.statistics_changed.connect(self._refresh)
        self._load_test();self._refresh()

    def _build(self):
        self.adapter_widget=QWidget();adapter_layout=QVBoxLayout(self.adapter_widget);adapter_layout.setContentsMargins(0,0,0,0)
        bar=QHBoxLayout();adapter_layout.addLayout(bar)
        bar.addWidget(QLabel('Ethernet adapter:'))
        self.adapter=QComboBox();self.adapter.setMinimumWidth(300)
        self.adapter.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.adapter.addItem(virtual_adapter().label,virtual_adapter())
        self.adapter.currentIndexChanged.connect(self._adapter_changed)
        bar.addWidget(self.adapter,1)
        self.scan=QPushButton('Refresh adapters');self.scan.clicked.connect(self._scan_adapters);bar.addWidget(self.scan)
        self.demo=QCheckBox('Inject demo traffic');self.demo.setChecked(True)
        self.peer=QCheckBox('Simulated peer replies');self.peer.setChecked(True)
        self.peer.setToolTip('Replies to valid UDP and raw 88B5 test frames addressed to 02:00:00:00:00:02. No real network traffic.')
        self.connect_button=QPushButton('Connect Ethernet');self.connect_button.clicked.connect(self._toggle_connection)
        self.connect_button.setMinimumWidth(145)
        bar.addWidget(self.connect_button)
        options=QHBoxLayout();adapter_layout.addLayout(options)
        self.promiscuous=QCheckBox('Promiscuous capture')
        self.promiscuous.setToolTip('PC Ethernet only: capture other destination MACs visible at this port. A switch mirror/TAP is required to see other switch ports.')
        for widget in (self.demo,self.peer,self.promiscuous):options.addWidget(widget)
        options.addStretch()
        root=QVBoxLayout(self)
        self.note=QLabel();self.note.setWordWrap(True);root.addWidget(self.note)
        self.scan_status=QLabel('Adapter scan runs when the Ethernet tab is opened.');self.scan_status.setWordWrap(True)
        root.addWidget(self.scan_status)
        tools=QHBoxLayout()
        self.explore=QPushButton('Start Exploring');self.explore.clicked.connect(self._toggle_exploring)
        clear=QPushButton('Clear');clear.clicked.connect(self._clear)
        self.auto_scroll=QCheckBox('Auto-scroll (all frames)');self.auto_scroll.setChecked(True)
        self.auto_scroll.setToolTip('Checked: chronological frames, up to 10,000. Unchecked: update up to 10,000 retained Ethernet flows with cumulative counts.')
        self.auto_scroll.toggled.connect(lambda enabled:self.model.set_history_mode(enabled))
        self.search=QLineEdit();self.search.setPlaceholderText('Find MAC, IP, protocol or data; space-separated terms')
        self.type_filter=QLineEdit();self.type_filter.setMaximumWidth(125);self.type_filter.setPlaceholderText('e.g. 0800')
        self.direction=QComboBox()
        for text,key in [('RX + TX','all'),('RX only','rx'),('TX only','tx')]:self.direction.addItem(text,key)
        self.direction.currentIndexChanged.connect(self._apply_filter)
        apply=QPushButton('Apply filter');apply.clicked.connect(self._apply_filter)
        reset=QPushButton('Clear filter');reset.clicked.connect(self._clear_filter)
        self.search.returnPressed.connect(self._apply_filter);self.type_filter.returnPressed.connect(self._apply_filter)
        for w in (self.explore,clear,self.auto_scroll,self.search,QLabel('EtherType'),self.type_filter,self.direction,apply,reset):tools.addWidget(w)
        root.addLayout(tools)
        self.model=EthernetTraceModel();self.proxy=EthernetFilter(self);self.proxy.setSourceModel(self.model)
        self.table=QTableView();self.table.setModel(self.proxy);self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows);self.table.verticalHeader().hide()
        self.table.horizontalHeader().setStretchLastSection(True)
        for col,width in ((0,150),(4,150),(5,150),(11,320),(12,300)):self.table.setColumnWidth(col,width)
        self.table.selectionModel().selectionChanged.connect(self._selection_changed)
        root.addWidget(self.table,1)
        self.details=QPlainTextEdit();self.details.setReadOnly(True);self.details.setMaximumHeight(92)
        self.details.setPlaceholderText('Select a row to inspect the complete frame bytes (without FCS).');root.addWidget(self.details)
        tests=QHBoxLayout();self.test=QComboBox();self.test.addItems(PRESETS)
        self.load_test=QPushButton('Load Test Frame');self.load_test.clicked.connect(self._load_test)
        self.inject=QPushButton('Inject Test RX');self.inject.clicked.connect(lambda:self._try(lambda:self.service.inject_test(self.test.currentText())))
        for w in (QLabel('Test frames'),self.test,self.load_test,self.inject):tests.addWidget(w)
        tests.addStretch();root.addLayout(tests)
        self.editor=QGroupBox('Transmit Ethernet frame');edit=QVBoxLayout(self.editor);row=QHBoxLayout()
        self.source=QLineEdit(LOCAL_MAC);self.destination=QLineEdit(PEER_MAC);self.ethertype=QLineEdit('88B5');self.ethertype.setMaximumWidth(80)
        self.tagged=QCheckBox('802.1Q VLAN');self.vlan=QSpinBox();self.vlan.setRange(0,4094);self.vlan.setValue(100)
        self.pcp=QSpinBox();self.pcp.setRange(0,7)
        self.interval=QLineEdit();self.interval.setPlaceholderText('Blank = once');self.interval.setMaximumWidth(130)
        self.send=QPushButton('Send');self.send.clicked.connect(self._send)
        self.tagged.toggled.connect(self._refresh)
        for w in (QLabel('Source MAC'),self.source,QLabel('Destination MAC'),self.destination,QLabel('EtherType (hex)'),self.ethertype,
                  self.tagged,self.vlan,QLabel('PCP'),self.pcp,QLabel('Interval (ms)'),self.interval,self.send):row.addWidget(w)
        edit.addLayout(row)
        self.payload=QPlainTextEdit();self.payload.setMaximumHeight(72);self.payload.setPlaceholderText('Payload after EtherType, in hexadecimal. Maximum 1500 bytes.')
        edit.addWidget(self.payload)
        hint=QLabel('Payload is raw: editing IPv4/UDP bytes does not repair their checksums. Test presets generate valid headers/checksums. Short frames are padded to 60 bytes, excluding FCS.')
        hint.setWordWrap(True);edit.addWidget(hint);root.addWidget(self.editor)
        self.recording=RecordingPanel(self.service,self,codec=EthernetCodec(),
                                      playback_codec_factory=EthernetCodec,
                                      replay_label=lambda:'Playback Log as Simulated TX' if self._selected().backend=='virtual' else 'Playback Log as TX')
        self.recording.playback_changed.connect(self._playback_changed)
        root.addWidget(self.recording)
        schema=QLabel('MF4 uses the custom SignalExplorer_EthernetFrame schema. TX means API acceptance, not confirmed reception. NICs may omit padding or offload checksums/VLANs.')
        schema.setWordWrap(True);root.addWidget(schema)
        self.status=QLabel();root.addWidget(self.status)

    def _try(self,callback):
        try:return callback()
        except Exception as exc:self._error(str(exc))

    def _error(self,text):
        self._stop_repeat();QMessageBox.warning(self,'Ethernet',text)

    def showEvent(self,event):
        super().showEvent(event)
        if not self._scanned:
            self._scanned=True;QTimer.singleShot(0,self._scan_adapters)

    def _selected(self):return self.adapter.currentData() or virtual_adapter()

    def _scan_adapters(self):
        if self.service.is_connected:return
        previous=self._selected().identity
        options,report=discover_adapters()
        self.adapter.blockSignals(True);self.adapter.clear()
        selected=0
        for index,option in enumerate(options):
            self.adapter.addItem(option.label,option)
            self.adapter.setItemData(index,option.detail,Qt.ItemDataRole.ToolTipRole)
            self.adapter.model().item(index).setEnabled(option.available)
            if option.identity==previous and option.available:selected=index
        self.adapter.setCurrentIndex(selected);self.adapter.blockSignals(False)
        self.scan_status.setText(report);self._scanned=True;self._adapter_changed()

    def _adapter_changed(self,*_):
        if not hasattr(self,'recording'):return
        self.source.setText(self._selected().config.get('mac',LOCAL_MAC))
        self._refresh();self.recording._refresh()

    def _toggle_connection(self):
        if self.service.is_connected:self.service.disconnect_bus()
        else:
            option=self._selected()
            if option.backend=='virtual':self._try(lambda:self.service.connect_simulated(self.demo.isChecked(),self.peer.isChecked()))
            elif option.available:
                config={**option.config,'promiscuous':self.promiscuous.isChecked()}
                self._try(lambda:self.service.connect_hardware(option.backend,config))

    def _connected(self,*_):self.exploring=True;self._refresh()
    def _disconnected(self):self.exploring=False;self._stop_repeat();self._refresh()
    def _toggle_exploring(self):self.exploring=not self.exploring;self._refresh()

    def _refresh(self,*_):
        connected=self.service.is_connected
        recording=getattr(self,'recording',None);playback=recording and recording.playback_busy
        repeating=self._repeat.isActive();locked=bool(playback or repeating)
        option=self._selected();simulated=option.backend=='virtual'
        for w in (self.demo,self.peer):w.setEnabled(not connected and simulated)
        for w in (self.adapter,self.scan):w.setEnabled(not connected)
        self.promiscuous.setEnabled(not connected and option.backend=='pcap')
        self.note.setText(('SIMULATION: in-process link; demo traffic and peer replies stay inside Signal Explorer.' if simulated else
                           'HARDWARE: Send, repeated send and playback transmit through the selected port. '+option.detail))
        self.editor.setTitle('Transmit simulated Ethernet frame' if simulated else 'Transmit Ethernet frame on selected hardware')
        self.connect_button.setText('Disconnect Ethernet' if connected else 'Connect Ethernet')
        self.explore.setEnabled(connected);self.explore.setText('Pause Exploring' if self.exploring else 'Start Exploring')
        self.inject.setEnabled(connected and simulated and not playback)
        self.send.setText('Stop' if repeating else 'Send');self.send.setEnabled(connected and not playback)
        for w in (self.source,self.destination,self.ethertype,self.tagged,self.payload,self.interval,self.load_test,self.test):w.setEnabled(not locked)
        self.vlan.setEnabled(not locked and self.tagged.isChecked());self.pcp.setEnabled(not locked and self.tagged.isChecked())
        diagnostics=self.service.diagnostics
        driver_drops=diagnostics['driver_dropped'] if diagnostics['driver_dropped'] is not None else 'n/a'
        losses=(f" | Driver drops {driver_drops} | Unsupported/error frames {diagnostics['rejected']} | Display drops {diagnostics['ui_dropped']}" if connected and not simulated else '')
        self.status.setText(f"Ethernet {'simulation connected' if simulated else 'capture session open'} | RX {self.rx} | TX {self.tx} | {self.proxy.rowCount()} visible rows | {'Exploring' if self.exploring else 'Monitoring stopped'}{losses}" if connected else 'Ethernet disconnected')

    def _load_test(self):
        frame=preset(self.test.currentText())
        self.source.setText(self._selected().config.get('mac',frame.source));self.destination.setText(frame.destination);self.ethertype.setText(f'{frame.ethertype:04X}')
        self.tagged.setChecked(frame.tagged);self.vlan.setValue(frame.vlan or 0);self.pcp.setValue(frame.pcp or 0)
        self.payload.setPlainText(frame.payload.hex(' ').upper())

    def _send(self):
        if self.recording.playback_busy:return
        if self._repeat.isActive():self._stop_repeat();return
        def send():
            text=self.interval.text().strip();interval=None
            if text:
                if not text.isascii() or not text.isdecimal() or not 1<=int(text)<=2147483647:
                    raise ValueError('Interval must be a positive whole number of milliseconds (1-2147483647), or blank.')
                interval=int(text)
            frame=make_frame(self.source.text(),self.destination.text(),int(self.ethertype.text().strip(),16),
                             bytes.fromhex(self.payload.toPlainText()),self.vlan.value() if self.tagged.isChecked() else None,self.pcp.value())
            if self.service.send_message(frame) and interval is not None:
                self._repeat_frame=frame;self._repeat.start(interval);self._refresh()
        self._try(send)

    def _send_repeat(self):
        if self._repeat_frame is None or not self.service.send_message(self._repeat_frame):self._stop_repeat()

    def _stop_repeat(self):
        self._repeat.stop();self._repeat_frame=None
        if hasattr(self,'status'):self._refresh()

    def _playback_changed(self,running):
        if running:self._stop_repeat()
        self._refresh()

    @Slot(object)
    def _message(self,frame):
        if not self.exploring:return
        self.model.append(frame)
        if frame.direction=='rx':self.rx+=1
        else:self.tx+=1
        if self.auto_scroll.isChecked():self.table.scrollToBottom()
        self._refresh()

    def _selection_changed(self,*_):
        rows=self.table.selectionModel().selectedRows()
        if not rows:return
        frame=self.proxy.data(rows[0],Qt.ItemDataRole.UserRole)
        protocol,summary=decode(frame)
        self.details.setPlainText(f'{frame.direction.upper()} {frame.source} -> {frame.destination} | {protocol} | {summary}\n'
                                  f'{len(frame.raw)} bytes without FCS; payload includes padding\n{frame.raw.hex(" ").upper()}')

    def _apply_filter(self,*_):
        self._try(lambda:self.proxy.configure(self.search.text(),self.type_filter.text(),self.direction.currentData()))
        if hasattr(self,'status'):self._refresh()

    def _clear_filter(self):
        self.search.clear();self.type_filter.clear();self.direction.setCurrentIndex(0);self._apply_filter()

    def _clear(self):self.model.clear();self.details.clear();self.rx=self.tx=0;self._refresh()

    def request_close(self):
        self._stop_repeat();return self.recording.request_close()
