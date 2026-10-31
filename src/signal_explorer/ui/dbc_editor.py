"""Native, offline DBC editor. Edits use private copies until explicitly saved."""
from copy import deepcopy
from pathlib import Path

import cantools
from cantools.database.can import Message, Signal as CanSignal
from cantools.database.conversion import BaseConversion
from signal_explorer.dbc.document import DbcDocument, identifier, number, signal_bits, validate_database
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QAction, QColor, QKeySequence
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
    QHBoxLayout, QLabel, QLineEdit, QListWidget, QMainWindow, QMessageBox,
    QPlainTextEdit, QPushButton, QSpinBox, QSplitter, QTableWidget, QHeaderView,
    QTableWidgetItem, QTabWidget, QToolBar, QVBoxLayout, QWidget,
)


class PropertiesDialog(QDialog):
    """Form fields leave unsupported metadata intact on a copied object."""
    def __init__(self, item, parent=None):
        super().__init__(parent)
        self.original = item
        self.result_item = None
        self.fields = {}
        self.setWindowTitle('Message properties' if isinstance(item, Message) else 'Signal properties')
        self.resize(520, 600)
        layout = QVBoxLayout(self)
        self.form = QFormLayout()
        layout.addLayout(self.form)
        self.text('name', 'Name', item.name)
        if isinstance(item, Message):
            self.text('frame_id', 'CAN ID (hex)', f'{item.frame_id:X}')
            self.check('is_extended_frame', 'Extended (29-bit)', item.is_extended_frame)
            self.check('is_fd', 'CAN FD', item.is_fd)
            self.spin('length', 'Payload bytes', item.length, 0, 64)
            self.text('cycle_time', 'Cycle time (ms, optional)', item.cycle_time)
            self.text('senders', 'Senders (comma separated)', ', '.join(item.senders))
        else:
            self.spin('start', 'Start bit (DBC numbering)', item.start, 0, 511)
            self.spin('length', 'Bit length', item.length, 1, 512)
            order = QComboBox()
            order.addItem('Intel / little endian', 'little_endian')
            order.addItem('Motorola / big endian', 'big_endian')
            order.setCurrentIndex(order.findData(item.byte_order))
            self.fields['byte_order'] = order
            self.form.addRow('Byte order', order)
            self.check('is_signed', 'Signed', item.is_signed)
            for key, label, value in (('scale', 'Factor', item.scale), ('offset', 'Offset', item.offset),
                                      ('minimum', 'Minimum (optional)', item.minimum),
                                      ('maximum', 'Maximum (optional)', item.maximum), ('unit', 'Unit', item.unit),
                                      ('receivers', 'Receivers (comma separated)', ', '.join(item.receivers))):
                self.text(key, label, value)
            choices = QPlainTextEdit()
            choices.setPlaceholderText('One raw integer=value label per line, e.g. 0=Released')
            choices.setPlainText('\n'.join(f'{k}={v}' for k, v in (item.choices or {}).items()))
            choices.setMaximumHeight(100)
            self.fields['choices'] = choices
            self.form.addRow('Value labels', choices)
            if item.is_float or item.is_multiplexer or item.multiplexer_signal:
                label = QLabel('Existing floating-point / multiplexing metadata is preserved.\nMultiplexing configuration is not edited in this basic form.')
                label.setWordWrap(True)
                layout.addWidget(label)
        self.text('comment', 'Comment', item.comment)
        self.error = QLabel()
        self.error.setWordWrap(True)
        layout.addWidget(self.error)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def text(self, key, label, value):
        field = QLineEdit('' if value is None else str(value))
        self.fields[key] = field
        self.form.addRow(label, field)

    def check(self, key, label, value):
        field = QCheckBox()
        field.setChecked(value)
        self.fields[key] = field
        self.form.addRow(label, field)

    def spin(self, key, label, value, minimum, maximum):
        field = QSpinBox()
        field.setRange(minimum, maximum)
        field.setValue(value)
        self.fields[key] = field
        self.form.addRow(label, field)

    def value(self, key):
        return self.fields[key].text().strip()

    def accept(self):
        try:
            item = deepcopy(self.original)
            item.name = identifier(self.value('name'))
            # Avoid replacing multilingual comments merely by opening the form.
            if self.value('comment') != (self.original.comment or ''):
                item.comment = self.value('comment') or None
            if isinstance(item, Message):
                item.is_extended_frame = self.fields['is_extended_frame'].isChecked()
                item.frame_id = int(self.value('frame_id'), 16)
                item.is_fd = self.fields['is_fd'].isChecked()
                item.length = self.fields['length'].value()
                cycle = self.value('cycle_time')
                item.cycle_time = int(cycle) if cycle else None
                if item.cycle_time is not None and item.cycle_time < 0:
                    raise ValueError('Cycle time must be nonnegative.')
                item.senders[:] = [identifier(v.strip()) for v in self.value('senders').split(',') if v.strip()]
            else:
                if item.name != self.original.name and (item.is_multiplexer):
                    raise ValueError('Renaming multiplexer signals is not supported by this basic editor.')
                item.start = self.fields['start'].value()
                item.length = self.fields['length'].value()
                item.byte_order = self.fields['byte_order'].currentData()
                item.is_signed = self.fields['is_signed'].isChecked()
                choices = {}
                for line in self.fields['choices'].toPlainText().splitlines():
                    if not line.strip():
                        continue
                    key, label = line.split('=', 1)
                    key = int(key.strip(), 16 if key.strip().lower().startswith('0x') else 10)
                    if key in choices or not label.strip():
                        raise ValueError('Value labels need unique integer keys and nonempty labels.')
                    original_choice = (self.original.choices or {}).get(key)
                    choices[key] = deepcopy(original_choice) if str(original_choice) == label.strip() else label.strip()
                item.conversion = BaseConversion.factory(number(self.value('scale')), number(self.value('offset')),
                                                         choices or None, item.is_float)
                item.minimum = number(self.value('minimum'), True)
                item.maximum = number(self.value('maximum'), True)
                item.unit = self.value('unit') or None
                item.receivers = [identifier(v.strip()) for v in self.value('receivers').split(',') if v.strip()]
            self.result_item = item
            super().accept()
        except (ValueError, TypeError) as exc:
            self.error.setText(str(exc))


