"""Optional Windows PEAK PLIN transport (PCAN-USB Pro/Pro FD, PLIN-USB).

Bindings follow PEAK PLIN-API 2025, default C structure alignment, stdcall.
No vendor DLLs or examples are redistributed. CAN continues to use PCAN-Basic.
"""
import ctypes as C
import os
import threading
import time

from signal_explorer.bus.discovery import ChannelOption
from signal_explorer.lin.model import LinFrame, checksum_model, lin_checksum, protected_id

U8, U16, U32, U64 = C.c_uint8, C.c_uint16, C.c_uint32, C.c_uint64


class Message(C.Structure):
    _fields_ = [('FrameId', U8), ('Length', U8), ('Direction', U8),
                ('ChecksumType', U8), ('Data', U8 * 8), ('Checksum', U8)]


class ReceiveMessage(C.Structure):
    _fields_ = [('Type', U8), ('FrameId', U8), ('Length', U8),
                ('Direction', U8), ('ChecksumType', U8), ('Data', U8 * 8),
                ('Checksum', U8), ('ErrorFlags', U32), ('TimeStamp', U64), ('hHw', U16)]


class FrameEntry(C.Structure):
    _fields_ = [('FrameId', U8), ('Length', U8), ('Direction', U8),
                ('ChecksumType', U8), ('Flags', U16), ('InitialData', U8 * 8)]


class PeakAPI:
    def __init__(self):
        if os.name != 'nt':
            raise RuntimeError('PEAK PLIN requires Windows.')
        try:
            self.dll = C.WinDLL(os.environ.get('SIGNAL_EXPLORER_PLIN_DLL', 'PLinApi.dll'))
        except OSError as exc:
            raise RuntimeError('Install PEAK LIN drivers, PLIN Manager and the matching '
                               '64-bit PLIN-API (PLinApi.dll). PCAN-Basic alone is for CAN.') from exc
        signatures = {
            'RegisterClient': [C.c_char_p, C.c_void_p, C.POINTER(U8)],
            'RemoveClient': [U8], 'ConnectClient': [U8, U16], 'DisconnectClient': [U8, U16],
            'GetAvailableHardware': [C.POINTER(U16), U16, C.POINTER(C.c_int32)],
            'GetHardwareParam': [U16, U16, C.c_void_p, U16],
            'InitializeHardware': [U8, U16, U8, U16],
            'ResetHardwareConfig': [U8, U16],
            'SetClientFilter': [U8, U16, U64],
            'SetFrameEntry': [U8, U16, C.POINTER(FrameEntry)],
            'Write': [U8, U16, C.POINTER(Message)],
            'Read': [U8, C.POINTER(ReceiveMessage)],
            'XmtWakeUp': [U8, U16], 'GetTargetTime': [U16, C.POINTER(U64)],
        }
        for name, args in signatures.items():
            function = getattr(self.dll, 'LIN_' + name)
            function.argtypes, function.restype = args, C.c_int32

    def call(self, name, *args):
        result = getattr(self.dll, 'LIN_' + name)(*args)
        if result:
            raise RuntimeError(f'PEAK LIN_{name} failed (PLIN error 0x{result:04X}).')

    def integer(self, hardware, parameter):
        value = C.c_int32()
        self.call('GetHardwareParam', hardware, parameter, C.byref(value), C.sizeof(value))
        return value.value

    def channels(self):
        handles = (U16 * 256)()
        count = C.c_int32()
        self.call('GetAvailableHardware', handles, C.sizeof(handles), C.byref(count))
        options = []
        for handle in handles[:count.value]:
            name = C.create_string_buffer(256)
            self.call('GetHardwareParam', handle, 1, name, C.sizeof(name))
            serial, channel = self.integer(handle, 11), self.integer(handle, 3)
            options.append(ChannelOption(
                f'{name.value.decode(errors="replace")} | serial {serial} | LIN {channel}',
                dict(hardware=handle, serial=serial, channel=channel)))
        return options


def discover_peak():
    try:
        channels = PeakAPI().channels()
        return channels, (f'{len(channels)} PEAK LIN channels detected.' if channels else
                          'PLIN API loaded; no LIN adapters detected. Connect a PEAK LIN interface and Refresh.')
    except Exception as exc:
        return [], str(exc)


