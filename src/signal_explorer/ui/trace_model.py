from __future__ import annotations

from collections import deque

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt
from can import Message

_COLUMNS = ("Time", "ID", "Ext", "DLC", "Data")
_MAX_ROWS = 10_000


class TraceModel(QAbstractTableModel):
    def __init__(self) -> None:
        super().__init__()
        self._rows: deque[tuple[str, str, str, str, str]] = deque(maxlen=_MAX_ROWS)

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        if parent.isValid():
            return 0
        return len(self._rows)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        if parent.isValid():
            return 0
        return len(_COLUMNS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):  # noqa: N802
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        if orientation == Qt.Orientation.Horizontal:
            return _COLUMNS[section]
        return str(section)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or role != Qt.ItemDataRole.DisplayRole:
            return None
        return self._rows[index.row()][index.column()]

    def append_message(self, msg: Message) -> None:
        row = (
            f"{msg.timestamp:.6f}",
            f"{msg.arbitration_id:X}",
            "Y" if msg.is_extended_id else "",
            str(msg.dlc),
            msg.data.hex(" ").upper(),
        )
        at_cap = len(self._rows) == self._rows.maxlen
        if at_cap:
            self.beginRemoveRows(QModelIndex(), 0, 0)
            self._rows.popleft()
            self.endRemoveRows()
        next_row = len(self._rows)
        self.beginInsertRows(QModelIndex(), next_row, next_row)
        self._rows.append(row)
        self.endInsertRows()

    def clear(self) -> None:
        self.beginResetModel()
        self._rows.clear()
        self.endResetModel()
