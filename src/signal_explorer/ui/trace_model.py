from __future__ import annotations
from copy import deepcopy
from collections import deque
from PySide6.QtCore import QAbstractItemModel, QModelIndex, Qt, QSortFilterProxyModel, Signal

_COLUMNS = ("Timestamp", "Counts", "CAN Channel", "Message ID (hex)", "Ext", "Direction", "DLC", "Data", "DBC Message", "Signals", "FD", "BRS", "ESI")

class TraceModel(QAbstractItemModel):
    def __init__(self, history_mode=True, max_history=10_000):
        super().__init__()
        if max_history < 1:
            raise ValueError("History size must be positive")
        self.history_mode = history_mode
        self.max_history = max_history
        self._history = deque()
        self._latest = []
        self._rows = self._history if history_mode else self._latest
        self._indices = {}
        self.database = None
        self.channel_labels = {}

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
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
            return _COLUMNS[section]

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        msg, count = self._rows[index.row()]
        if role == Qt.ItemDataRole.UserRole:
            return msg.arbitration_id
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        name, signals = self._decode(msg)
        return (f"{msg.timestamp:.6f}", str(count), self.channel_labels.get(str(msg.channel), str(msg.channel)) if msg.channel is not None else "",
                f"{msg.arbitration_id:03X}" + (" (Error Frame)" if msg.is_error_frame else ""), "Y" if msg.is_extended_id else "",
                "RX" if msg.is_rx else "TX", str(msg.dlc),
                msg.data.hex(" ").upper(), name, signals, "Y" if msg.is_fd else "",
                "Y" if msg.bitrate_switch else "", "Y" if msg.error_state_indicator else "")[index.column()]

    def _decode(self, msg):
        if self.database is None or msg.is_error_frame or msg.is_remote_frame:
            return "", ""
        definition = next((m for m in self.database.messages
                           if m.frame_id == msg.arbitration_id and m.is_extended_frame == msg.is_extended_id), None)
        if definition is None:
            return "", ""
        try:
            values = definition.decode(msg.data)
            units = {s.name: s.unit or "" for s in definition.signals}
            return definition.name, "; ".join(f"{key}={value} {units.get(key, '')}".strip()
                                               for key, value in values.items())
        except Exception as exc:
            return definition.name, f"Decode error: {exc}"

    def set_database(self, database):
        self.database = database
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
        key = (msg.channel, msg.arbitration_id, msg.is_extended_id)
        frame = deepcopy(msg)
        existing = key in self._indices
        row = self._indices.get(key, len(self._latest))
        count = self._latest[row][1] + 1 if existing else 1
        if not self.history_mode and not existing:
            self.beginInsertRows(QModelIndex(), row, row)
        if existing:
            self._latest[row][:] = [frame, count]
        else:
            self._indices[key] = row
            self._latest.append([frame, count])
        if not self.history_mode:
            if existing:
                self.dataChanged.emit(self.index(row, 0), self.index(row, len(_COLUMNS)-1))
            else:
                self.endInsertRows()
        if len(self._history) == self.max_history:
            if self.history_mode:
                self.beginRemoveRows(QModelIndex(), 0, 0)
            self._history.popleft()
            if self.history_mode:
                self.endRemoveRows()
        at = len(self._history)
        if self.history_mode:
            self.beginInsertRows(QModelIndex(), at, at)
        self._history.append([frame, count])
        if self.history_mode:
            self.endInsertRows()

    def clear(self):
        self.beginResetModel()
        self._history.clear()
        self._latest.clear()
        self._indices.clear()
        self.endResetModel()


class SignalTreeModel(TraceModel):
    """Monitor hierarchy; child indexes retain their owning frame record."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._expanded = set()
        self.modelReset.connect(self._expanded.clear)
        self.rowsAboutToBeRemoved.connect(self._forget_rows)

    def _forget_rows(self, parent, first, last):
        if not parent.isValid():
            for row in range(first, last + 1):
                self._expanded.discard(id(self._rows[row]))

    def _definition(self, record):
        msg = record[0]
        if self.database is None or msg.is_error_frame or msg.is_remote_frame:
            return None
        return next((m for m in self.database.messages
                     if m.frame_id == msg.arbitration_id
                     and m.is_extended_frame == msg.is_extended_id), None)

    def index(self, row, column, parent=QModelIndex()):
        if row < 0 or not 0 <= column < len(_COLUMNS):
            return QModelIndex()
        if not parent.isValid():
            return self.createIndex(row, column) if row < len(self._rows) else QModelIndex()
        if parent.column() != 0 or parent.internalPointer() is not None:
            return QModelIndex()
        record = self._rows[parent.row()]
        definition = self._definition(record)
        if definition is not None and row < len(definition.signals):
            return self.createIndex(row, column, record)
        return QModelIndex()

    def parent(self, index):
        record = index.internalPointer() if index.isValid() else None
        if record is not None:
            for row, candidate in enumerate(self._rows):
                if candidate is record:
                    return self.createIndex(row, 0)
        return QModelIndex()

    def rowCount(self, parent=QModelIndex()):
        if not parent.isValid():
            return len(self._rows)
        if parent.column() != 0 or parent.internalPointer() is not None:
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
        if expanded:
            self._expanded.add(key)
        else:
            self._expanded.discard(key)
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
            return record[0].arbitration_id
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        definition = self._definition(record)
        if definition is None or index.row() >= len(definition.signals):
            return None
        signal = definition.signals[index.row()]
        if index.column() == 0:
            return signal.name
        if index.column() == 9:
            try:
                value = definition.decode(record[0].data).get(signal.name, '—')
                return f"{value} {signal.unit or ''}".strip()
            except Exception as exc:
                return f"Decode error: {exc}"
        return ''

    def set_database(self, database):
        self.beginResetModel()
        self.database = database
        self.endResetModel()

    def append_message(self, msg):
        super().append_message(msg)
        if not self.history_mode:
            row = self._indices[(msg.channel, msg.arbitration_id, msg.is_extended_id)]
            parent = self.index(row, 0)
            count = self.rowCount(parent)
            if count:
                self.dataChanged.emit(self.index(0, 0, parent), self.index(count - 1, len(_COLUMNS) - 1, parent))


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
