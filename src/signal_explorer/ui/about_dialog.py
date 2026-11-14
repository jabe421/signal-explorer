from html import escape
from importlib.resources import files

from PySide6.QtCore import Qt
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QLabel, QTextBrowser, QVBoxLayout


class AboutDialog(QDialog):
    def __init__(self, version, parent=None):
        super().__init__(parent)
        self.setWindowTitle("About Signal Explorer")
        self.resize(680, 500)

        summary = QLabel(
            f"Signal Explorer\nVersion {version}\n\n"
            "An open-source desktop tool for monitoring, transmitting, recording "
            "and replaying CAN, CAN FD, LIN and Ethernet traffic. "
            "Use a supported hardware adapter or try the built-in simulation."
        )
        summary.setWordWrap(True)
        summary.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        instructions = QTextBrowser()
        instructions.setOpenExternalLinks(True)
        instructions.setAccessibleName("Windows driver setup instructions")
        driver_setup = files("signal_explorer").joinpath("assets", "Driver Setup.txt")
        paragraphs = []
        for paragraph in driver_setup.read_text(encoding="utf-8").strip().split("\n\n"):
            lines = []
            for line in paragraph.splitlines():
                text = escape(line)
                if line.startswith("https://"):
                    lines.append(f'<br><a href="{text}">{text}</a>')
                else:
                    lines.append(text)
            paragraphs.append("<p>" + " ".join(lines) + "</p>")
        instructions.setHtml("".join(paragraphs))
        instructions.moveCursor(QTextCursor.MoveOperation.Start)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        buttons.button(QDialogButtonBox.StandardButton.Close).setFocus()

        layout = QVBoxLayout(self)
        layout.addWidget(summary)
        layout.addWidget(instructions, stretch=1)
        layout.addWidget(buttons)
