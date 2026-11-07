"""Support details kept out of the main monitoring controls."""

from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)
from signal_explorer.diagnostics import diagnostics_report


class DiagnosticsWindow(QDialog):
    def __init__(self, context, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Signal Explorer — Diagnostics")
        self.resize(850, 620)
        self.context = context
        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        self.status = QLabel(
            "Devices reflect the most recent Refresh devices scan; no device is opened by this report."
        )
        refresh = QPushButton("Refresh report")
        refresh.clicked.connect(self.refresh)
        copy = QPushButton("Copy diagnostics")
        copy.clicked.connect(self.copy_report)
        close = QPushButton("Close")
        close.clicked.connect(self.close)
        buttons = QHBoxLayout()
        for widget in (refresh, copy, close):
            buttons.addWidget(widget)
        layout = QVBoxLayout(self)
        layout.addWidget(self.status)
        layout.addWidget(self.text)
        layout.addLayout(buttons)
        self.refresh()

    def refresh(self):
        self.text.setPlainText(diagnostics_report(self.context()))

    def copy_report(self):
        self.refresh()
        QApplication.clipboard().setText(self.text.toPlainText())
        self.status.setText("Diagnostics copied to clipboard.")
