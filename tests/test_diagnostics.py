"""Support reports must stay useful when drivers, logging or connections fail."""

import os

os.environ["QT_QPA_PLATFORM"] = "offscreen"

from collections import deque
import json
import logging
from pathlib import Path
import re
import sys
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import can
import pytest
from PySide6.QtWidgets import QApplication, QPushButton

from signal_explorer import diagnostics
from signal_explorer.bus.discovery import ChannelOption
from signal_explorer.bus.multichannel import open_channels
from signal_explorer.ui.main_window import MainWindow

app = QApplication.instance() or QApplication([])


@pytest.fixture
def isolated_logging(monkeypatch, tmp_path):
    logger = diagnostics.LOGGER
    previous_handlers = logger.handlers[:]
    previous_level = logger.level
    logger.handlers = []
    monkeypatch.setattr(diagnostics, "_configured", False)
    monkeypatch.setattr(diagnostics, "_log_path", None)
    monkeypatch.setattr(diagnostics, "_recent", deque(maxlen=50))
    try:
        yield tmp_path
    finally:
        for handler in logger.handlers:
            handler.close()
        logger.handlers = previous_handlers
        logger.setLevel(previous_level)


def test_rotation_recent_errors_and_idempotent_configuration(isolated_logging):
    path = diagnostics.configure_logging(isolated_logging)
    assert diagnostics.configure_logging(isolated_logging / "other") == path
    handler = next(
        h
        for h in diagnostics.LOGGER.handlers
        if isinstance(h, logging.handlers.RotatingFileHandler)
    )
    assert handler.maxBytes == 2_000_000 and handler.backupCount == 3
    assert len(diagnostics.LOGGER.handlers) == 2
    handler.maxBytes = 300
    logger = logging.getLogger("signal_explorer.test")
    for number in range(70):
        logger.warning("Support test error %s", number)
    report = json.loads(diagnostics.diagnostics_report())
    assert len(report["recent_errors"]) == 50
    assert "Support test error 69" in report["recent_errors"][-1]
    assert len(list(isolated_logging.glob("signal-explorer.log*"))) == 4
    assert "Support test error 69" in path.read_text(encoding="utf-8")


def test_log_directory_failure_uses_temp_fallback(isolated_logging, monkeypatch):
    blocked = isolated_logging / "not-a-directory"
    blocked.write_text("file", encoding="utf-8")
    monkeypatch.setattr(
        diagnostics.tempfile, "gettempdir", lambda: str(isolated_logging)
    )
    path = diagnostics.configure_logging(blocked)
    assert path == isolated_logging / "signal-explorer-logs" / "signal-explorer.log"
    assert any("Cannot create diagnostic log" in line for line in diagnostics._recent)


def test_logging_unavailable_is_nonfatal_and_does_not_duplicate_handlers(
    isolated_logging, monkeypatch
):
    def denied(*args, **kwargs):
        raise PermissionError("test log filesystem unavailable")

    monkeypatch.setattr(Path, "mkdir", denied)
    assert diagnostics.configure_logging(isolated_logging) is None
    assert diagnostics.configure_logging(isolated_logging) is None
    assert len(diagnostics.LOGGER.handlers) == 1
    assert len(diagnostics._recent) == 2


def test_unhandled_exception_hooks_log_and_preserve_previous_hooks(
    isolated_logging, monkeypatch
):
    path = diagnostics.configure_logging(isolated_logging)
    previous, previous_thread = Mock(), Mock()
    monkeypatch.setattr(sys, "excepthook", previous)
    monkeypatch.setattr(threading, "excepthook", previous_thread)
    monkeypatch.setattr(diagnostics, "_hooks_installed", False)
    diagnostics.install_exception_hooks()
    installed = sys.excepthook
    diagnostics.install_exception_hooks()
    assert sys.excepthook is installed
    try:
        raise RuntimeError("diagnostic exception example")
    except RuntimeError as exc:
        sys.excepthook(type(exc), exc, exc.__traceback__)
        threading.excepthook(
            SimpleNamespace(
                exc_type=type(exc),
                exc_value=exc,
                exc_traceback=exc.__traceback__,
                thread=threading.current_thread(),
            )
        )
    previous.assert_called_once()
    previous_thread.assert_called_once()
    text = path.read_text(encoding="utf-8")
    assert "Unhandled exception" in text and "Unhandled worker exception" in text
    assert "RuntimeError: diagnostic exception example" in text


