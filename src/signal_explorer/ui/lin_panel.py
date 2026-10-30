from pathlib import Path
from PySide6.QtCore import Qt, Slot
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QComboBox, QLabel,
    QPushButton, QCheckBox, QLineEdit, QSpinBox, QTableView, QTableWidget,
    QTableWidgetItem, QMessageBox, QFileDialog, QSplitter, QInputDialog)
from signal_explorer.lin.service import LinService
from signal_explorer.lin.database import LinDatabase
from signal_explorer.lin.model import Response, ScheduleSlot, lin_checksum, checksum_model
from signal_explorer.lin.codec import LINCodec
from signal_explorer.ui.lin_trace_model import LinTraceModel
from signal_explorer.ui.trace_model import IdFilter
from signal_explorer.ui.recording_panel import RecordingPanel

class LinPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.service = LinService(self)
        self.database = None
        self.exploring = False
        self.rx = self.tx = 0
        self._build()
        self.service.connected.connect(self._connected)
        self.service.disconnected.connect(self._disconnected)
        self.service.message.connect(self._message)
        self.service.error.connect(lambda text: QMessageBox.warning(self, 'LIN', text))
        self.service.schedule_changed.connect(lambda running: self._refresh())
        self._load_database(str(Path(__file__).parents[1] / 'lin' / 'demo.ldf'))
        self._refresh()

    def _build(self):
        self.adapter_widget = QWidget()
        bar = QHBoxLayout(self.adapter_widget); bar.setContentsMargins(0,0,0,0)
        self.adapter = QComboBox(); self.adapter.addItem('Virtual LIN (simulated)', 'virtual')
        self.adapter.addItem('Intrepid ValueCAN4-2EL', 'neovi')
        self.adapter.addItem('Vector VN16xx / XL LIN', 'vector')
        self.adapter.addItem('PEAK PLIN / PCAN-USB Pro FD', 'peak')
        self.channel = QComboBox();self.channel.setMinimumWidth(200)
        self.refresh_devices = QPushButton('Refresh');self.refresh_devices.clicked.connect(self._scan_devices)
        self.role = QComboBox()
        for label,key in [('Monitor','monitor'),('Master','master'),('Slave','slave')]: self.role.addItem(label,key)
        self.role.currentIndexChanged.connect(self._role_changed)
        self.baudrate = QComboBox()
        for rate in (2400,4800,9600,10400,19200,20000): self.baudrate.addItem(str(rate),rate)
        self.baudrate.setCurrentIndex(4)
        self.demo_peer = QCheckBox('Simulated peer'); self.demo_peer.setChecked(True)
        self.connect_button = QPushButton('Connect LIN'); self.connect_button.clicked.connect(self._toggle_connection)
        for widget in (QLabel('LIN adapter'),self.adapter,self.channel,self.refresh_devices,QLabel('Role'),self.role,QLabel('Baud rate'),self.baudrate,self.demo_peer,self.connect_button):bar.addWidget(widget)
        bar.addStretch()
        root = QVBoxLayout(self)
        self.note=QLabel('Logical simulation: no electrical bus or hardware-driver traffic.')
        self.note.setWordWrap(True);root.addWidget(self.note)
        tools=QHBoxLayout()
        self.explore=QPushButton('Start Exploring');self.explore.clicked.connect(self._toggle_exploring)
        clear=QPushButton('Clear');clear.clicked.connect(self._clear)
        self.auto_scroll=QCheckBox('Auto-scroll (all events)');self.auto_scroll.setChecked(True)
        self.auto_scroll.toggled.connect(self._view_changed)
        self.filter=QLineEdit();self.filter.setPlaceholderText('Hex IDs, e.g. 10, 20-2F')
        apply=QPushButton('Apply filter');apply.clicked.connect(self._filter_changed);self.filter.returnPressed.connect(self._filter_changed)
        reset=QPushButton('Clear filter');reset.clicked.connect(self._clear_filter)
        self.load_ldf=QPushButton('Load LDF');self.load_ldf.clicked.connect(self._choose_ldf)
        self.ldf_label=QLabel('No LDF')
        for w in (self.explore,clear,self.auto_scroll,self.filter,apply,reset,self.load_ldf,self.ldf_label):tools.addWidget(w)
        root.addLayout(tools)
        self.model=LinTraceModel();self.proxy=IdFilter(self,max_id=63);self.proxy.setSourceModel(self.model)
        self.table=QTableView();self.table.setModel(self.proxy);self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().hide();self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setColumnWidth(0,155);self.table.setColumnWidth(8,190)
        root.addWidget(self.table,1)
        control=QHBoxLayout()
        self.header_id=QLineEdit('20');self.header_id.setMaximumWidth(65)
        self.header=QPushButton('Send Header');self.header.clicked.connect(lambda:self._try(lambda:self.service.send_header(int(self.header_id.text(),16))))
        self.schedule_button=QPushButton('Start Schedule');self.schedule_button.clicked.connect(self._toggle_schedule)
        self.sleep=QPushButton('Sleep');self.sleep.clicked.connect(self._sleep)
        self.wake=QPushButton('Wake');self.wake.clicked.connect(lambda:self._try(lambda:self.service.endpoint.wake()))
        self.fault=QComboBox()
        for label,key in [('Checksum error','checksum'),('No response','no_response'),('PID parity error','parity'),('Response collision','collision')]:self.fault.addItem(label,key)
        self.inject=QPushButton('Inject next fault');self.inject.clicked.connect(self._inject)
        for w in (QLabel('Frame ID (hex)'),self.header_id,self.header,self.schedule_button,self.sleep,self.wake,self.fault,self.inject):control.addWidget(w)
        root.addLayout(control)
        edit=QSplitter(Qt.Orientation.Horizontal)
        response_box=QWidget();rl=QVBoxLayout(response_box);rl.setContentsMargins(0,0,0,0)
        rl.addWidget(QLabel('Responses: select locally published IDs; Slave responses wait for a master header.'))
        self.responses=QTableWidget(0,6);self.responses.setHorizontalHeaderLabels(['ID (hex)','Length','Publisher','Publish locally','Data (hex)','Checksum'])
        self.responses.horizontalHeader().setStretchLastSection(True)
        self.responses.setMaximumHeight(150);rl.addWidget(self.responses)
        row=QHBoxLayout();self.apply_responses=QPushButton('Apply Responses');self.apply_responses.clicked.connect(lambda:self._try(self._apply_responses))
        self.add_frame=QPushButton('Add Frame');self.add_frame.clicked.connect(self._add_frame)
        row.addWidget(self.apply_responses);row.addWidget(self.add_frame);rl.addLayout(row);edit.addWidget(response_box)
        schedule_box=QWidget();sl=QVBoxLayout(schedule_box);sl.setContentsMargins(0,0,0,0)
        top=QHBoxLayout();top.addWidget(QLabel('Master schedule'))
        self.schedule_names=QComboBox();top.addWidget(self.schedule_names)
        self.use_schedule=QPushButton('Use LDF Schedule');self.use_schedule.clicked.connect(lambda:self._try(self._use_ldf_schedule));top.addWidget(self.use_schedule);sl.addLayout(top)
        self.schedule=QTableWidget(0,2);self.schedule.setHorizontalHeaderLabels(['Frame ID (hex)','Slot delay (ms)'])
        self.schedule.setMaximumHeight(150);sl.addWidget(self.schedule)
        bottom=QHBoxLayout();self.add_slot=QPushButton('Add Slot');self.add_slot.clicked.connect(lambda:self._add_slot(0x20,100))
        self.remove_slot=QPushButton('Remove Slot');self.remove_slot.clicked.connect(lambda:self.schedule.removeRow(self.schedule.currentRow()))
        bottom.addWidget(self.add_slot);bottom.addWidget(self.remove_slot);sl.addLayout(bottom);edit.addWidget(schedule_box)
        root.addWidget(edit)
        self.recording = RecordingPanel(self.service,self,codec=LINCodec(),
            playback_codec_factory=lambda:LINCodec(self.service.role,self.service.configured_responses),
            tx_options=lambda:dict(lengths=self.service.lengths, models=self.service.models, operation_label='headers' if self.service.role=='master' else 'buffer updates'),
            replay_label=lambda: 'Playback Headers' if self.role.currentData()=='master' else 'Replay Response Buffers' if self.role.currentData()=='slave' else 'Playback (Master/Slave only)')
        self.recording.playback_changed.connect(self._playback_changed)
        self.recording.replay.setToolTip('Master: replay headers using current response configuration. Slave: timed updates to selected local response buffers; incoming headers trigger actual TX.')
        root.addWidget(self.recording)
        self.status=QLabel('LIN disconnected');self.status.setWordWrap(True);root.addWidget(self.status)
        self.adapter.currentIndexChanged.connect(self._scan_devices)
        self._scan_devices()

    def _scan_devices(self,*_):
        self.channel.clear()
        physical=self.adapter.currentData()!='virtual'
        self.channel.setVisible(physical);self.refresh_devices.setVisible(physical)
        self.demo_peer.setVisible(not physical)
        if physical:
            if self.adapter.currentData()=='neovi':
                from signal_explorer.lin.intrepid import discover_intrepid as discover
            elif self.adapter.currentData()=='vector':
                from signal_explorer.lin.vector import discover_vector as discover
            else:
                from signal_explorer.lin.peak import discover_peak as discover
            options,status=discover()
            for option in options:self.channel.addItem(option.label,option.config)
            self.note.setText(status+' | Native LIN backend: bench validation pending. Use external LIN power, common ground and the vendor pinout. One master per bus.')
        else:
            self.note.setText('Logical simulation: no electrical bus or hardware-driver traffic.')
        if hasattr(self,'status'):self._refresh()

    def _try(self, callback):
        try:return callback()
        except Exception as exc:QMessageBox.warning(self,'LIN',str(exc))

    def _load_database(self,path):
        database=LinDatabase(path)
        self.service.set_database(database);self.database=database;self.model.set_database(database)
        self.recording.database_path=path
        index=self.baudrate.findData(database.baudrate)
        if index<0:raise ValueError('This LIN baud rate is not available.')
        self.baudrate.setCurrentIndex(index)
        self.ldf_label.setText(Path(path).name)
        self.responses.setRowCount(0)
        for identifier,frame in database.frames.items():
            self._response_row(identifier,frame.length,frame.publisher.name,database.initial_data(identifier),database.model)
        self.schedule_names.clear();self.schedule_names.addItems(database.schedules())
        if self.schedule_names.count():self._use_ldf_schedule()
        self._role_changed()

    def _choose_ldf(self):
        path,_=QFileDialog.getOpenFileName(self,'Load LIN Description File','','LIN descriptions (*.ldf)')
        if path:self._try(lambda:self._load_database(path))

    def _response_row(self,identifier,length,publisher,data,model):
        row=self.responses.rowCount();self.responses.insertRow(row)
        for col,text in enumerate((f'{identifier:02X}',str(length),publisher)):
            item=QTableWidgetItem(text);item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable);self.responses.setItem(row,col,item)
        check=QCheckBox();self.responses.setCellWidget(row,3,check)
        self.responses.setItem(row,4,QTableWidgetItem(data.hex(' ').upper()))
        combo=QComboBox();combo.addItems(['enhanced','classic']);combo.setCurrentText(checksum_model(identifier,model));self.responses.setCellWidget(row,5,combo)

    def _role_changed(self,*_):
        if self.service.is_connected:return
        role=self.role.currentData()
        for row in range(self.responses.rowCount()):
            publisher=self.responses.item(row,2).text()
            is_master=self.database and publisher==self.database.master
            self.responses.cellWidget(row,3).setChecked((role=='master' and is_master) or (role=='slave' and not is_master))
        self._refresh()

    def _configured(self):
        responses={};lengths={};models={}
        for row in range(self.responses.rowCount()):
            identifier=int(self.responses.item(row,0).text(),16);length=int(self.responses.item(row,1).text())
            data=bytes.fromhex(self.responses.item(row,4).text());model=self.responses.cellWidget(row,5).currentText()
            if len(data)!=length:raise ValueError(f'ID {identifier:02X} requires {length} bytes.')
            lin_checksum(identifier,data,model)
            lengths[identifier]=length;models[identifier]=checksum_model(identifier,model)
            if self.responses.cellWidget(row,3).isChecked():responses[identifier]=Response(data,models[identifier])
        return responses,lengths,models

    def _toggle_connection(self):
        if self.service.is_connected:self.service.disconnect_bus();return
        def connect():
            responses,lengths,models=self._configured()
            self.service.lengths=lengths;self.service.models=models
            if self.adapter.currentData()=='virtual':
                self.service.connect_simulated(self.role.currentData(),self.baudrate.currentData(),self.demo_peer.isChecked(),responses)
            else:
                if self.channel.currentData() is None:raise ValueError('No LIN channel selected. Install the LIN driver/API, connect the adapter, then Refresh.')
                self.service.connect_hardware(self.adapter.currentData(),self.channel.currentData(),self.role.currentData(),self.baudrate.currentData(),responses)
        self._try(connect)

    def _connected(self,*_):
        self.exploring=True;self._refresh()

    def _disconnected(self):
        self.exploring=False;self._refresh()

    def _refresh(self,*_):
        connected=self.service.is_connected;master=connected and self.service.role=='master'
        playback=getattr(self,'recording',None) and self.recording.playback_busy
        for w in (self.role,self.baudrate,self.demo_peer,self.adapter,self.channel,self.refresh_devices,self.load_ldf,self.add_frame):w.setEnabled(not connected)
        self.connect_button.setText('Disconnect LIN' if connected else 'Connect LIN')
        self.explore.setEnabled(connected);self.explore.setText('Pause Exploring' if self.exploring else 'Start Exploring')
        for w in (self.header,self.header_id,self.sleep):w.setEnabled(master and not playback and not self.service.schedule_running)
        self.schedule_button.setEnabled(master and not playback)
        self.schedule_button.setText('Stop Schedule' if self.service.schedule_running else 'Start Schedule')
        for w in (self.schedule,self.schedule_names,self.use_schedule,self.add_slot,self.remove_slot):w.setEnabled(not self.service.schedule_running and not playback)
        self.apply_responses.setEnabled(connected and self.service.role!='monitor' and not playback)
        self.responses.setEnabled(not playback)
        self.wake.setEnabled(connected and self.service.role!='monitor' and not playback)
        if connected and not getattr(self.service.endpoint,'supports_wake',True):
            self.wake.setEnabled(False)
            self.wake.setToolTip('The legacy Intrepid API wake pulse is not enabled; wake the bus using the external master.')
        else:self.wake.setToolTip('Send a native LIN wake pulse.')
        simulated=connected and self.service.bus is not None
        self.inject.setEnabled(simulated);self.fault.setEnabled(simulated)
        self.recording._refresh()
        self.status.setText(f"LIN {'connected: '+self.service.role if connected else 'disconnected'} | RX {self.rx} | TX {self.tx} | {self.proxy.rowCount()} visible rows | {'Exploring' if self.exploring else 'Monitoring stopped'}")

    def _apply_responses(self):
        responses,lengths,models=self._configured()
        if lengths!=self.service.lengths:raise ValueError('Disconnect before changing frame definitions.')
        if self.service.bus is None and models!=self.service.models:
            raise ValueError('Disconnect physical LIN before changing checksum models.')
        self.service.configured_responses=responses;self.service.models=models;self.service.restore_responses()
        self.service._demo_tick(poll=False)

    def _add_frame(self):
        text,ok=QInputDialog.getText(self,'Add unconditional frame','Frame ID (hex), length (1-8), e.g. 21, 4')
        if not ok:return
        def add():
            identifier,length=(int(v.strip(),16 if i==0 else 10) for i,v in enumerate(text.split(',')))
            if not 0<=identifier<=61 or not 1<=length<=8:raise ValueError('ID must be 00-3D and length 1-8.')
            if any(int(self.responses.item(r,0).text(),16)==identifier for r in range(self.responses.rowCount())):raise ValueError('That ID is already defined.')
            self._response_row(identifier,length,'Manual',bytes(length),'enhanced')
        self._try(add)

    def _add_slot(self,identifier,delay):
        row=self.schedule.rowCount();self.schedule.insertRow(row)
        self.schedule.setItem(row,0,QTableWidgetItem(f'{identifier:02X}'));self.schedule.setItem(row,1,QTableWidgetItem(f'{delay:g}'))

    def _use_ldf_schedule(self):
        if not self.database or not self.schedule_names.currentText():return
        slots=self.database.schedule(self.schedule_names.currentText())
        self.schedule.setRowCount(0)
        for slot in slots:self._add_slot(slot.frame_id,slot.delay_ms)

    def _toggle_schedule(self):
        if self.service.schedule_running:self.service.stop_schedule();return
        def start():
            slots=[ScheduleSlot(int(self.schedule.item(r,0).text(),16),float(self.schedule.item(r,1).text())) for r in range(self.schedule.rowCount())]
            self.service.start_schedule(slots)
        self._try(start)

    def _sleep(self):
        self.service.stop_schedule();self._try(lambda:self.service.endpoint.sleep())

    def _inject(self):
        if self.service.bus:self.service.bus.next_fault=self.fault.currentData()

    def _toggle_exploring(self):
        self.exploring=not self.exploring;self._refresh()

    @Slot(object)
    def _message(self,event):
        if not self.exploring:return
        self.model.append(event)
        if event.direction=='rx':self.rx+=1
        else:self.tx+=1
        if self.auto_scroll.isChecked():self.table.scrollToBottom()
        self._refresh()

    def _clear(self):
        self.model.clear();self.rx=self.tx=0;self._refresh()

    def _view_changed(self,checked):
        self.model.set_history_mode(checked);self._refresh()

    def _filter_changed(self):
        self._try(lambda:self.proxy.set_expression(self.filter.text()));self._refresh()

    def _clear_filter(self):
        self.filter.clear();self._filter_changed()

    def _playback_changed(self,running):
        if running:self.service.stop_schedule()
        else:self.service.restore_responses()
        self._refresh()

    def request_close(self):
        self.service.stop_schedule()
        return self.recording.request_close()
