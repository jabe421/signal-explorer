"""ValueCAN4-2EL LIN via optional python-ics and installed icsneo40.dll.

The J1850 header/data packing follows Intrepid's transmit_lin_example.py.
Settings writes are temporary (never EEPROM). Native slave buffers handle wire
turnaround; the receive/UI thread does not generate responses to headers.
"""
from copy import deepcopy

from signal_explorer.bus.discovery import ChannelOption
from signal_explorer.bus.ownership import DeviceLease
from signal_explorer.bus.timestamps import HardwareClock
from signal_explorer.lin.model import LinFrame, Response, protected_id, checksum_model, lin_checksum
from signal_explorer.lin.native import NativeLinTransport


def serial_number(ics, device):
    number = device.SerialNumber
    return ics.base36enc(number) if int('0A0000', 36) < number < int('ZZZZZZ', 36) else str(number)


def is_valuecan_el(device):
    # NEODEVICE_VCAN42_EL is 10 in Intrepid's public device type definitions.
    return getattr(device, 'DeviceType', None) == 10


def discover_intrepid():
    try:
        import ics
        devices = ics.find_devices()
        options = [ChannelOption(f'{d.Name} | serial {serial_number(ics,d)} | LIN 1',
                                 dict(serial=serial_number(ics,d), channel=1, network=ics.NETID_LIN))
                   for d in devices if is_valuecan_el(d)]
        return options, (f'{len(options)} ValueCAN4-2EL LIN channels detected.' if options else
                         'No ValueCAN4-2EL found. ValueCAN4-2 and ValueCAN3 are not LIN adapters. Check USB and Intrepid drivers.')
    except ImportError:
        return [], 'Install the Intrepid optional package: pip install -e ".[intrepid]", plus Intrepid drivers and 64-bit icsneo40.dll.'
    except Exception as exc:
        return [], f'Intrepid LIN scan failed: {exc}'


