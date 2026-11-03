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


def can_device_key(config, fallback=''):
    for field in ('serial', 'device_serial', 'device_name', 'device_model'):
        if config.get(field) is not None:
            return str(config[field])
    return fallback


def can_channel_key(config):
    """Prefer physical positions over indices assigned by the driver on USB scan."""
    if config.get('physical_port') is not None:
        return 'port:' + str(config['physical_port'])
    if config.get('device_serial') is not None and config.get('channel_label'):
        return 'label:' + config['channel_label']
    return 'channel:' + str(config['channel'])


class CanChannelPreferences:
    def __init__(self, interface):
        self.key = f'adapters/can/{interface}/channel_selection'

    def _read(self):
        try:
            value = json.loads(QSettings().value(self.key, '{}', type=str))
            if isinstance(value, dict) and isinstance(value.get('devices', {}), dict):
                return value
        except (ValueError, TypeError):
            pass
        return {}

    def last_device(self):
        return self._read().get('last_device')

    def channels(self, device):
        selected = self._read().get('devices', {}).get(str(device))
        return selected if isinstance(selected, list) and selected and all(isinstance(c, str) for c in selected) else None

    def save(self, device, configs):
        if not configs:
            return
        value = self._read()
        value['last_device'] = str(device)
        value.setdefault('devices', {})[str(device)] = [can_channel_key(c) for c in configs]
        settings = QSettings()
        settings.setValue(self.key, json.dumps(value))
        settings.sync()

    def remember_device(self, device):
        value = self._read()
        value['last_device'] = str(device)
        settings = QSettings()
        settings.setValue(self.key, json.dumps(value))
        settings.sync()
