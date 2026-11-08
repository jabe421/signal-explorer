from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import QApplication, QMessageBox, QSplashScreen


def main() -> int:
    from signal_explorer.diagnostics import configure_logging, install_exception_hooks
    configure_logging()
    install_exception_hooks()
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

        window = MainWindow(auto_discover=True)
        window.show()
        splash.finish(window)
    finally:
        splash.close()
    result = app.exec()
    if window.installer_on_exit is not None:
        from signal_explorer.updates import discard_installer, launch_installer

        try:
            launch_installer(window.installer_on_exit)
        except OSError as exc:
            discard_installer(window.installer_on_exit)
            QMessageBox.warning(None, "Signal Explorer update", str(exc))
    return result
