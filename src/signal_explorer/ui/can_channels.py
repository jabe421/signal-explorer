"""Explicit channel selection for one discovered adapter."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QLabel, QComboBox,
                              QListWidget, QListWidgetItem, QDialogButtonBox, QMessageBox)
from signal_explorer.ui.adapter_preferences import CanChannelPreferences, can_channel_key, can_device_key


def device_key(option):
    cfg = option.config
    return can_device_key(cfg, option.label.split(' | ')[0])


def channel_label(interface, cfg):
    if 'channel_label' in cfg:
        return cfg['channel_label']
    return f"CAN{int(cfg['channel']) + 1}" if interface == 'vector' else str(cfg['channel'])


class CanChannelsDialog(QDialog):
    def __init__(self, options, interface, current=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Enable CAN channels')
        self.resize(800, 420)
        self.interface, self.current = interface, current or []
        self.preferences = CanChannelPreferences(interface)
        self._current_device = None
        self.selected_configs = []
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel('Device:'))
        self.device = QComboBox()
        self.groups = {}
        for option in options:
            self.groups.setdefault(device_key(option), []).append(option)
        for key, items in self.groups.items():
            cfg = items[0].config
            name = cfg.get('device_name', cfg.get('device_model', items[0].label.split(' | ')[0]))
            self.device.addItem(f'{name} | {key}', key)
        if current:
            match = next((o for o in options if str(o.config['channel']) == str(current[0]['channel'])
                          and device_key(o) == can_device_key(current[0], o.label.split(' | ')[0])), None)
            if match:
                self._current_device = device_key(match)
                self.device.setCurrentIndex(self.device.findData(device_key(match)))
        elif self.device.findData(self.preferences.last_device()) >= 0:
            self.device.setCurrentIndex(self.device.findData(self.preferences.last_device()))
        layout.addWidget(self.device)
        self.inventory = QLabel(); self.inventory.setWordWrap(True); layout.addWidget(self.inventory)
        self.channels = QListWidget(); layout.addWidget(self.channels)
        layout.addWidget(QLabel('Only checked CAN channels will open. Bitrate and CAN FD settings apply to all checked channels.'))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept); buttons.rejected.connect(self.reject); layout.addWidget(buttons)
        self.device.currentIndexChanged.connect(self.populate)
        self.populate()

    def populate(self):
        self.channels.clear()
        options = self.groups[self.device.currentData()]
        selected = ([can_channel_key(c) for c in self.current] if self._current_device == self.device.currentData()
                    else self.preferences.channels(self.device.currentData()))
        lin_count = options[0].config.get('lin_channel_count')
        self.inventory.setText(f'{len(options)} CAN channels available' +
                               (f"; LIN inventory: {', '.join(f'LIN{i}' for i in range(1, lin_count + 1))}. "
                                'This dialog enables CAN; LIN transport support is configured separately.' if lin_count else
                                '. Vector choices reflect driver-reported active CAN transceivers/piggies.'))
        for index, option in enumerate(options):
            cfg = dict(option.config, channel_label=channel_label(self.interface, option.config))
            item = QListWidgetItem(cfg['channel_label'] + ' | ' + option.label)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            checked = can_channel_key(option.config) in selected if selected is not None else index == 0
            item.setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
            item.setData(Qt.ItemDataRole.UserRole, cfg)
            self.channels.addItem(item)

    def accept(self):
        selected = [self.channels.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.channels.count())
                    if self.channels.item(i).checkState() == Qt.CheckState.Checked]
        if not selected:
            QMessageBox.warning(self, 'CAN channels', 'Enable at least one CAN channel.')
            return
        self.selected_configs = selected
        super().accept()
