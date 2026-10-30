"""ValueCAN4-2EL raw Ethernet using python-ics and the installed neoVI DLL.

Only the Ethernet enable bit is changed, in RAM; PHY configuration is preserved.
The 16-bit ExtraDataPtr length and Ethernet's enabled=0 convention follow the
vendor binding. TX trace entries represent API acceptance, not wire confirmation.
"""
from copy import deepcopy
from threading import RLock

from signal_explorer.bus.ownership import DeviceLease
from signal_explorer.bus.timestamps import HardwareClock
from signal_explorer.lin.intrepid import is_valuecan_el, serial_number
from signal_explorer.ethernet.model import EthernetFrame


class IntrepidEthernetTransport:
    def __init__(self, config, callback, api=None):
        if api is None: import ics as api
        self.api, self.callback = api, callback
        self._lock = RLock(); self._lease = None; self._closed = False
        self.device = self.original_settings = None
        self._clock = HardwareClock()
        self._settings_written = self._network_touched = False
        self.rejected = 0
        self.driver_dropped = None  # Legacy get_messages exposes API errors, not a cumulative drop counter.
        self.local_mac = None  # Raw Ethernet sender uses the editor's source MAC.
        self.network = api.NETID_ETHERNET
        self._lease = DeviceLease('neovi', config['serial'], 'Ethernet')
        try:
            matches = [d for d in api.find_devices() if is_valuecan_el(d) and serial_number(api, d) == str(config['serial'])]
            if len(matches) != 1:
                raise RuntimeError('Selected ValueCAN4-2EL is no longer available. Refresh adapters.')
            device = matches[0]
            if device.NumberOfClients:
                raise RuntimeError('ValueCAN4-2EL is in use. Disconnect CAN/LIN/Vehicle Spy on this device first.')
            from can.interfaces.ics_neovi.neovi_bus import open_lock
            with open_lock: api.open_device(device)
            self.device = device
            self.original_settings = deepcopy(api.get_device_settings(device))
            from ics.structures.e_device_settings_type import e_device_settings_type
            if self.original_settings.DeviceSettingType != e_device_settings_type.DeviceVCAN4SettingsType:
                raise RuntimeError('Unexpected ValueCAN4 settings layout; no settings were changed.')
            settings = deepcopy(self.original_settings)
            settings.Settings.vcan4.network_enables_2 |= 1 << 13  # CoreMini ETHERNET, not NETID index.
            self._network_touched = True
            api.enable_network_com(device, False, self.network)
            self._settings_written = True  # Restore even if a partial settings write raises.
            api.set_device_settings(device, settings, False)
            api.enable_network_com(device, True, self.network)
        except Exception:
            try: self.close()
            except Exception: pass
            raise

    def send(self, frame):
        with self._lock:
            if self._closed: raise RuntimeError('Intrepid Ethernet session is closed.')
            message = self.api.SpyMessage()
            message.Protocol = self.api.SPY_PROTOCOL_ETHERNET
            message.NetworkID, message.NetworkID2 = self.network & 255, self.network >> 8
            # Assign Protocol before ExtraDataPtr: setter packs length into the
            # Header/Data length bytes. Ethernet must keep ExtraDataPtrEnabled=0.
            message.ExtraDataPtr = tuple(frame.raw)
            if (message.NumberBytesHeader << 8 | message.NumberBytesData) != len(frame.raw):
                raise RuntimeError('Installed python-ics cannot encode this Ethernet frame length. Update python-ics.')
            self.api.transmit_messages(self.device, message)

    def poll(self):
        with self._lock:
            if self._closed: raise RuntimeError('Intrepid Ethernet session is closed.')
            messages, errors = self.api.get_messages(self.device, False, 0)
            if errors: raise RuntimeError(f'Intrepid reported {errors} receive/API errors; Ethernet capture stopped.')
            for message in messages:
                network = message.NetworkID | message.NetworkID2 << 8
                if message.Protocol != self.api.SPY_PROTOCOL_ETHERNET or network != self.network: continue
                flags, flags2 = message.StatusBitField, message.StatusBitField2
                bad = flags & (self.api.SPY_STATUS_GLOBAL_ERR | self.api.SPY_STATUS_CRC_ERROR)
                bad |= flags2 & (self.api.SPY_STATUS2_ETHERNET_CRC_ERROR | self.api.SPY_STATUS2_ETHERNET_FRAME_TOO_SHORT)
                if bad:
                    self.rejected += 1
                    if flags & self.api.SPY_STATUS_TX_MSG:
                        raise RuntimeError(f'Intrepid Ethernet TX error (status 0x{flags:08X}/0x{flags2:08X}). Check 12 V, link and PHY settings.')
                    continue
                if flags & self.api.SPY_STATUS_TX_MSG: continue  # Service already recorded the accepted TX once.
                length = message.NumberBytesHeader << 8 | message.NumberBytesData
                raw = bytes(message.ExtraDataPtr or ())
                if len(raw) != length:
                    self.rejected += 1
                    continue
                if flags2 & self.api.SPY_STATUS2_ETHERNET_FCS_AVAILABLE:
                    raw = raw[:-4]
                try:
                    frame = EthernetFrame(self._clock.to_unix(self.api.get_timestamp_for_msg(self.device, message)), raw)
                except ValueError:
                    self.rejected += 1
                    continue
                self.callback(frame)

    def close(self):
        with self._lock:
            if self._closed: return
            self._closed = True; failures = []
            def attempt(callback):
                try: callback()
                except Exception as exc: failures.append(str(exc))
            try:
                if self.device is not None:
                    if self._network_touched:
                        attempt(lambda: self.api.enable_network_com(self.device, False, self.network))
                    if self._settings_written:
                        attempt(lambda: self.api.set_device_settings(self.device, self.original_settings, False))
                    attempt(lambda: self.api.close_device(self.device))
            finally:
                if self._lease: self._lease.release()
            if failures: raise RuntimeError('; '.join(failures))