def test_report_missing_packages_and_installed_build_identity(tmp_path, monkeypatch):
    def missing(name):
        raise diagnostics.metadata.PackageNotFoundError(name)

    monkeypatch.setattr(diagnostics.metadata, "version", missing)
    monkeypatch.setattr(diagnostics, "loaded_driver_libraries", lambda: {})
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "SignalExplorer.exe"))
    (tmp_path / "build-info.json").write_text(
        json.dumps(dict(version="0.2.0-beta.1", commit="abc123")), encoding="utf-8"
    )
    report = json.loads(
        diagnostics.diagnostics_report(
            {"detected": ChannelOption("CAN1", {"channel": 0})}
        )
    )
    assert report["versions"]["python-can"] == "not installed"
    assert report["build"]["version"] == "0.2.0-beta.1"
    assert report["build"]["commit"] == "abc123"
    assert report["context"]["detected"]["config"]["channel"] == 0
    (tmp_path / "build-info.json").write_text("bad JSON", encoding="utf-8")
    assert "unavailable" in json.loads(diagnostics.diagnostics_report())["build"]


@pytest.mark.skipif(sys.platform != "win32", reason="Windows file-version resource API")
def test_windows_file_version_is_read_without_loading_vendor_driver():
    path = Path(os.environ["WINDIR"]) / "System32" / "kernel32.dll"
    assert re.fullmatch(
        r"\d+\.\d+\.\d+\.\d+", diagnostics._windows_file_version(str(path))
    )
    assert (
        diagnostics._windows_file_version(
            str(path.with_name("missing-signal-explorer-test.dll"))
        )
        == "unavailable"
    )


def test_partial_connect_cleanup_logs_secondary_failure_preserving_original(caplog):
    class Bus:
        def shutdown(self):
            raise RuntimeError("secondary shutdown failed")

    calls = []

    def factory(**kwargs):
        calls.append(kwargs)
        if len(calls) > 1:
            raise can.CanInitializationError("original open failed")
        return Bus()

    with pytest.raises(can.CanInitializationError, match="original open failed"):
        open_channels(factory, "virtual", [{"channel": "one"}, {"channel": "two"}], {})
    assert "Failed to release a partially opened CAN channel" in caplog.text
    assert "secondary shutdown failed" in caplog.text


def test_help_window_report_and_copy_do_not_open_or_scan_hardware(monkeypatch):
    window = MainWindow()
    scan = Mock(side_effect=AssertionError("Diagnostics must not scan hardware"))
    monkeypatch.setattr("signal_explorer.ui.main_window.discover_channels", scan)
    monkeypatch.setattr(window._service, "connect_bus", scan)
    monkeypatch.setattr(window._service, "connect_async", scan)
    try:
        window._channel_options = [
            ChannelOption("Test CAN1", {"channel": 0, "supports_fd": True})
        ]
        help_menu = next(
            action.menu()
            for action in window.menuBar().actions()
            if action.text() == "Help"
        )
        help_menu.actions()[0].trigger()
        dialog = window._diagnostics_window
        assert dialog.isVisible()
        report = json.loads(dialog.text.toPlainText())
        assert report["context"]["detected_can_channels"][0]["label"] == "Test CAN1"
        assert not report["context"]["can_connected"]
        previous_clipboard = app.clipboard().text()
        try:
            copy = next(
                button
                for button in dialog.findChildren(QPushButton)
                if button.text() == "Copy diagnostics"
            )
            copy.click()
            assert (
                json.loads(app.clipboard().text())["context"]["selected_can_backend"]
                == "virtual"
            )
        finally:
            app.clipboard().setText(previous_clipboard)
        scan.assert_not_called()
        dialog.close()
    finally:
        window.close()
