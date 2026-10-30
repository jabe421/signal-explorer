"""Vector XL LIN transport, including VN1640 channels fitted with LIN piggies."""
import ctypes as C
from ctypes.util import find_library
import time

from signal_explorer.bus.discovery import prepare_vector_library, ChannelOption
from signal_explorer.bus.ownership import DeviceLease
from signal_explorer.lin.model import LinFrame, checksum_model, lin_checksum
from signal_explorer.lin.native import NativeLinTransport

U8, U16, U32, U64, I32 = C.c_uint8, C.c_uint16, C.c_uint32, C.c_uint64, C.c_int32


class Parameters(C.Structure):
    _pack_ = 1
    _fields_ = [('LINMode', U32), ('baudrate', I32), ('LINVersion', U32), ('reserved', U32)]


class LinMessage(C.Structure):
    _pack_ = 1
    _fields_ = [('id', U8), ('dlc', U8), ('flags', U16), ('data', U8 * 8), ('crc', U8)]


class TagData(C.Union):
    _pack_ = 1
    _fields_ = [('raw', U8 * 32), ('lin', LinMessage)]


class Event(C.Structure):
    _pack_ = 1
    _fields_ = [('tag', U8), ('chanIndex', U8), ('transId', U16), ('portHandle', U16),
                ('flags', U8), ('reserved', U8), ('timeStamp', U64), ('tagData', TagData)]


class VectorLinAPI:
    def __init__(self):
        prepare_vector_library()
        filename = 'vxlapi64' if C.sizeof(C.c_void_p) == 8 else 'vxlapi'
        path = find_library(filename)
        if not path:
            raise RuntimeError('Install Vector hardware drivers and the XL Driver Library. '
                               'Select Vector DLL in the CAN tab if needed, then restart.')
        self.dll = C.WinDLL(path)
        from can.interfaces.vector.xlclass import XLdriverConfig
        self.Config = XLdriverConfig
        signatures = {
            'xlOpenDriver': [], 'xlCloseDriver': [], 'xlGetDriverConfig': [C.POINTER(XLdriverConfig)],
            'xlOpenPort': [C.POINTER(I32), C.c_char_p, U64, C.POINTER(U64), U32, U32, U32],
            'xlClosePort': [I32], 'xlActivateChannel': [I32, U64, U32, U32],
            'xlDeactivateChannel': [I32, U64], 'xlGetSyncTime': [I32, C.POINTER(U64)],
            'xlReceive': [I32, C.POINTER(U32), C.POINTER(Event)],
            'xlLinSetChannelParams': [I32, U64, Parameters],
            'xlLinSetDLC': [I32, U64, C.POINTER(U8)],
            'xlLinSetChecksum': [I32, U64, C.POINTER(U8)],
            'xlLinSetSlave': [I32, U64, U8, C.POINTER(U8), U8, U16],
            'xlLinSwitchSlave': [I32, U64, U8, U8],
            'xlLinSendRequest': [I32, U64, U8, U32],
            'xlLinSetSleepMode': [I32, U64, U32, U8], 'xlLinWakeUp': [I32, U64],
        }
        # Independent bindings: never alter python-can's shared xlReceive ABI.
        self.functions = {name: C.WINFUNCTYPE(C.c_int16, *args)((name, self.dll))
                          for name, args in signatures.items()}

    def call(self, name, *args):
        result = self.functions[name](*args)
        if result:
            raise RuntimeError(f'Vector {name} failed (XL status {result}).')

    def channels(self):
        self.call('xlOpenDriver')
        try:
            config = self.Config()
            self.call('xlGetDriverConfig', C.byref(config))
            return [ChannelOption(
                f'{ch.name.decode(errors="replace")} | serial {ch.serialNumber} | LIN channel {ch.hwChannel + 1} | {ch.transceiverName.decode(errors="replace")}',
                dict(channel=int(ch.hwChannel)+1, channel_index=int(ch.channelIndex),
                     mask=int(ch.channelMask), serial=int(ch.serialNumber)))
                for ch in config.channel[:config.channelCount]
                if ch.channelBusCapabilities & (2 << 16)]
        finally:
            self.call('xlCloseDriver')


def discover_vector():
    try:
        channels = VectorLinAPI().channels()
        return channels, (f'{len(channels)} Vector LIN channels detected.' if channels else
                          'No active LIN-capable Vector channels found. Check VN1640 LIN piggies and Vector Hardware Config.')
    except Exception as exc:
        return [], str(exc)