class DbcEditorWindow(QMainWindow):
    closed = Signal(object)
    saved = Signal(str, object)

    def __init__(self, database=None, path=None, parent=None):
        super().__init__(parent, Qt.WindowType.Window)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.document = DbcDocument(database, path)
        self.resize(1400, 800)
        self._build()
        self.refresh()

    @property
    def path(self):
        return self.document.path

    def _build(self):
        bar = QToolBar('DBC', self)
        self.addToolBar(bar)
        menu = self.menuBar().addMenu('&File')
        for name, callback, shortcut in (
            ('New', self.new_file, QKeySequence.StandardKey.New),
            ('Open...', self.open_file, QKeySequence.StandardKey.Open),
            ('Save', self.save_file, QKeySequence.StandardKey.Save),
            ('Save As...', self.save_as, QKeySequence.StandardKey.SaveAs),
        ):
            action = QAction(name, self)
            action.triggered.connect(callback)
            action.setShortcut(shortcut)
            bar.addAction(action)
            menu.addAction(action)
        validate = QAction('Validate', self)
        validate.triggered.connect(self.validate)
        bar.addAction(validate)
        edit_menu = self.menuBar().addMenu('&Edit')
        for name, callback, shortcut in (
            ('Undo', self.undo, QKeySequence.StandardKey.Undo),
            ('Redo', self.redo, QKeySequence.StandardKey.Redo),
        ):
            action = QAction(name, self)
            action.triggered.connect(callback)
            action.setShortcut(shortcut)
            edit_menu.addAction(action)
            bar.addAction(action)
        splitter = QSplitter()
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.addWidget(QLabel('Messages (CAN ID in hex)'))
        self.messages = QListWidget()
        self.messages.currentRowChanged.connect(self.select_message)
        self.messages.itemDoubleClicked.connect(lambda *_: self.edit_message())
        left_layout.addWidget(self.messages)
        self.buttons(left_layout, [('Add', self.add_message), ('Edit', self.edit_message), ('Delete', self.delete_message)])
        splitter.addWidget(left)
        right = QWidget()
        layout = QVBoxLayout(right)
        self.summary = QLabel()
        layout.addWidget(self.summary)
        self.signals = QTableWidget(0, 9)
        self.signals.setHorizontalHeaderLabels(['Signal', 'Start', 'Bits', 'Order', 'Signed', 'Factor', 'Offset', 'Unit', 'Range'])
        self.signals.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.signals.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.signals.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.signals.itemDoubleClicked.connect(lambda *_: self.edit_signal())
        self.signals.currentCellChanged.connect(lambda *_: self.preview())
        signal_panel = QWidget()
        signal_layout = QVBoxLayout(signal_panel)
        signal_layout.setContentsMargins(0, 0, 0, 0)
        signal_layout.addWidget(QLabel('Signals'))
        signal_layout.addWidget(self.signals)
        self.buttons(signal_layout, [('Add signal', self.add_signal), ('Edit signal', self.edit_signal), ('Delete signal', self.delete_signal)])
        map_panel = QWidget()
        map_layout = QVBoxLayout(map_panel)
        map_layout.setContentsMargins(0, 0, 0, 0)
        map_layout.addWidget(QLabel('Message map — payload bytes × bits'))
        self.bits = QTableWidget(0, 8)
        self.bits.setHorizontalHeaderLabels([str(i) for i in range(7, -1, -1)])
        self.bits.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.bits.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        map_layout.addWidget(self.bits)
        self.map_selection = QLabel('Select a signal to highlight its payload bits.')
        self.map_selection.setWordWrap(True)
        map_layout.addWidget(self.map_selection)
        self.signal_map_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.signal_map_splitter.addWidget(signal_panel)
        self.signal_map_splitter.addWidget(map_panel)
        self.signal_map_splitter.setStretchFactor(0, 3)
        self.signal_map_splitter.setStretchFactor(1, 2)
        self.signal_map_splitter.setSizes([650, 470])
        layout.addWidget(self.signal_map_splitter, 3)
        tabs = QTabWidget()
        self.syntax = QPlainTextEdit()
        self.syntax.setReadOnly(True)
        tabs.addTab(self.syntax, 'DBC preview')
        layout.addWidget(tabs, 1)
        layout.addWidget(QLabel('Test decoding (offline only; no CAN transmission)'))
        self.payload = QLineEdit()
        self.payload.setPlaceholderText('Hex payload, e.g. FF F0')
        self.payload.textChanged.connect(self.decode_preview)
        layout.addWidget(self.payload)
        self.decoded = QPlainTextEdit()
        self.decoded.setReadOnly(True)
        self.decoded.setMaximumHeight(120)
        layout.addWidget(self.decoded)
        splitter.addWidget(right)
        splitter.setStretchFactor(1, 3)
        self.setCentralWidget(splitter)

    def buttons(self, layout, definitions):
        row = QHBoxLayout()
        for title, callback in definitions:
            button = QPushButton(title)
            button.clicked.connect(callback)
            row.addWidget(button)
        layout.addLayout(row)

    def current_message(self):
        row = self.messages.currentRow()
        return self.document.database.messages[row] if 0 <= row < len(self.document.database.messages) else None

    def refresh(self, row=None):
        if row is None:
            row = max(0, self.messages.currentRow())
        self.messages.blockSignals(True)
        self.messages.clear()
        for message in self.document.database.messages:
            self.messages.addItem(f'{message.frame_id:X}  {message.name}' + (' [EXT]' if message.is_extended_frame else ''))
        self.messages.setCurrentRow(min(row, len(self.document.database.messages)-1))
        self.messages.blockSignals(False)
        self.select_message()
        self.setWindowTitle(f"{'* ' if self.document.dirty else ''}{self.document.path or 'Untitled.dbc'} - Signal Explorer DBC Editor")

    def select_message(self, *_):
        message = self.current_message()
        self.signals.blockSignals(True)
        self.signals.setRowCount(0)
        self.summary.setText(f'{message.name} | {message.length} bytes | {"CAN FD" if message.is_fd else "Classic CAN"}' if message else 'Add a message to begin.')
        if message:
            for signal in message.signals:
                row = self.signals.rowCount()
                self.signals.insertRow(row)
                for col, value in enumerate((signal.name, signal.start, signal.length,
                    'Intel' if signal.byte_order == 'little_endian' else 'Motorola',
                    'Yes' if signal.is_signed else 'No', signal.scale, signal.offset,
                    signal.unit or '', f'{signal.minimum} .. {signal.maximum}')):
                    self.signals.setItem(row, col, QTableWidgetItem(str(value)))
            self.signals.resizeColumnsToContents()
        self.signals.blockSignals(False)
        self.preview()

    def commit(self, database, row=None):
        self.document.commit(database)
        self.refresh(row)

    def edit_object(self, item, apply):
        dialog = PropertiesDialog(item, self)
        while dialog.exec() == QDialog.DialogCode.Accepted:
            try:
                candidate = deepcopy(self.document.database)
                apply(candidate, dialog.result_item)
                self.commit(candidate)
                return
            except Exception as exc:
                dialog.error.setText(str(exc))

    def add_message(self):
        used = {m.frame_id for m in self.document.database.messages if not m.is_extended_frame}
        frame_id = next((i for i in range(0x800) if i not in used), None)
        if frame_id is None:
            QMessageBox.warning(self, 'No standard IDs available', 'Use an extended message or remove an existing standard message.')
            return
        item = Message(frame_id, f'Message_{frame_id:X}', 8, [])
        count = len(self.document.database.messages)
        self.edit_object(item, lambda db, obj: db.messages.append(obj))
        if len(self.document.database.messages) > count:
            self.messages.setCurrentRow(len(self.document.database.messages)-1)

    def edit_message(self):
        row = self.messages.currentRow()
        if self.current_message():
            self.edit_object(self.current_message(), lambda db, obj: db.messages.__setitem__(row, obj))

    def add_signal(self):
        message = self.current_message()
        if message is None:
            return
        used = set(b for s in message.signals for b in signal_bits(s))
        start = next((b for b in range(message.length*8) if b not in used), 0)
        names = {s.name for s in message.signals}
        name = next(f'Signal_{i}' for i in range(len(names)+1) if f'Signal_{i}' not in names)
        row = self.messages.currentRow()
        self.edit_object(CanSignal(name, start, 1), lambda db, obj: db.messages[row].signals.append(obj))

    def edit_signal(self):
        row, signal_row = self.messages.currentRow(), self.signals.currentRow()
        if self.current_message() and signal_row >= 0:
            def replace(db, obj):
                old = db.messages[row].signals[signal_row].name
                db.messages[row].signals[signal_row] = obj
                for group in db.messages[row].signal_groups or []:
                    group.signal_names[:] = [obj.name if name == old else name for name in group.signal_names]
            self.edit_object(self.current_message().signals[signal_row], replace)

    def delete_message(self):
        row = self.messages.currentRow()
        if self.current_message() and self.confirm_delete('message and its signals'):
            db = deepcopy(self.document.database)
            del db.messages[row]
            self.commit(db, max(0, row-1))

    def delete_signal(self):
        row, signal_row = self.messages.currentRow(), self.signals.currentRow()
        if not self.current_message() or signal_row < 0 or not self.confirm_delete('signal'):
            return
        db = deepcopy(self.document.database)
        signal = db.messages[row].signals[signal_row]
        if any(s.multiplexer_signal == signal.name for s in db.messages[row].signals):
            QMessageBox.warning(self, 'Signal in use', 'Remove dependent multiplexed signals first.')
            return
        del db.messages[row].signals[signal_row]
        for group in db.messages[row].signal_groups or []:
            group.signal_names[:] = [name for name in group.signal_names if name != signal.name]
        try:
            self.commit(db)
        except Exception as exc:
            QMessageBox.warning(self, 'Invalid edit', str(exc))

    def confirm_delete(self, name):
        return QMessageBox.question(self, 'Delete', f'Delete selected {name}?',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes

    def preview(self):
        message = self.current_message()
        self.bits.setRowCount(message.length if message else 0)
        self.map_selection.setText('Select a signal to highlight its payload bits.')
        if message:
            owners = {}
            for signal in message.signals:
                for bit in signal_bits(signal):
                    owners.setdefault(bit, []).append(signal.name)
            selected = self.signals.currentRow()
            selected_name = message.signals[selected].name if 0 <= selected < len(message.signals) else None
            if selected_name:
                signal = message.signals[selected]
                self.map_selection.setText(f'{signal.name}: start bit {signal.start}, {signal.length} bits, '
                                           f'{"Intel" if signal.byte_order == "little_endian" else "Motorola"}')
            for byte in range(message.length):
                self.bits.setVerticalHeaderItem(byte, QTableWidgetItem(f'Byte {byte}'))
                for col in range(8):
                    names = owners.get(byte*8 + 7-col, [])
                    label = '/'.join(names)
                    cell = QTableWidgetItem((label[:8] + '…' if len(label) > 9 else label) if names else '—')
                    cell.setToolTip(f'DBC bit {byte*8+7-col}: ' + ', '.join(names))
                    if selected_name in names:
                        cell.setBackground(QColor('#b7d9f7'))
                    self.bits.setItem(byte, col, cell)
        try:
            self.syntax.setPlainText(self.document.database.as_dbc_string())
        except Exception as exc:
            self.syntax.setPlainText(str(exc))
        self.decode_preview()

    def decode_preview(self, *_):
        message = self.current_message()
        if message is None:
            self.decoded.clear()
            return
        try:
            payload = bytes.fromhex(self.payload.text())
            if len(payload) != message.length:
                raise ValueError(f'Enter exactly {message.length} payload bytes.')
            values = message.decode(payload)
            units = {s.name: s.unit or '' for s in message.signals}
            self.decoded.setPlainText('\n'.join(f'{name} = {value} {units[name]}'.strip() for name, value in values.items()))
        except Exception as exc:
            self.decoded.setPlainText(str(exc))

    def validate(self):
        try:
            validate_database(self.document.database)
            self.statusBar().showMessage('DBC is valid: no overlapping or out-of-bounds signals.', 6000)
            return True
        except Exception as exc:
            QMessageBox.warning(self, 'DBC validation failed', str(exc))
            return False

    def undo(self):
        self.document.undo()
        self.refresh()

    def redo(self):
        self.document.redo()
        self.refresh()

    def confirm_save(self):
        if not self.document.dirty:
            return True
        answer = QMessageBox.question(self, 'Unsaved DBC', 'Save changes before continuing?',
            QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Save)
        return answer == QMessageBox.StandardButton.Discard or (answer == QMessageBox.StandardButton.Save and self.save_file())

    def new_file(self):
        if self.confirm_save():
            self.document = DbcDocument()
            self.refresh()

    def open_file(self, path=None):
        if not self.confirm_save():
            return False
        if not isinstance(path, (str, Path)):
            path, _ = QFileDialog.getOpenFileName(self, 'Open DBC', '', 'DBC files (*.dbc)')
        if not path:
            return False
        try:
            database = cantools.database.load_file(str(path), sort_signals=None)
            self.document = DbcDocument(database, path)
            self.refresh(0)
            return True
        except Exception as exc:
            QMessageBox.warning(self, 'DBC open failed', str(exc))
            return False

    def save_as(self):
        path, _ = QFileDialog.getSaveFileName(self, 'Save DBC', str(self.document.path or 'Untitled.dbc'), 'DBC files (*.dbc)')
        return self.save_file(path=path) if path else False

    def save_file(self, _checked=False, *, path=None):
        target = Path(path).resolve() if path else self.document.path
        if target is None:
            return self.save_as()
        try:
            db, text = self.document.save_snapshot()
            # Verify the serialized file before replacing the destination atomically.
            encoded = text.encode('cp1252')
            cantools.database.load_string(text, database_format='dbc', strict=True)
            from PySide6.QtCore import QSaveFile, QIODevice
            output = QSaveFile(str(target))
            if not output.open(QIODevice.OpenModeFlag.WriteOnly):
                raise OSError(output.errorString())
            if output.write(encoded) != len(encoded) or not output.commit():
                raise OSError(output.errorString())
            self.document.database, self.document.path = db, target
            self.document.mark_saved()
            self.refresh()
            self.saved.emit(str(target), deepcopy(db))
            self.statusBar().showMessage('DBC saved.', 5000)
            return True
        except Exception as exc:
            QMessageBox.warning(self, 'DBC save failed', str(exc))
            return False

    def closeEvent(self, event):
        if not self.confirm_save():
            event.ignore()
            return
        self.closed.emit(self)
        event.accept()
