from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import QApplication, QSplashScreen


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("Signal Explorer")
    app.setOrganizationName("Signal Explorer")
    assets = Path(__file__).parent / "assets"
    app.setWindowIcon(QIcon(str(assets / "signal-explorer.ico")))
    artwork = QPixmap(str(assets / "signal-explorer-splash.png"))
    splash = QSplashScreen(artwork.scaledToWidth(480, Qt.TransformationMode.SmoothTransformation))
    splash.setMask(splash.pixmap().mask())
    splash.show()
    app.processEvents()
    try:
        # Load protocol/UI dependencies after painting the splash, so it covers
        # the expensive part of startup as well as main-window construction.
        from signal_explorer.ui.main_window import MainWindow

        window = MainWindow()
        window.show()
        splash.finish(window)
    finally:
        splash.close()
    return app.exec()
