from collections import deque
from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt

COLUMNS = ("Timestamp", "Counts", "Bus Number", "Frame ID (hex)", "PID (hex)", "Direction",
           "Event", "Length", "Data", "Checksum", "Model", "Publisher", "LDF Frame", "Signals")


class LinTraceModel(QAbstractTableModel):
    def __init__(self):
        super().__init__()
        self.history = deque()
        self.latest = {}
        self.keys = []
        self.history_mode = True
        self.database = None

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.history if self.history_mode else self.keys)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(COLUMNS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
            return COLUMNS[section]

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid(): return None
        event, count = self.history[index.row()] if self.history_mode else self.latest[self.keys[index.row()]]
        if role == Qt.ItemDataRole.UserRole: return event.frame_id
        if role != Qt.ItemDataRole.DisplayRole: return None
        name, signals = self.database.decode(event) if self.database else ("", "")
        return (f"{event.timestamp:.6f}", str(count), str(event.channel), f"{event.frame_id:02X}",
                f"{event.pid:02X}", event.direction.upper(), event.kind, str(len(event.data)),
                event.data.hex(' ').upper(), f"{event.checksum:02X}" if event.checksum is not None else "",
                event.model, event.publisher, name, signals)[index.column()]

    def append(self, event):
        key = (event.channel, event.frame_id, event.kind)
        count = self.latest.get(key, (None, 0))[1] + 1
        existing = key in self.latest
        if self.history_mode:
            if len(self.history) == 10000:
                self.beginRemoveRows(QModelIndex(), 0, 0); self.history.popleft(); self.endRemoveRows()
            row = len(self.history)
            self.beginInsertRows(QModelIndex(), row, row)
        elif not existing:
            row = len(self.keys)
            self.beginInsertRows(QModelIndex(), row, row)
        self.latest[key] = (event, count)
        if not existing: self.keys.append(key)
        self.history.append((event, count))
        if not self.history_mode and len(self.history) > 10000: self.history.popleft()
        if self.history_mode or not existing:
            self.endInsertRows()
        else:
            row = self.keys.index(key)
            self.dataChanged.emit(self.index(row,0), self.index(row,len(COLUMNS)-1))

    def set_history_mode(self, enabled):
        self.beginResetModel(); self.history_mode = enabled; self.endResetModel()

    def set_database(self, database):
        self.beginResetModel(); self.database = database; self.endResetModel()

    def clear(self):
        self.beginResetModel(); self.history.clear(); self.latest.clear(); self.keys.clear(); self.endResetModel()
