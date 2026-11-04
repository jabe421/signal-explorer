from PySide6.QtCore import QRect
from PySide6.QtWidgets import QTreeView


class SignalTreeView(QTreeView):
    """Use explicit plus/minus controls for expandable CAN messages."""

    def drawBranches(self, painter, rect, index):
        if not self.model().hasChildren(index):
            return
        x = rect.right() - self.indentation() // 2
        y = rect.center().y()
        painter.save()
        painter.setPen(self.palette().text().color())
        painter.drawRect(QRect(x - 5, y - 5, 10, 10))
        painter.drawLine(x - 3, y, x + 3, y)
        if not self.isExpanded(index):
            painter.drawLine(x, y - 3, x, y + 3)
        painter.restore()
