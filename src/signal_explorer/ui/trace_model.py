from __future__ import annotations
from copy import deepcopy
from collections import deque
from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, QSortFilterProxyModel

_COLUMNS = ("Timestamp", "Counts", "Bus Number", "Message ID (hex)", "Ext", "Direction", "DLC", "Data", "DBC Message", "Signals")

class TraceModel(QAbstractTableModel):
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
        return (f"{msg.timestamp:.6f}", str(count), str(msg.channel) if msg.channel is not None else "",
                f"{msg.arbitration_id:03X}", "Y" if msg.is_extended_id else "",
                "RX" if msg.is_rx else "TX", str(msg.dlc),
                msg.data.hex(" ").upper(), name, signals)[index.column()]

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
            self._latest[row] = (frame, count)
        else:
            self._indices[key] = row
            self._latest.append((frame, count))
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
        self._history.append((frame, count))
        if self.history_mode:
            self.endInsertRows()

    def clear(self):
        self.beginResetModel()
        self._history.clear()
        self._latest.clear()
        self._indices.clear()
        self.endResetModel()


class IdFilter(QSortFilterProxyModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.ranges = []

    def set_expression(self, text):
        ranges = []
        for token in text.replace(" ", ",").split(","):
            if not token:
                continue
            parts = token.split("-")
            if len(parts) > 2:
                raise ValueError("Use hexadecimal IDs or ranges, e.g. 100, 200-2FF")
            low, high = int(parts[0], 16), int(parts[-1], 16)
            if not 0 <= low <= high <= 0x1FFFFFFF:
                raise ValueError("ID range must be between 0 and 1FFFFFFF.")
            ranges.append((low, high))
        self.ranges = ranges
        self.invalidateFilter()

    def filterAcceptsRow(self, row, parent):
        value = self.sourceModel().data(self.sourceModel().index(row, 0, parent), Qt.ItemDataRole.UserRole)
        return not self.ranges or any(low <= value <= high for low, high in self.ranges)