class VectorLinTransport(NativeLinTransport):
    def __init__(self, config, role, baudrate, lengths, models, callback, api=None):
        super().__init__(role, lengths, models, callback)
        self.api = api or VectorLinAPI()
        self.channel, self.index, self.mask = config['channel'], config['channel_index'], config['mask']
        self.port = I32(-1)
        self._driver = self._activated = self._permission = False
        self._lease = DeviceLease('vector', config.get('serial'), 'LIN')
        try:
            self.api.call('xlOpenDriver'); self._driver = True
            permission = U64(self.mask)
            self.api.call('xlOpenPort', C.byref(self.port), b'Signal Explorer LIN', self.mask,
                          C.byref(permission), 4096, 3, 2)
            if permission.value & self.mask != self.mask:
                raise RuntimeError('Vector LIN initialization access denied. Release this channel in CANalyzer or other XL applications.')
            self._permission = True
            self.api.call('xlLinSetChannelParams', self.port, self.mask,
                          Parameters(1 if role == 'master' else 2, baudrate, 3, 0))
            dlc = (U8 * 64)(*[lengths.get(i, 8 if i in (60, 61) else 0xff) for i in range(64)])
            crc = (U8 * 60)(*[0 if self.model(i) == 'classic' else 1 if i in lengths else 0xff for i in range(60)])
            self.api.call('xlLinSetDLC', self.port, self.mask, dlc)
            self.api.call('xlLinSetChecksum', self.port, self.mask, crc)
            for identifier in range(62):
                self.api.call('xlLinSwitchSlave', self.port, self.mask, identifier, 0)
            self.api.call('xlActivateChannel', self.port, self.mask, 2, 0)  # Do not reset shared clocks.
            self._activated = True
            clock = U64()
            self.api.call('xlGetSyncTime', self.port, C.byref(clock))
            self._epoch = time.time() - clock.value / 1e9
        except Exception:
            try: self.close()
            except Exception: pass
            raise

    def set_response(self, identifier, response):
        with self._lock:
            self.validate_response(identifier, response)
            data = (U8 * 8)(*response.data)
            model = checksum_model(identifier, response.model)
            self.api.call('xlLinSetSlave', self.port, self.mask, identifier, data, len(response.data),
                          0x100 if model == 'classic' else 0x200)
            self.api.call('xlLinSwitchSlave', self.port, self.mask, identifier, 0xff)
            self.responses[identifier] = response

    def remove_response(self, identifier):
        with self._lock:
            self.active()
            self.api.call('xlLinSwitchSlave', self.port, self.mask, identifier, 0)
            self.responses.pop(identifier, None)

    def header(self, identifier):
        with self._lock:
            self.validate_header(identifier)
            self.api.call('xlLinSendRequest', self.port, self.mask, identifier, 0)

    def sleep(self):
        with self._lock:
            self.active('master')
            self.api.call('xlLinSetSleepMode', self.port, self.mask, 0, 0)

    def wake(self):
        with self._lock:
            self.active()
            if self.role == 'monitor': raise ValueError('Monitor sessions cannot wake the bus.')
            self.api.call('xlLinWakeUp', self.port, self.mask)

    def poll(self):
        with self._lock:
            self.active()
            for _ in range(256):
                event, count = Event(), U32(1)
                result = self.api.functions['xlReceive'](self.port, C.byref(count), C.byref(event))
                if result == 10: break  # XL_ERR_QUEUE_IS_EMPTY
                if result: raise RuntimeError(f'Vector xlReceive failed (XL status {result}).')
                if count.value: self._receive(event)

    def _receive(self, event):
        if event.flags & 1: raise RuntimeError('Vector LIN receive queue overrun; data was lost.')
        if event.chanIndex != self.index: return
        timestamp, raw = self._epoch + event.timeStamp / 1e9, event.tagData.raw
        if event.tag in (24, 25):
            if event.tag == 25 and raw[0] == 0: return
            self.callback(LinFrame(timestamp, 0, kind='wake' if event.tag == 24 or raw[0] == 2 else 'sleep', channel=self.channel))
            return
        if event.tag in (21, 22):
            raise RuntimeError(f'Vector LIN error event {event.tag}; check bus power, wiring, baud rate and parity.')
        if event.tag not in (20, 23): return
        identifier = raw[0] & 63
        self.callback(LinFrame(timestamp, identifier, kind='header',
                               direction='tx' if self.role == 'master' else 'rx', channel=self.channel))
        if event.tag == 23:
            self.callback(LinFrame(timestamp, identifier, kind='no_response', channel=self.channel))
            return
        msg = event.tagData.lin
        if msg.dlc > 8: raise RuntimeError('Vector returned an invalid LIN length.')
        data, model = bytes(msg.data[:msg.dlc]), self.model(identifier)
        if identifier not in self.models and data and msg.crc == lin_checksum(identifier,data,'classic'):
            model = 'classic'
        kind = 'no_response' if not data else 'checksum_error' if msg.flags & 0x81 or msg.crc != lin_checksum(identifier,data,model) else 'frame'
        direction = 'tx' if msg.flags & 0x40 and self.role != 'monitor' and identifier in self.responses else 'rx'
        self.callback(LinFrame(timestamp, identifier, data, direction=direction,
                               kind=kind, checksum=msg.crc if data else None, model=model, channel=self.channel))

    def close(self):
        with self._lock:
            if self._closed: return
            self._closed = True
            failures = []
            calls = []
            if self._permission:
                calls += [('xlLinSwitchSlave', self.port, self.mask, i, 0) for i in self.responses]
            if self._activated: calls.append(('xlDeactivateChannel', self.port, self.mask))
            if self.port.value != -1: calls.append(('xlClosePort', self.port))
            if self._driver: calls.append(('xlCloseDriver',))
            try:
                for call in calls:
                    try: self.api.call(*call)
                    except Exception as exc: failures.append(str(exc))
            finally:
                self._lease.release()
            if failures: raise RuntimeError('; '.join(failures))
