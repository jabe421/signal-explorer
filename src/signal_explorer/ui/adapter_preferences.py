"""Per-protocol selection preferences, separate from connection state."""
import json
from PySide6.QtCore import QSettings


class AdapterPreference:
    def __init__(self, protocol):
        self.key = f"adapters/{protocol}/last_selected"

    def load(self, default="virtual"):
        return QSettings().value(self.key, default, type=str)

    def save(self, value):
        settings = QSettings()
        settings.setValue(self.key, value)
        settings.sync()


def ethernet_key(adapter):
    """Keep the actual NIC or device identity, rather than its display index."""
    return json.dumps([str(value) for value in adapter.identity])