class IntrepidLinTransport(NativeLinTransport):
    # The legacy python-ics binding has no documented dedicated LIN wake pulse.
    # Do not substitute a header/break and claim it is a conforming wake signal.
    supports_wake = False

    def __init__(self, config, role, baudrate, lengths, models, callback, api=None):
        super().__init__(role, lengths, models, callback)
        if api is None:
            import ics as api
        self.api, self.channel, self.network = api, 1, api.NETID_LIN
        self.device = self.original_settings = None
        self._clock = HardwareClock()
        self._settings_written = self._network_touched = False
        self._lease = DeviceLease('neovi', config.get('serial'), 'LIN')
        try:
            matches = [d for d in api.find_devices() if serial_number(api,d) == str(config['serial']) and is_valuecan_el(d)]
            if len(matches) != 1:
                raise RuntimeError('The selected ValueCAN4-2EL is no longer available. Refresh LIN devices.')
            device = matches[0]
            if device.NumberOfClients:
                raise RuntimeError('This Intrepid device is in use. Disconnect other CAN/LIN/Vehicle Spy sessions first.')
            # python-can uses the same open mutex for the neoVI server.
            from can.interfaces.ics_neovi.neovi_bus import open_lock
            with open_lock:
                api.open_device(device)
            self.device = device
            self.original_settings = deepcopy(api.get_device_settings(device))
            from ics.structures.e_device_settings_type import e_device_settings_type
            if self.original_settings.DeviceSettingType != e_device_settings_type.DeviceVCAN4SettingsType:
                raise RuntimeError('Unexpected ValueCAN4 settings layout; no device settings were changed.')
            settings = deepcopy(self.original_settings)
            settings.Settings.vcan4.lin1.Baudrate = baudrate
            # Intrepid LIN_SETTINGS enums: RESISTOR_ON=0, RESISTOR_OFF=1.
            settings.Settings.vcan4.lin1.MasterResistor = 0 if role == 'master' else 1
            settings.Settings.vcan4.network_enables |= 1 << 2  # CoreMini LIN1 network bit.
            api.enable_network_com(device, False, self.network)
            self._network_touched = True
            self._settings_written = True
            api.set_device_settings(device, settings, False)
            # Empty responder updates remove stale entries. INIT is clear, so
            # these configure the native table without emitting master headers.
            for identifier in range(62):
                self._transmit(identifier, None, master=False)
            api.enable_network_com(device, True, self.network)
        except Exception:
            try: self.close()
            except Exception: pass
            raise

    def _transmit(self, identifier, response, master):
        message = self.api.SpyMessageJ1850()
        message.Protocol, message.NetworkID = self.api.SPY_PROTOCOL_LIN, self.network
        message.StatusBitField = self.api.SPY_STATUS_INIT_MESSAGE if master else 0
        packed = bytes([protected_id(identifier)])
        if response is not None:
            packed += response.data + bytes([lin_checksum(identifier, response.data, response.model)])
        message.Header = tuple(packed[:3])
        message.Data = tuple(packed[3:])
        message.NumberBytesHeader, message.NumberBytesData = len(message.Header), len(message.Data)
        self.api.transmit_messages(self.device, message)

    def set_response(self, identifier, response):
        with self._lock:
            self.validate_response(identifier, response)
            if self.role == 'slave':
                self._transmit(identifier, response, master=False)
            self.responses[identifier] = response

    def remove_response(self, identifier):
        with self._lock:
            self.active()
            if self.role == 'slave':
                self._transmit(identifier, None, master=False)
            self.responses.pop(identifier, None)

    def header(self, identifier):
        with self._lock:
            self.validate_header(identifier)
            self._transmit(identifier, self.responses.get(identifier), master=True)

    def sleep(self):
        with self._lock:
            self.active('master')
            self._transmit(60, Response(b'\x00'+b'\xff'*7, 'classic'), master=True)

    def wake(self):
        raise NotImplementedError('Intrepid LIN wake pulses are not enabled through the legacy API. Use the external master to wake the bus.')

    def poll(self):
        with self._lock:
            self.active()
            messages, errors = self.api.get_messages(self.device, True, 0)
            if errors:
                raise RuntimeError(f'Intrepid reported {errors} receive/API errors; LIN capture stopped.')
            for message in messages:
                if message.Protocol == self.api.SPY_PROTOCOL_LIN and message.NetworkID == self.network:
                    self._receive(message)

    def _receive(self, message):
        header = bytes(message.Header[:message.NumberBytesHeader])
        if not header: raise RuntimeError('Intrepid returned LIN data without a protected ID.')
        identifier, pid = header[0] & 63, header[0]
        timestamp = self._clock.to_unix(self.api.get_timestamp_for_msg(self.device, message))
        raw = header[1:] + bytes(message.Data[:message.NumberBytesData])
        flags, flags2 = message.StatusBitField, message.StatusBitField2
        expected = self.lengths.get(identifier)
        checksum = None
        if raw:
            if expected is not None and len(raw) == expected:
                data, checksum = raw, message.AckBytes[0]
            else:
                data, checksum = raw[:-1], raw[-1]
        else: data = b''
        if len(data) > 8: raise RuntimeError('Intrepid returned a LIN payload longer than eight bytes.')
        model = self.model(identifier)
        if expected is None and data and checksum == lin_checksum(identifier,data,'classic'):
            model = 'classic'
        if flags2 & 0x04000000 or pid != protected_id(identifier): kind = 'parity_error'
        elif flags2 & 0x02000000: kind = 'collision'
        elif flags2 & 0xC0000000 or not data: kind = 'no_response'
        elif flags & self.api.SPY_STATUS_CRC_ERROR or checksum != lin_checksum(identifier,data,model): kind = 'checksum_error'
        elif flags & self.api.SPY_STATUS_GLOBAL_ERR or flags2 & 0x39E00000:
            raise RuntimeError(f'Intrepid LIN bus error (status 0x{flags:08X}/0x{flags2:08X}).')
        elif expected is not None and len(data) != expected:
            raise RuntimeError('Received LIN length differs from the current network definition.')
        else: kind = 'frame'
        if kind != 'parity_error':
            self.callback(LinFrame(timestamp,identifier,kind='header',pid=pid,channel=self.channel,
                                   direction='tx' if self.role=='master' and flags & self.api.SPY_STATUS_INIT_MESSAGE else 'rx'))
        direction = 'tx' if self.role!='monitor' and flags & self.api.SPY_STATUS_TX_MSG and identifier in self.responses else 'rx'
        self.callback(LinFrame(timestamp,identifier,data,kind=kind,direction=direction,
                               checksum=checksum,model=model,pid=pid,channel=self.channel))

    def close(self):
        with self._lock:
            if self._closed: return
            self._closed = True
            failures = []
            def attempt(callback):
                try: callback()
                except Exception as exc: failures.append(str(exc))
            try:
                if self.device:
                    if self._network_touched:
                        attempt(lambda:self.api.enable_network_com(self.device,False,self.network))
                        for identifier in range(62):
                            attempt(lambda i=identifier:self._transmit(i,None,master=False))
                    if self._settings_written:
                        attempt(lambda:self.api.set_device_settings(self.device,self.original_settings,False))
                    # Close with LIN communication off. Reopening a session
                    # explicitly enables it after configuration/buffer cleanup.
                    attempt(lambda:self.api.close_device(self.device))
            finally:
                self._lease.release()
            if failures: raise RuntimeError('; '.join(failures))
