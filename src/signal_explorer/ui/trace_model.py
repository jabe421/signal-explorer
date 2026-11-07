from __future__ import annotations
from dataclasses import dataclass, field
from PySide6.QtCore import QAbstractItemModel, QModelIndex, Qt, QSortFilterProxyModel, Signal
from signal_explorer.bus.frames import snapshot_message
from signal_explorer.ui.trace_timestamps import FORMAT_DETAILS, format_timestamp, valid_timestamp

_COLUMNS = ("Timestamp", "Counts", "CAN Channel", "Message ID (hex)", "Ext", "Direction", "DLC", "Data", "DBC Message", "Signals", "FD", "BRS", "ESI")


@dataclass(slots=True, eq=False)
class _Record:
    frame: object
    count: int
    position: int
    revision: int = -1
    name: str = ''
    summary: str = ''
    values: dict = field(default_factory=dict)
    error: str = ''
    relative: float | None = None
    delta: float | None = None
    delta_id: float | None = None
    time_revision: int = -1
    time_text: str = ''


class TraceModel(QAbstractItemModel):
    def __init__(self, history_mode=True, max_history=10_000):
        super().__init__()
        if max_history < 1:
            raise ValueError('History size must be positive')
        self.history_mode = history_mode
        self.max_history = max_history
        # Qt paints by row number: lists provide constant-time random access.
        # The bounded history is trimmed once per batch, not once per frame.
        self._history = []
        self._latest = []
        self._rows = self._history if history_mode else self._latest
        self._indices = {}
        self._history_next = 0
        self._history_offset = 0
        self.database = None
        self._definitions = {}
        self._units = {}
        self._revision = 0
        self.channel_labels = {}
        self.timestamp_mode = 'epoch'
        self._time_revision = 0
        self._session_start = self._time_origin = None
        self._previous_time = None
        self._previous_by_id = {}

    def start_session(self, timestamp):
        """Reset interval baselines; retained rows keep their original timings."""
        if not valid_timestamp(timestamp):
            raise ValueError('Connection start timestamp must be finite.')
        self._session_start = self._time_origin = timestamp
        self._previous_time = None
        self._previous_by_id.clear()

    def set_timestamp_mode(self, mode):
        if mode not in FORMAT_DETAILS:
            raise ValueError('Unknown trace timestamp format.')
        if mode == self.timestamp_mode:
            return
        self.timestamp_mode = mode
        self._time_revision += 1
        self.headerDataChanged.emit(Qt.Orientation.Horizontal, 0, 0)
        if self._rows:
            self.dataChanged.emit(self.index(0, 0), self.index(len(self._rows)-1, 0))

    def _timestamp_text(self, record):
        if record.time_revision != self._time_revision:
            record.time_text = format_timestamp(self.timestamp_mode, record.frame.timestamp,
                                                record.relative, record.delta, record.delta_id)
            record.time_revision = self._time_revision
        return record.time_text

    def index(self, row, column, parent=QModelIndex()):
        if parent.isValid() or not 0 <= row < len(self._rows) or not 0 <= column < len(_COLUMNS):
            return QModelIndex()
        return self.createIndex(row, column)

    def parent(self, index):
        return QModelIndex()

    def set_channel_labels(self, labels):
        self.channel_labels.update(labels)
        if self._rows:
            self.dataChanged.emit(self.index(0, 2), self.index(len(self._rows)-1, 2))

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(_COLUMNS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and section == 0:
            if role == Qt.ItemDataRole.ToolTipRole:
                return FORMAT_DETAILS[self.timestamp_mode][2]
            if role == Qt.ItemDataRole.DisplayRole:
                return FORMAT_DETAILS[self.timestamp_mode][1]
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
            return _COLUMNS[section]

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not 0 <= index.row() < len(self._rows):
            return None
        record = self._rows[index.row()]
        msg = record.frame
        if role == Qt.ItemDataRole.ToolTipRole and index.column() in (0, 5):
            return (f"Display: {FORMAT_DETAILS[self.timestamp_mode][2]}\n"
                    f"Original epoch timestamp: {msg.timestamp}\n"
                    f"Timestamp: {getattr(msg, 'timestamp_source', 'unspecified')}\n"
                    f"Native timestamp: {getattr(msg, 'raw_timestamp', None)}\n"
                    f"TX evidence: {getattr(msg, 'tx_confirmation', 'none')}")
        if role == Qt.ItemDataRole.UserRole:
            return msg.arbitration_id
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        column = index.column()
        if column == 0: return self._timestamp_text(record)
        if column == 1: return str(record.count)
        if column == 2: return self.channel_labels.get(str(msg.channel), str(msg.channel)) if msg.channel is not None else ''
        if column == 3: return f'{msg.arbitration_id:03X}' + (' (Error Frame)' if msg.is_error_frame else '')
        if column == 4: return 'Y' if msg.is_extended_id else ''
        if column == 5: return 'RX' if msg.is_rx else 'TX'
        if column == 6: return str(msg.dlc)
        if column == 7: return msg.data.hex(' ').upper()
        if column in (8, 9):
            self._decode_record(record)
            return record.name if column == 8 else record.summary
        if column == 10: return 'Y' if msg.is_fd else ''
        if column == 11: return 'Y' if msg.bitrate_switch else ''
        if column == 12: return 'Y' if msg.error_state_indicator else ''

    def _definition_for_frame(self, msg):
        if msg.is_error_frame or msg.is_remote_frame:
            return None
        return self._definitions.get((msg.arbitration_id, msg.is_extended_id))

    def _decode_record(self, record):
        if record.revision == self._revision:
            return
        record.revision = self._revision
        record.name = record.summary = record.error = ''
        record.values = {}
        definition = self._definition_for_frame(record.frame)
        if definition is None:
            return
        record.name = definition.name
        try:
            record.values = definition.decode(record.frame.data)
            units = self._units[(record.frame.arbitration_id, record.frame.is_extended_id)]
            record.summary = '; '.join(f"{key}={value} {units.get(key, '')}".strip()
                                       for key, value in record.values.items())
        except Exception as exc:
            record.error = record.summary = f'Decode error: {exc}'

    def _configure_database(self, database):
        self.database = database
        self._revision += 1
        self._definitions = {}
        self._units = {}
        for definition in database.messages if database is not None else ():
            key = definition.frame_id, definition.is_extended_frame
            # Preserve the previous first-match behavior for duplicate definitions.
            if key not in self._definitions:
                self._definitions[key] = definition
                self._units[key] = {s.name: s.unit or '' for s in definition.signals}

    def set_database(self, database):
        self._configure_database(database)
        if self._rows:
            self.dataChanged.emit(self.index(0, 8), self.index(len(self._rows)-1, 9))

    def set_history_mode(self, enabled):
        if self.history_mode == enabled:
            return
        self.beginResetModel()
        self.history_mode = enabled
        self._rows = self._history if enabled else self._latest
        self.endResetModel()

    def append_message(self, msg):
        self.append_messages([msg])

    def _before_latest_change(self, record, frame):
        return None

    def _after_latest_change(self, record, operation):
        pass

    def append_messages(self, messages, *, owned=False):
        """Append a batch; only the service may transfer owned, immutable snapshots."""
        updates = {}
        history = []
        keep_from = max(0, len(messages) - self.max_history)
        for offset, msg in enumerate(messages):
            frame = msg if owned else snapshot_message(msg)
            key = frame.channel, frame.arbitration_id, frame.is_extended_id
            stamp = frame.timestamp if valid_timestamp(frame.timestamp) else None
            if self._time_origin is None and stamp is not None:
                self._time_origin = stamp
            relative = stamp - self._time_origin if stamp is not None else None
            previous_id = self._previous_by_id.get(key)
            delta = stamp - self._previous_time if stamp is not None and self._previous_time is not None else None
            delta_id = stamp - previous_id if stamp is not None and previous_id is not None else None
            self._previous_time = stamp
            self._previous_by_id[key] = stamp
            if key in updates:
                count = updates[key][1] + 1
            else:
                previous = self._indices.get(key)
                count = self._latest[previous].count + 1 if previous is not None else 1
            updates[key] = frame, count, relative, delta, delta_id
            if offset >= keep_from:
                history.append(_Record(frame, count, self._history_next + offset,
                                       relative=relative, delta=delta, delta_id=delta_id))
        self._history_next += len(messages)

        changed = []
        new = []
        for key, (frame, count, relative, delta, delta_id) in updates.items():
            row = self._indices.get(key)
            if row is None:
                row = len(self._latest) + len(new)
                new.append((key, _Record(frame, count, row, relative=relative, delta=delta, delta_id=delta_id)))
            else:
                record = self._latest[row]
                operation = self._before_latest_change(record, frame) if not self.history_mode else None
                record.frame, record.count, record.revision = frame, count, -1
                record.relative, record.delta, record.delta_id = relative, delta, delta_id
                record.time_revision = -1
                if not self.history_mode:
                    self._after_latest_change(record, operation)
                    changed.append(row)
        if new:
            first = len(self._latest)
            if not self.history_mode:
                self.beginInsertRows(QModelIndex(), first, first+len(new)-1)
            for key, record in new:
                self._indices[key] = len(self._latest)
                self._latest.append(record)
            if not self.history_mode:
                self.endInsertRows()
        if changed:
            self.dataChanged.emit(self.index(min(changed), 0), self.index(max(changed), len(_COLUMNS)-1))

        remove = min(len(self._history), max(0, len(self._history)+len(history)-self.max_history))
        if remove:
            if self.history_mode:
                self.beginRemoveRows(QModelIndex(), 0, remove-1)
            del self._history[:remove]
            self._history_offset += remove
            if self.history_mode:
                self.endRemoveRows()
        if history:
            first = len(self._history)
            if self.history_mode:
                self.beginInsertRows(QModelIndex(), first, first+len(history)-1)
            if not self._history:
                self._history_offset = history[0].position
            self._history.extend(history)
            if self.history_mode:
                self.endInsertRows()

    def clear(self):
        self.beginResetModel()
        self._history.clear()
        self._latest.clear()
        self._indices.clear()
        self._history_next = self._history_offset = 0
        self._time_origin = self._session_start
        self._previous_time = None
        self._previous_by_id.clear()
        self.endResetModel()


class SignalTreeModel(TraceModel):
    """Child indexes retain stable records; parent lookup is constant-time."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._expanded = set()
        self.modelReset.connect(self._expanded.clear)
        self.rowsAboutToBeRemoved.connect(self._forget_rows)

    def _forget_rows(self, parent, first, last):
        if not parent.isValid():
            for record in self._rows[first:last+1]:
                self._expanded.discard(id(record))

    def _definition(self, record):
        return self._definition_for_frame(record.frame)

    def index(self, row, column, parent=QModelIndex()):
        if row < 0 or not 0 <= column < len(_COLUMNS):
            return QModelIndex()
        if not parent.isValid():
            return self.createIndex(row, column) if row < len(self._rows) else QModelIndex()
        if parent.column() != 0 or parent.internalPointer() is not None or parent.row() >= len(self._rows):
            return QModelIndex()
        record = self._rows[parent.row()]
        definition = self._definition(record)
        if definition is not None and row < len(definition.signals):
            return self.createIndex(row, column, record)
        return QModelIndex()

    def parent(self, index):
        record = index.internalPointer() if index.isValid() else None
        if record is not None:
            row = record.position - self._history_offset if self.history_mode else record.position
            if 0 <= row < len(self._rows) and self._rows[row] is record:
                return self.createIndex(row, 0)
        return QModelIndex()

    def rowCount(self, parent=QModelIndex()):
        if not parent.isValid():
            return len(self._rows)
        if parent.column() != 0 or parent.internalPointer() is not None or parent.row() >= len(self._rows):
            return 0
        definition = self._definition(self._rows[parent.row()])
        return len(definition.signals) if definition is not None else 0

    def columnCount(self, parent=QModelIndex()):
        return len(_COLUMNS)

    def flags(self, index):
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        return Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable

    def hasChildren(self, parent=QModelIndex()):
        return self.rowCount(parent) > 0

    def set_expanded(self, index, expanded):
        if not index.isValid() or index.internalPointer() is not None:
            return
        key = id(self._rows[index.row()])
        self._expanded.add(key) if expanded else self._expanded.discard(key)
        cell = self.index(index.row(), 9)
        self.dataChanged.emit(cell, cell)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        record = index.internalPointer()
        if record is None:
            if role == Qt.ItemDataRole.DisplayRole and index.column() == 9 and id(self._rows[index.row()]) in self._expanded:
                return ''
            return super().data(index, role)
        if role == Qt.ItemDataRole.UserRole:
            return record.frame.arbitration_id
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        definition = self._definition(record)
        if definition is None or index.row() >= len(definition.signals):
            return None
        signal = definition.signals[index.row()]
        if index.column() == 0:
            return signal.name
        if index.column() == 9:
            self._decode_record(record)
            return record.error or f"{record.values.get(signal.name, '—')} {signal.unit or ''}".strip()
        return ''

    def set_database(self, database):
        self.beginResetModel()
        self._configure_database(database)
        self.endResetModel()

    def _before_latest_change(self, record, frame):
        previous = self._definition(record)
        current = self._definition_for_frame(frame)
        if previous is not None and current is None:
            if previous.signals:
                self.beginRemoveRows(self.index(record.position, 0), 0, len(previous.signals)-1)
            self._expanded.discard(id(record))
            return 'remove' if previous.signals else None
        if previous is None and current is not None and current.signals:
            self.beginInsertRows(self.index(record.position, 0), 0, len(current.signals)-1)
            return 'insert'

    def _after_latest_change(self, record, operation):
        if operation == 'remove': self.endRemoveRows()
        if operation == 'insert': self.endInsertRows()
        if id(record) in self._expanded:
            parent = self.index(record.position, 0)
            count = self.rowCount(parent)
            if count:
                self.dataChanged.emit(self.index(0, 9, parent), self.index(count-1, 9, parent))


class IdFilter(QSortFilterProxyModel):
    filteringAboutToChange = Signal()

    def __init__(self, parent=None, max_id=0x1FFFFFFF):
        super().__init__(parent)
        self.ranges = []
        self.max_id = max_id

    def set_expression(self, text):
        ranges = []
        for token in text.replace(" ", ",").split(","):
            if not token:
                continue
            parts = token.split("-")
            if len(parts) > 2:
                raise ValueError("Use hexadecimal IDs or ranges, e.g. 100, 200-2FF")
            low, high = int(parts[0], 16), int(parts[-1], 16)
            if not 0 <= low <= high <= self.max_id:
                raise ValueError(f"ID range must be between 0 and {self.max_id:X}.")
            ranges.append((low, high))
        self.filteringAboutToChange.emit()
        self.ranges = ranges
        self.invalidateFilter()

    def filterAcceptsRow(self, row, parent):
        value = self.sourceModel().data(self.sourceModel().index(row, 0, parent), Qt.ItemDataRole.UserRole)
        return not self.ranges or any(low <= value <= high for low, high in self.ranges)
