"""Independent CAN transmit list and DBC signal panel."""
from dataclasses import replace
from pathlib import Path
import os
import tempfile

import cantools
from signal_explorer.paths import save_path
from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QTableWidget,
    QTableWidgetItem, QHeaderView, QPushButton, QComboBox, QLineEdit, QLabel, QSplitter,
    QInputDialog, QMessageBox, QFileDialog, QDialog, QFormLayout, QDoubleSpinBox,
    QSpinBox, QDialogButtonBox, QAbstractItemView)

from signal_explorer.bus.transmit import (TxEntry, Waveform, TransmitEngine, dbc_entry,
                                         encode_entries, decode_entries, default_signal_values, encode_signals)


class WaveformDialog(QDialog):
    def __init__(self, wave, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Signal waveform')
        form = QFormLayout(self)
        self.kind = QComboBox(); self.kind.addItems(['Off','Ramp','Pulse','Sine']); self.kind.setCurrentText(wave.kind)
        form.addRow('Generation', self.kind)
        self.minimum, self.maximum, self.step = [QDoubleSpinBox() for _ in range(3)]
        for label, control, value in [('Minimum',self.minimum,wave.minimum),('Maximum',self.maximum,wave.maximum),('Step per TX (ramp)',self.step,wave.step)]:
            control.setDecimals(6); control.setRange(-1e15,1e15); control.setValue(value); form.addRow(label,control)
        self.period = QSpinBox(); self.period.setRange(1,86400000); self.period.setValue(wave.period_ms)
        form.addRow('Period (ms, pulse/sine)', self.period)
        form.addRow(QLabel('Values are physical units. Cyclic sending advances the waveform.'))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept); buttons.rejected.connect(self.reject); form.addRow(buttons)

    def wave(self):
        return Waveform(self.kind.currentText(),self.minimum.value(),self.maximum.value(),self.step.value(),self.period.value())

    def accept(self):
        try: self.wave().value(0,0)
        except ValueError as exc:
            QMessageBox.warning(self,'Waveform',str(exc)); return
        super().accept()


