"""Small Windows Npcap binding. No install, adapter opening or TX on discovery.

Uses cdecl (CDLL) and the Win32 timeval ABI, including in a 64-bit process.
pcap_open's NOCAPTURE_LOCAL suppresses this handle's injected packet echoes.
"""
import ctypes as C
import os
from pathlib import Path
from threading import RLock
import time

from signal_explorer.bus.ownership import DeviceLease
from signal_explorer.ethernet.model import EthernetFrame, mac_bytes


class PcapIf(C.Structure):
    pass


PcapIf._fields_ = [('next', C.POINTER(PcapIf)), ('name', C.c_char_p),
                  ('description', C.c_char_p), ('addresses', C.c_void_p), ('flags', C.c_uint32)]


class PacketHeader(C.Structure):
    _fields_ = [('seconds', C.c_int32), ('microseconds', C.c_int32),
                ('caplen', C.c_uint32), ('length', C.c_uint32)]


class PcapStats(C.Structure):
    _fields_ = [('received', C.c_uint32), ('dropped', C.c_uint32),
                ('interface_dropped', C.c_uint32), ('captured', C.c_uint32)]


class NpcapAPI:
    def __init__(self):
        if os.name != 'nt':
            raise RuntimeError('PC Ethernet capture currently requires Windows and Npcap.')
        folder = Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32' / 'Npcap'
        path = folder / 'wpcap.dll'
        if not path.is_file():
            raise RuntimeError('Npcap is not installed. Install Npcap from npcap.com, then Refresh adapters.')
        # Load only the installed Npcap path, never a DLL in the current folder.
        with os.add_dll_directory(str(folder)):
            self.dll = C.CDLL(str(path))
        definitions = {
            'pcap_findalldevs': ([C.POINTER(C.POINTER(PcapIf)), C.c_char_p], C.c_int),
            'pcap_freealldevs': ([C.POINTER(PcapIf)], None),
            'pcap_open': ([C.c_char_p, C.c_int, C.c_int, C.c_int, C.c_void_p, C.c_char_p], C.c_void_p),
            'pcap_setnonblock': ([C.c_void_p, C.c_int, C.c_char_p], C.c_int),
            'pcap_datalink': ([C.c_void_p], C.c_int),
            'pcap_next_ex': ([C.c_void_p, C.POINTER(C.POINTER(PacketHeader)), C.POINTER(C.POINTER(C.c_ubyte))], C.c_int),
            'pcap_sendpacket': ([C.c_void_p, C.c_void_p, C.c_int], C.c_int),
            'pcap_geterr': ([C.c_void_p], C.c_char_p),
            'pcap_stats': ([C.c_void_p, C.POINTER(PcapStats)], C.c_int),
            'pcap_close': ([C.c_void_p], None),
        }
        for name, (args, result) in definitions.items():
            function = getattr(self.dll, name)
            function.argtypes, function.restype = args, result

    def devices(self):
        head = C.POINTER(PcapIf)(); error = C.create_string_buffer(256)
        if self.dll.pcap_findalldevs(C.byref(head), error):
            raise RuntimeError(error.value.decode(errors='replace'))
        result = []
        try:
            current = head
            while current:
                entry = current.contents
                result.append(dict(name=entry.name.decode(errors='replace'),
                                   description=(entry.description or b'').decode(errors='replace'), flags=entry.flags))
                current = entry.next
        finally:
            if head: self.dll.pcap_freealldevs(head)
        return result

    def open(self, name, promiscuous=False):
        error = C.create_string_buffer(256)
        flags = 8 | 16 | int(promiscuous)  # NOCAPTURE_LOCAL, MAX_RESPONSIVENESS, optional PROMISCUOUS.
        handle = self.dll.pcap_open(name.encode(), 65535, flags, 50, None, error)
        if not handle:
            raise RuntimeError('Npcap open failed: ' + error.value.decode(errors='replace') +
                               ' Check cable/adapter, Npcap driver and capture permissions (administrator-only install may require elevation).')
        try:
            if self.dll.pcap_datalink(handle) != 1:  # DLT_EN10MB, not radiotap, loopback, cooked capture, etc.
                raise RuntimeError('This capture interface does not provide Ethernet frames (DLT_EN10MB).')
            if self.dll.pcap_setnonblock(handle, 1, error):
                raise RuntimeError(error.value.decode(errors='replace'))
            return handle
        except Exception:
            self.close(handle)
            raise

    def receive(self, handle):
        header = C.POINTER(PacketHeader)(); data = C.POINTER(C.c_ubyte)()
        result = self.dll.pcap_next_ex(handle, C.byref(header), C.byref(data))
        if result == 0: return None
        if result < 0: raise RuntimeError(self.error(handle) or 'Npcap capture stopped.')
        h = header.contents
        if h.caplen > 65535 or h.caplen > h.length:
            raise RuntimeError('Npcap returned an invalid capture length.')
        return h.seconds + h.microseconds / 1e6, C.string_at(data, h.caplen), h.length

    def send(self, handle, raw):
        buffer = C.create_string_buffer(raw)
        if self.dll.pcap_sendpacket(handle, buffer, len(raw)):
            raise RuntimeError('Npcap transmit failed: ' + self.error(handle))

    def error(self, handle):
        return (self.dll.pcap_geterr(handle) or b'').decode(errors='replace')

    def stats(self, handle):
        stats = PcapStats()
        if self.dll.pcap_stats(handle, C.byref(stats)):
            raise RuntimeError('Npcap statistics failed: ' + self.error(handle))
        return dict(driver_received=stats.received, driver_dropped=stats.dropped,
                    interface_dropped=stats.interface_dropped)

    def close(self, handle):
        self.dll.pcap_close(handle)


class PcapTransport:
    """Bounded, nonblocking polls. Direction is inferred from the NIC source MAC."""
    def __init__(self, config, callback, api=None):
        self.api = api if api is not None else NpcapAPI()
        self.callback, self.handle = callback, None
        self._lock = RLock(); self._lease = None
        self.rejected = 0; self.driver_dropped = 0; self._last_stats = 0
        self.config = dict(config)
        self.source = mac_bytes(config['mac'])
        self.local_mac = config['mac']
        self._lease = DeviceLease('pcap', config['name'], 'Ethernet')
        try:
            if not any(d['name'] == config['name'] for d in self.api.devices()):
                raise RuntimeError('Selected PC Ethernet adapter is no longer available. Refresh adapters.')
            self.handle = self.api.open(config['name'], config.get('promiscuous', False))
        except Exception:
            self.close()
            raise

    def poll(self):
        with self._lock:
            if self.handle is None: raise RuntimeError('PC Ethernet session is closed.')
            for _ in range(128):
                packet = self.api.receive(self.handle)
                if packet is None: break
                timestamp, raw, wire_length = packet
                if len(raw) != wire_length:
                    self.rejected += 1
                    continue
                try:
                    frame = EthernetFrame(timestamp, raw, 'tx' if raw[6:12] == self.source else 'rx')
                except ValueError:
                    self.rejected += 1
                    continue
                self.callback(frame)
            if time.monotonic() - self._last_stats >= 1:
                stats = self.api.stats(self.handle)
                self.driver_dropped = stats['driver_dropped'] + stats['interface_dropped']
                self._last_stats = time.monotonic()

    def send(self, frame):
        with self._lock:
            if self.handle is None: raise RuntimeError('PC Ethernet session is closed.')
            self.api.send(self.handle, frame.raw)

    def close(self):
        with self._lock:
            handle, self.handle = self.handle, None
            try:
                if handle is not None: self.api.close(handle)
            finally:
                if self._lease: self._lease.release()