class PeakLinTransport:
    """Owns one idle PLIN channel. All native calls, including replay, serialize."""
    def __init__(self, config, role, baudrate, lengths, models, callback, api=None):
        if role not in {'master', 'slave', 'monitor'}:
            raise ValueError('Unknown LIN role.')
        self.api = api or PeakAPI()
        self.hardware = int(config['hardware'])
        self.channel = int(config.get('channel', 1))
        self.role, self.lengths, self.models = role, dict(lengths), dict(models)
        self.callback, self.responses = callback, {}
        self._lock = threading.RLock()
        self.client = U8()
        self._connected = self._configured = self._closed = False
        try:
            # PLIN lets all clients reconfigure a channel. Refuse an occupied
            # channel rather than reset another application's response table.
            clients = (U8 * 256)()
            self.api.call('GetHardwareParam', self.hardware, 4, clients, C.sizeof(clients))
            if clients[0]:
                raise RuntimeError('This PEAK LIN channel is in use. Disconnect other PLIN applications first.')
            self.api.call('RegisterClient', b'Signal Explorer LIN', None, C.byref(self.client))
            self.api.call('ConnectClient', self.client, self.hardware)
            self._connected = True
            self.api.call('GetHardwareParam', self.hardware, 4, clients, C.sizeof(clients))
            if clients[0] != 1 or clients[1] != self.client.value:
                raise RuntimeError('Another PLIN client connected during setup. Close it and retry.')
            self.api.call('ResetHardwareConfig', self.client, self.hardware)
            self._configured = True
            self.api.call('InitializeHardware', self.client, self.hardware,
                          2 if role == 'master' else 1, baudrate)
            # Subscribe to every ID with no publisher enabled, including monitor.
            for identifier in range(64):
                self._entry(identifier)
            self.api.call('SetClientFilter', self.client, self.hardware, 0xFFFFFFFFFFFFFFFF)
            target = U64()
            self.api.call('GetTargetTime', self.hardware, C.byref(target))
            self._epoch = time.time() - target.value / 1_000_000
        except Exception:
            try:
                self.close()
            except Exception:
                pass  # Preserve the connection error; close attempts every release.
            raise

    def _entry(self, identifier, response=None):
        model = checksum_model(identifier, response.model if response else self.models.get(identifier, 'enhanced'))
        entry = FrameEntry(FrameId=identifier, Length=self.lengths.get(identifier, 8),
                           Direction=1 if response else (2 if identifier in self.lengths or identifier >= 60 else 3),
                           ChecksumType=(1 if model == 'classic' else 2) if response or identifier in self.lengths or identifier >= 60 else 3,
                           Flags=1 if response else 0)
        if response:
            entry.InitialData[:len(response.data)] = response.data
        self.api.call('SetFrameEntry', self.client, self.hardware, C.byref(entry))

    def _active(self):
        if self._closed:
            raise RuntimeError('PEAK LIN session is closed.')

    def set_response(self, identifier, response):
        with self._lock:
            self._active()
            if self.role == 'monitor':
                raise ValueError('Monitor sessions cannot publish responses.')
            if not 0 <= identifier <= 61 or len(response.data) != self.lengths.get(identifier):
                raise ValueError('Response ID/length does not match the LIN definition.')
            lin_checksum(identifier, response.data, response.model)
            if identifier in (60, 61) and len(response.data) != 8:
                raise ValueError('Diagnostic responses require eight bytes.')
            self._entry(identifier, response)
            self.responses[identifier] = response

    def remove_response(self, identifier):
        with self._lock:
            self._active()
            self._entry(identifier)  # Native subscriber: no stale automatic TX.
            self.responses.pop(identifier, None)

    def _write(self, identifier, response=None):
        model = checksum_model(identifier, response.model if response else self.models.get(identifier, 'enhanced'))
        msg = Message(FrameId=protected_id(identifier), Length=self.lengths.get(identifier, 8),
                      Direction=1 if response else 2, ChecksumType=1 if model == 'classic' else 2)
        if response:
            msg.Length = len(response.data)
            msg.Data[:len(response.data)] = response.data
            msg.Checksum = lin_checksum(identifier, response.data, model)
        self.api.call('Write', self.client, self.hardware, C.byref(msg))

    def header(self, identifier):
        with self._lock:
            self._active()
            if self.role != 'master':
                raise ValueError('Only the LIN master can send headers.')
            if not 0 <= identifier <= 61 or identifier not in self.lengths:
                raise ValueError('Define an unconditional frame ID 00-3D first.')
            # A publisher write emits header+local response; subscriber emits only
            # the header and lets an external slave provide its response.
            self._write(identifier, self.responses.get(identifier))

    def sleep(self):
        with self._lock:
            self._active()
            if self.role != 'master':
                raise ValueError('Only the master can send the sleep command.')
            from signal_explorer.lin.model import Response
            self._write(60, Response(b'\x00' + b'\xff' * 7, 'classic'))

    def wake(self):
        with self._lock:
            self._active()
            if self.role == 'monitor':
                raise ValueError('Monitor sessions cannot wake the bus.')
            self.api.call('XmtWakeUp', self.client, self.hardware)

    def poll(self):
        with self._lock:
            self._active()
            # Bounded batches keep the GUI responsive under receive load.
            for _ in range(256):
                msg = ReceiveMessage()
                result = self.api.dll.LIN_Read(self.client, C.byref(msg))
                if result == 3:  # errRcvQueueEmpty
                    break
                if result:
                    raise RuntimeError(f'PEAK LIN_Read failed (PLIN error 0x{result:04X}).')
                if msg.hHw != self.hardware:
                    continue
                self._receive(msg)

    def _receive(self, msg):
        timestamp = self._epoch + msg.TimeStamp / 1_000_000
        identifier = msg.FrameId & 63
        if msg.Type in (1, 2):
            self.callback(LinFrame(timestamp, 0, kind='sleep' if msg.Type == 1 else 'wake', channel=self.channel))
            return
        if msg.Type != 0:
            raise RuntimeError(f'PEAK LIN status event {msg.Type}; receive data may have been lost.')
        flags = msg.ErrorFlags
        if flags & 6: kind = 'parity_error'
        elif flags & 0x20: kind = 'checksum_error'
        elif flags & 0x18: kind = 'no_response'
        elif flags & ~0x200:  # OTHER_RESPONSE alone is a source indication.
            raise RuntimeError(f'PEAK LIN bus error flags 0x{flags:08X}.')
        else: kind = 'frame'
        if msg.Length > 8:
            raise RuntimeError('PEAK returned a LIN payload longer than eight bytes.')
        # PLIN Direction is publisher/subscriber, not raw RX/TX. OTHER_RESPONSE
        # means another node supplied data despite our publisher configuration.
        direction = 'tx' if self.role != 'monitor' and msg.Direction == 1 and not flags & 0x200 else 'rx'
        # PLIN reports whole transactions with a LIN-break timestamp. Rebuild
        # the header observation for role-aware MF4 playback, never on parity
        # errors. It is not a separate driver event or a second wire frame.
        if not flags & 6:
            self.callback(LinFrame(timestamp, identifier,
                                   direction='tx' if self.role == 'master' else 'rx',
                                   kind='header', pid=msg.FrameId, channel=self.channel))
        length = 0 if flags & 8 else min(msg.Length, msg.Checksum) if flags & 0x10 else msg.Length
        self.callback(LinFrame(timestamp, identifier, bytes(msg.Data[:length]),
                               direction=direction, kind=kind,
                               checksum=None if flags & 0x18 else msg.Checksum,
                               model='classic' if msg.ChecksumType == 1 else 'enhanced',
                               pid=msg.FrameId, channel=self.channel))

    def close(self):
        with self._lock:
            if self._closed:
                return
            self._closed = True
            failures = []
            calls = []
            if self._configured:
                calls.append(('ResetHardwareConfig', self.client, self.hardware))
            if self._connected:
                calls.append(('DisconnectClient', self.client, self.hardware))
            if self.client.value:
                calls.append(('RemoveClient', self.client))
            for call in calls:
                try:
                    self.api.call(*call)
                except Exception as exc:
                    failures.append(str(exc))
            if failures:
                raise RuntimeError('; '.join(failures))
