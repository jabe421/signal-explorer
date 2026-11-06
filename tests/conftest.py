"""Keep UI tests from reading or changing the user's saved adapter choices."""
import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QFileDialog, QMessageBox


@pytest.fixture(autouse=True)
def reject_unexpected_dialogs(monkeypatch):
    """Fail on unmocked modal dialogs instead of waiting for invisible input in CI.

    Record failures until teardown: Qt can swallow exceptions from signal slots.
    Tests expecting a dialog should patch that specific call themselves.
    """
    unexpected = []

    def handler(name, result):
        def reject(*args, **kwargs):
            unexpected.append(f"{name}: {args[1:3]!r} {kwargs!r}")
            return result
        return reject

    for name in ('warning', 'critical', 'information', 'question', 'about'):
        monkeypatch.setattr(QMessageBox, name, handler(name, QMessageBox.StandardButton.Cancel))
    for name in ('getSaveFileName', 'getOpenFileName'):
        monkeypatch.setattr(QFileDialog, name, handler(name, ('', '')))
    monkeypatch.setattr(QFileDialog, 'getOpenFileNames', handler('getOpenFileNames', ([], '')))
    monkeypatch.setattr(QFileDialog, 'getExistingDirectory', handler('getExistingDirectory', ''))
    yield
    if unexpected:
        pytest.fail('Unexpected modal dialog(s); explicitly mock expected dialogs:\n' + '\n'.join(unexpected))


@pytest.fixture(autouse=True)
def adapter_settings(tmp_path, monkeypatch):
    settings = QSettings(str(tmp_path / 'adapters.ini'), QSettings.Format.IniFormat)
    monkeypatch.setattr('signal_explorer.ui.adapter_preferences.QSettings', lambda: settings)
    return settings