class TransmitWindow(QMainWindow):
    def __init__(self, service, database_provider=lambda: None, playback_busy=lambda: False, parent=None):
        super().__init__(parent, Qt.WindowType.Window)
        self.service, self.database_provider, self.playback_busy = service, database_provider, playback_busy
        self.engine = TransmitEngine(service, playback_busy, self)
        self._dirty, self._syncing, self._path = False, False, None
        self._uids, self._rows, self._steps = [], {}, {}
        self._preview_was_busy = False
        self._payload_dirty = False
        self.resize(1500,820)
        self._build()
        self.engine.changed.connect(self._changed)
        self.engine.progress.connect(self._refresh_status)
        self.engine.failed.connect(self.status.setText)
        service.connected.connect(self._connection_changed)
        service.disconnected.connect(self._connection_changed)
        self._reload()

    def _build(self):
        central=QWidget(); layout=QVBoxLayout(central); self.setCentralWidget(central)
        menu=self.menuBar().addMenu('File')
        for label,callback,shortcut in [('Open transmit list...',self.load,QKeySequence.StandardKey.Open),
                                        ('Save transmit list...',self.save,QKeySequence.StandardKey.Save)]:
            action=QAction(label,self);action.triggered.connect(callback);action.setShortcut(shortcut);menu.addAction(action)
        self.table=QTableWidget(0,21)
        self.table.setHorizontalHeaderLabels(['Use','Message Name','ID (hex)','Channel','Ext','FD','BRS','DLC','Send','Cyclic','Cycle (ms)',
                                              *[str(i) for i in range(8)],'Sent','Status'])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setAlternatingRowColors(True)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        for column,width in enumerate([36,170,85,105,38,38,38,48,70,48,90,*([37]*8),65,200]):
            self.table.setColumnWidth(column,width)
        self.table.itemChanged.connect(self._edit_cell)
        self.table.currentCellChanged.connect(lambda *_:self._show_signals())
        splitter=QSplitter(Qt.Orientation.Vertical);splitter.addWidget(self.table);layout.addWidget(splitter,1)
        lower=QWidget(); lower_layout=QVBoxLayout(lower)
        self.message_label=QLabel('Select a message to edit its full payload or DBC signals.')
        self.message_label.setTextFormat(Qt.TextFormat.PlainText)
        lower_layout.addWidget(self.message_label)
        payload_bar=QHBoxLayout();payload_bar.addWidget(QLabel('Complete payload (hex, including CAN FD bytes 8-63)'))
        self.payload=QLineEdit();self.payload.editingFinished.connect(self._edit_payload)
        self.payload.textEdited.connect(lambda _:setattr(self,'_payload_dirty',True));payload_bar.addWidget(self.payload,1)
        lower_layout.addLayout(payload_bar)
        self.signals=QTableWidget(0,10)
        self.signals.setHorizontalHeaderLabels(['Signal Name','Start Bit','Bits','Raw Value','Physical Value','Unit','Step','Dec','Inc','Waveform'])
        self.signals.horizontalHeader().setStretchLastSection(True)
        self.signals.setColumnWidth(0,180);self.signals.setColumnWidth(4,130)
        self.signals.itemChanged.connect(self._edit_signal)
        lower_layout.addWidget(self.signals)
        self.signal_status=QLabel('DBC signal editing is available for messages added from a loaded DBC.')
        self.signal_status.setTextFormat(Qt.TextFormat.PlainText)
        self.signal_status.setWordWrap(True);lower_layout.addWidget(self.signal_status)
        splitter.addWidget(lower);splitter.setSizes([430,240])
        buttons=QHBoxLayout()
        definitions=[('New Raw',self.add_raw),('New from DBC',self.add_dbc),('Clone',self.clone),('Delete',self.delete),
                     ('Open TX List',self.load),('Save TX List',self.save),('Send All Once',self.send_all),
                     ('Start Cyclic',self.start_cyclic),('Stop All',self.engine.stop_all)]
        definitions += [('Help',self.help),('Close',self.close)]
        for label,callback in definitions:
            button=QPushButton(label);button.clicked.connect(callback);buttons.addWidget(button)
        layout.addLayout(buttons)
        self.status=QLabel('Connect CAN in the main window. Configure rows here, then use now/Start or the batch controls.')
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True);layout.addWidget(self.status)
        layout.addWidget(QLabel('Data columns show bytes 0-7. Cyclic schedules use best-effort PC timing. Closing this window stops its transmissions.'))

    def _try(self, callback):
        try: return callback()
        except Exception as exc:
            QMessageBox.warning(self,'Transmit messages',str(exc))
            return None

    def help(self):
        QMessageBox.information(self,'Send Messages',
            'New Raw adds a hexadecimal frame. New from DBC copies a message from the database loaded in the main window.\n\n'
            'Choose a channel, payload and frame format for each row. The now button sends once. Check Cyclic, set Cycle (ms), '
            'and press Start for repeated sending. Use Stop or Stop All to stop. Use excludes unchecked rows from batch sending.\n\n'
            'Select a DBC row to edit numeric raw/physical signal values, increment/decrement them, or define a ramp, pulse or sine wave. '
            'Edits apply to subsequent sends while a row runs. The complete payload field includes CAN FD bytes beyond byte 7.\n\n'
            'Use Open TX List / Save TX List or the File menu. Opening a list never starts sending. Closing this window, disconnecting CAN or starting '
            'logfile playback stops its scheduled transmissions; the main monitor and single sender stay available.')

    def _selected(self):
        return [self._uids[index.row()] for index in self.table.selectionModel().selectedRows()]

    def _current(self):
        row=self.table.currentRow()
        return self._uids[row] if 0 <= row < len(self._uids) else None

    def _title(self):
        self.setWindowTitle('Send Messages - '+(self._path.name if self._path else 'Untitled')+(' *' if self._dirty else ''))

    def _changed(self):
        self._dirty=True;self._reload()

    def _connection_changed(self,*_):
        self._reload()

    def _reload(self):
        current=self._current()
        self._syncing=True
        try:
            # Qt defers destruction of replaced cell widgets; hide old controls
            # immediately so they cannot briefly paint over another column.
            for row in range(self.table.rowCount()):
                for column in (3,8):
                    widget=self.table.cellWidget(row,column)
                    if widget:
                        widget.hide();widget.setParent(None)
                        self.table.removeCellWidget(row,column);widget.deleteLater()
            rows=self.engine.entries();self._uids=[e.uid for e in rows];self._rows={e.uid:e for e in rows}
            self.table.setRowCount(len(rows))
            for row,entry in enumerate(rows):
                for col in range(21):
                    text = (entry.name if col==1 else f'{entry.identifier:X}' if col==2 else str(len(entry.data)) if col==7
                            else str(entry.period_ms) if col==10 else f'{entry.data[col-11]:02X}' if 11<=col<19 and col-11<len(entry.data) else '')
                    item=QTableWidgetItem(text)
                    if col in (0,4,5,6,9):
                        item.setFlags(Qt.ItemFlag.ItemIsEnabled|Qt.ItemFlag.ItemIsSelectable|Qt.ItemFlag.ItemIsUserCheckable)
                        item.setCheckState(Qt.CheckState.Checked if {0:entry.enabled,4:entry.extended,5:entry.fd,6:entry.brs,9:entry.cyclic}[col] else Qt.CheckState.Unchecked)
                    elif col in (3,8,19,20) or (11<=col<19 and col-11>=len(entry.data)):
                        item.setFlags(Qt.ItemFlag.ItemIsEnabled|Qt.ItemFlag.ItemIsSelectable)
                    self.table.setItem(row,col,item)
                combo=QComboBox();combo.addItem('Default TX',None)
                for channel in self.service.channels: combo.addItem(channel['label'],channel['native_channel'])
                selected=next((i for i in range(combo.count()) if str(combo.itemData(i))==str(entry.channel)), -1)
                if selected<0:
                    combo.addItem(str(entry.channel),entry.channel);selected=combo.count()-1
                combo.setCurrentIndex(selected)
                combo.currentIndexChanged.connect(lambda _,uid=entry.uid,c=combo:self._try(lambda:self.engine.update(replace(self.engine.get(uid),channel=c.currentData()))))
                self.table.setCellWidget(row,3,combo)
                send=QPushButton();send.clicked.connect(lambda _,uid=entry.uid:self._send_row(uid));self.table.setCellWidget(row,8,send)
            index=self._uids.index(current) if current in self._uids else 0
            if rows:self.table.setCurrentCell(index,1)
        finally:self._syncing=False
        self._title();self._refresh_status();self._show_signals()

    def _refresh_status(self):
        locked=self.playback_busy() or not self.service.is_connected
        self._syncing=True
        try:
            for row,uid in enumerate(self._uids):
                count,running,pending,error=self.engine.state(uid);entry=self._rows[uid]
                display=self.engine.last_data(uid) if running or pending else entry.data
                for column in range(11,19):
                    text=f'{display[column-11]:02X}' if column-11<len(display) else ''
                    if self._editing(self.table,row,column):continue
                    if self.table.item(row,column).text()!=text:self.table.item(row,column).setText(text)
                self.table.item(row,19).setText(str(count))
                self.table.item(row,20).setText(error or ('Cyclic' if running else 'Sending / stopping' if pending else 'Ready' if not locked else 'Disconnected / replay active'))
                self.table.item(row,20).setToolTip(error)
                button=self.table.cellWidget(row,8)
                button.setText('Stop' if running else 'Start' if entry.cyclic else 'now')
                button.setEnabled(running or (not locked and not pending and entry.enabled))
                self.table.cellWidget(row,3).setEnabled(True)
            current=self._current()
            busy=current is not None and any(self.engine.state(current)[1:3])
            self.payload.setReadOnly(False)
            if busy:
                data=self.engine.last_data(current)
                if not self.payload.hasFocus():self.payload.setText(data.hex(' ').upper())
                definition=self._rows[current].definition
                if definition:
                    try:
                        raw=definition.decode(data,decode_choices=False,scaling=False)
                        physical=definition.decode(data,decode_choices=False)
                        for row in range(self.signals.rowCount()):
                            if any(self._editing(self.signals,row,column) for column in (3,4,6)):continue
                            name=self.signals.item(row,0).text()
                            if name in raw:
                                self.signals.item(row,3).setText(str(raw[name]));self.signals.item(row,4).setText(str(physical[name]))
                    except Exception as exc:self.signal_status.setText(f'Decode failed: {exc}')
        finally:self._syncing=False
        if self._preview_was_busy and not busy and not self.payload.hasFocus() and self.signals.state()!=QAbstractItemView.State.EditingState:self._show_signals()
        self._preview_was_busy=busy

    @staticmethod
    def _editing(table,row,column):
        return (table.state()==QAbstractItemView.State.EditingState and
                table.currentRow()==row and table.currentColumn()==column)

    def _edit_cell(self,item):
        if self._syncing:return
        uid=self._uids[item.row()];entry=self.engine.get(uid);col=item.column()
        try:
            changes={}
            if col in (0,4,5,6,9):
                changes[{0:'enabled',4:'extended',5:'fd',6:'brs',9:'cyclic'}[col]]=item.checkState()==Qt.CheckState.Checked
                if col==5 and not changes['fd']:changes['brs']=False
            elif col==1:changes['name']=item.text()
            elif col==2:changes['identifier']=int(item.text(),16)
            elif col==7:
                length=int(item.text())
                if not 0<=length<=64:raise ValueError('Payload length must be 0-64 bytes.')
                changes['data']=self._edit_data(uid,entry)[:length].ljust(length,b'\x00')
            elif col==10:changes['period_ms']=int(item.text())
            elif 11<=col<19:
                data=bytearray(self._edit_data(uid,entry));value=int(item.text(),16)
                if not 0<=value<=255:raise ValueError('A data byte must be 00-FF.')
                data[col-11]=value;changes.update(data=bytes(data),waves={})
            else:return
            if col in (2,4,5,7) and any(getattr(entry,key)!=value for key,value in changes.items()):
                changes.update(definition=None,waves={})
            self.engine.update(replace(entry,**changes))
        except Exception as exc:
            QMessageBox.warning(self,'Invalid message',str(exc));self._reload()

    def add_raw(self):
        self._try(lambda:self.engine.add(TxEntry(name=f'Message {len(self._uids)+1}')))

    def add_dbc(self):
        database=self.database_provider()
        if database is None:
            QMessageBox.information(self,'DBC messages','Load a DBC in the main CAN window first.');return
        definitions=sorted(database.messages,key=lambda m:(m.name,m.frame_id))
        choices=[f'{m.name} | {m.frame_id:X} | {"FD" if m.is_fd else "CAN"}' for m in definitions]
        if not choices:return
        name,ok=QInputDialog.getItem(self,'New DBC message','Message',choices,0,False)
        if ok:self._try(lambda:self.engine.add(dbc_entry(definitions[choices.index(name)])))

    def clone(self):
        for uid in self._selected():
            entry=self.engine.get(uid)
            self._try(lambda e=entry:self.engine.add(replace(e,uid=TxEntry().uid,name=e.name+' copy')))

    def delete(self):self.engine.remove(self._selected())

    def _send_row(self,uid):
        if self.engine.state(uid)[1]:self.engine.stop([uid])
        else:self._try(lambda:self.engine.start([uid],self.engine.get(uid).cyclic))

    def send_all(self):self._try(lambda:self.engine.start([e.uid for e in self.engine.entries()]))
    def start_cyclic(self):self._try(lambda:self.engine.start([e.uid for e in self.engine.entries() if e.cyclic],True))

    def _show_signals(self):
        if self._syncing:return
        uid=self._current();self._syncing=True
        try:
            for row in range(self.signals.rowCount()):
                for column in (7,8,9):
                    widget=self.signals.cellWidget(row,column)
                    if widget:
                        widget.hide();widget.setParent(None)
                        self.signals.removeCellWidget(row,column);widget.deleteLater()
            self.signals.setRowCount(0);self.payload.clear()
            self._payload_dirty=False
            if uid is None:
                self.message_label.setText('Select a message to edit its full payload or DBC signals.')
                self.signal_status.setText('DBC signal editing is available for messages added from a loaded DBC.');return
            entry=self.engine.get(uid);self.payload.setText(entry.data.hex(' ').upper())
            self.message_label.setText(f'{entry.name} | ID {entry.identifier:X} | {len(entry.data)} bytes | '+('CAN FD' if entry.fd else 'Classic CAN'))
            if entry.definition is None:
                self.signal_status.setText('Raw message. Add a message from DBC to edit signals.');return
            physical=entry.definition.decode(entry.data,decode_choices=False)
            raw=entry.definition.decode(entry.data,decode_choices=False,scaling=False)
            definitions=[s for s in entry.definition.signals if s.name in physical]
            self.signals.setRowCount(len(definitions))
            for row,signal in enumerate(definitions):
                for col,value in enumerate((signal.name,signal.start,signal.length,raw[signal.name],physical[signal.name],signal.unit or '',self._steps.get((uid,signal.name),1))):
                    item=QTableWidgetItem(str(value))
                    if col not in (3,4,6):item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                    item.setToolTip(str(signal.choices or ''))
                    self.signals.setItem(row,col,item)
                for col,label,step in ((7,'-',-1),(8,'+',1)):
                    button=QPushButton(label);button.clicked.connect(lambda _,n=signal.name,d=step:self._step_signal(n,d));self.signals.setCellWidget(row,col,button)
                wave=QPushButton(entry.waves.get(signal.name,Waveform()).kind)
                wave.clicked.connect(lambda _,n=signal.name:self._wave(n));self.signals.setCellWidget(row,9,wave)
            self.signal_status.setText('Raw/physical values are editable while running. Updates apply to subsequent sends. DBC definitions are copied when added.')
        except Exception as exc:self.signal_status.setText(f'Decode failed: {exc}')
        finally:
            self._syncing=False
            current=self._current();busy=current is not None and any(self.engine.state(current)[1:3])
            self.payload.setReadOnly(False)

    def _edit_payload(self):
        uid=self._current()
        if uid is None or self._syncing or not self._payload_dirty:return
        self._payload_dirty=False
        entry=self.engine.get(uid)
        def update():
            payload=bytes.fromhex(self.payload.text())
            if len(payload)!=len(entry.data):raise ValueError(f'Enter exactly {len(entry.data)} bytes; change DLC to resize a raw message.')
            if payload!=entry.data or entry.waves:self.engine.update(replace(entry,data=payload,waves={}))
        if self._try(update) is None:self._show_signals()

    def _set_signal(self,name,value):
        uid=self._current();entry=self.engine.get(uid)
        base=self._edit_data(uid,entry)
        values=default_signal_values(entry.definition)
        values.update(entry.definition.decode(base,decode_choices=False));values[name]=value
        waves=dict(entry.waves);waves.pop(name,None)
        self.engine.update(replace(entry,data=encode_signals(entry.definition,base,values),waves=waves))

    def _edit_data(self,uid,entry):
        data=self.engine.last_data(uid) if any(self.engine.state(uid)[1:3]) else entry.data
        return data if len(data)==len(entry.data) else entry.data

    def _edit_signal(self,item):
        if self._syncing:return
        uid=self._current();name=self.signals.item(item.row(),0).text()
        def update():
            if item.column()==6:
                step=float(item.text())
                if not 0<step<1e15:raise ValueError('Signal step must be finite and positive.')
                self._steps[(uid,name)]=step;return
            entry=self.engine.get(uid);signal=entry.definition.get_signal_by_name(name)
            if item.column()==3:
                value=float(item.text()) if signal.is_float else int(item.text(),0) if 'x' in item.text().lower() else int(item.text())
                value=signal.conversion.raw_to_scaled(value,decode_choices=False)
            elif item.column()==4:value=float(item.text())
            else:return
            self._set_signal(name,value)
        self._try(update);self._show_signals()

    def _step_signal(self,name,direction):
        def update():
            uid=self._current();entry=self.engine.get(uid)
            base=self.engine.last_data(uid) if name in entry.waves and any(self.engine.state(uid)[1:3]) else entry.data
            value=entry.definition.decode(base,decode_choices=False)[name]
            self._set_signal(name,value+direction*self._steps.get((uid,name),1))
        self._try(update)

    def _wave(self,name):
        uid=self._current();entry=self.engine.get(uid)
        dialog=WaveformDialog(entry.waves.get(name,Waveform()),self)
        if dialog.exec():
            waves=dict(entry.waves)
            if dialog.wave().kind=='Off':waves.pop(name,None)
            else:waves[name]=dialog.wave()
            self._try(lambda:self.engine.update(replace(entry,waves=waves)))

    def save(self):
        path,_=QFileDialog.getSaveFileName(self,'Save CAN transmit list',save_path('CAN-messages.json', self._path),'Transmit list (*.json)')
        if not path:return False
        def write():
            target=Path(path);temporary=None
            try:
                with tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',dir=target.parent,delete=False) as file:
                    temporary=Path(file.name);file.write(encode_entries(self.engine.entries()))
                os.replace(temporary,target)
            finally:
                if temporary:temporary.unlink(missing_ok=True)
            self._path=target;self._dirty=False;self._title();return True
        return bool(self._try(write))

    def _confirm_replace(self):
        if not self._dirty:return True
        answer=QMessageBox.question(self,'Save transmit list?','Save changes to the CAN transmit list?',
                                   QMessageBox.StandardButton.Save|QMessageBox.StandardButton.Discard|QMessageBox.StandardButton.Cancel)
        if answer==QMessageBox.StandardButton.Cancel:return False
        return self.save() if answer==QMessageBox.StandardButton.Save else True

    def load(self):
        self.engine.stop_all()
        if not self._confirm_replace():return
        path,_=QFileDialog.getOpenFileName(self,'Open CAN transmit list','','Transmit list (*.json)')
        if not path:return
        def read():
            if Path(path).stat().st_size>16*1024*1024:raise ValueError('Transmit list is too large (maximum 16 MiB).')
            entries=decode_entries(Path(path).read_text(encoding='utf-8'))
            self.engine.replace_entries(entries)
            self._path=Path(path);self._dirty=False;self._title()
        self._try(read)

    def playback_changed(self,running):
        if running:self.engine.stop_all()
        self._refresh_status()

    def request_close_for_main(self):
        self.engine.stop_all()
        return self._confirm_replace()

    def shutdown(self):self.engine.shutdown()

    def closeEvent(self,event):
        self.engine.stop_all()
        super().closeEvent(event)
