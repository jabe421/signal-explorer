from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QPushButton, QScrollArea

from signal_explorer.ui.main_window import MainWindow


def test_can_tool_layout_round_trip_and_reset():
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    window.show()
    try:
        sender = window._transmit_dock
        health = window._bus_health_dock
        assert sender.isHidden() and health.isHidden()
        window._view_menu.actions()[0].trigger()
        assert not sender.isHidden()
        assert window.dockWidgetArea(sender) == Qt.DockWidgetArea.BottomDockWidgetArea
        window._open_bus_health()
        assert window._bus_health_window.timer.isActive()
        close_button = next(
            button for button in window._bus_health_window.findChildren(QPushButton)
            if button.text() == 'Close'
        )
        close_button.click()
        assert health.isHidden()
        assert not window._bus_health_window.timer.isActive()
        window._open_bus_health()
        health.setFloating(True)
    finally:
        window.close()

    restored = MainWindow()
    restored.show()
    try:
        assert not restored._transmit_dock.isHidden()
        assert restored._bus_health_dock.isFloating()
        assert not restored._bus_health_dock.isHidden()
        restored._view_menu.actions()[-1].trigger()
        assert restored._transmit_dock.isHidden()
        assert restored._bus_health_dock.isHidden()
        assert not restored._bus_health_dock.isFloating()
        assert not restored._bus_health_window.timer.isActive()
        app.processEvents()
    finally:
        restored.close()


def test_visible_docks_allow_main_window_to_fit_smaller_screen():
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    window.show()
    try:
        window._open_transmit_window()
        window._open_bus_health()
        window._show_can_dock(window._signal_graph_dock)
        window.resize(1024, 768)
        app.processEvents()
        assert window.width() <= 1024
        assert window.height() <= 768
        assert isinstance(window.centralWidget(), QScrollArea)
        assert isinstance(window._transmit_dock.widget(), QScrollArea)
    finally:
        window.close()
