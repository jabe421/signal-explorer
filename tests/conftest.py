"""Keep UI tests from reading or changing the user's saved adapter choices."""
import pytest
from PySide6.QtCore import QSettings


@pytest.fixture(autouse=True)
def adapter_settings(tmp_path, monkeypatch):
    settings = QSettings(str(tmp_path / 'adapters.ini'), QSettings.Format.IniFormat)
    monkeypatch.setattr('signal_explorer.ui.adapter_preferences.QSettings', lambda: settings)
    return settings
