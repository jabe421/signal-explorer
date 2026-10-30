"""Read-only Ethernet adapter enumeration; always keeps the virtual adapter."""
from dataclasses import dataclass, field
import ctypes as C
import os

from signal_explorer.ethernet.model import LOCAL_MAC, mac_text


@dataclass(frozen=True)
class EthernetAdapter:
    label: str
    backend: str
    config: dict = field(default_factory=dict)
    available: bool = True
    detail: str = ''

    @property
    def identity(self):
        return self.backend, self.config.get('name', self.config.get('serial', 'virtual'))


def virtual_adapter():
    return EthernetAdapter('Simulated Ethernet | Virtual Ethernet 1', 'virtual', dict(mac=LOCAL_MAC),
                           detail='In-process test traffic; no physical network transmission.')


class AdapterAddress(C.Structure):
    pass


AdapterAddress._fields_ = [
    ('alignment', C.c_uint64), ('next', C.POINTER(AdapterAddress)), ('name', C.c_char_p),
    ('unicast', C.c_void_p), ('anycast', C.c_void_p), ('multicast', C.c_void_p), ('dns', C.c_void_p),
    ('dns_suffix', C.c_wchar_p), ('description', C.c_wchar_p), ('friendly_name', C.c_wchar_p),
    ('mac', C.c_ubyte * 8), ('mac_length', C.c_uint32), ('flags', C.c_uint32),
    ('mtu', C.c_uint32), ('interface_type', C.c_uint32), ('oper_status', C.c_uint32),
]


def windows_adapters():
    """IP Helper GUID/friendly name/MAC mapping, without PowerShell or admin rights."""
    if os.name != 'nt': return []
    function = C.WinDLL('iphlpapi').GetAdaptersAddresses
    function.argtypes = [C.c_uint32, C.c_uint32, C.c_void_p, C.c_void_p, C.POINTER(C.c_uint32)]
    function.restype = C.c_uint32
    size = C.c_uint32(16384)
    for _ in range(3):
        buffer = C.create_string_buffer(size.value)
        status = function(0, 0, None, buffer, C.byref(size))
        if status == 111: continue  # ERROR_BUFFER_OVERFLOW: adapters may change during enumeration.
        if status == 232: return []  # ERROR_NO_DATA
        if status: raise RuntimeError(f'Windows Ethernet enumeration failed (error {status}).')
        result = []; current = C.cast(buffer, C.POINTER(AdapterAddress))
        while current:
            entry = current.contents
            if entry.interface_type == 6 and entry.mac_length == 6:
                result.append(dict(guid=entry.name.decode(), label=entry.friendly_name or entry.name.decode(),
                                   description=entry.description or '', mac=mac_text(entry.mac[:6]),
                                   up=entry.oper_status == 1))
            current = entry.next
        return result
    raise RuntimeError('Windows adapter list changed repeatedly; retry Refresh adapters.')


def discover_pc(adapters=None, api=None):
    from signal_explorer.ethernet.pcap import NpcapAPI
    hardware = windows_adapters() if adapters is None else adapters
    problem = ''
    try:
        capture = (api if api is not None else NpcapAPI()).devices()
    except Exception as exc:
        capture = []; problem = str(exc)
    options = []
    for adapter in hardware:
        name = next((c['name'] for c in capture if c['name'].casefold().endswith(adapter['guid'].casefold())), None)
        ready = name is not None
        detail = (f"{adapter['description']} | MAC {adapter['mac']} | {'Link up' if adapter['up'] else 'Link down'} | "
                  'Npcap; direction inferred from source MAC. NIC offloads may alter checksums/VLAN/padding.')
        if not ready: detail += ' | ' + (problem or 'Npcap cannot access this adapter. Check capture permissions.')
        config = dict(name=name or adapter['guid'], mac=adapter['mac'], link_up=adapter['up'])
        label = f"PC Ethernet | {adapter['label']} | {adapter['mac']} | {'up' if adapter['up'] else 'down'}"
        if not ready: label += ' | capture unavailable'
        options.append(EthernetAdapter(label, 'pcap', config, ready, detail))
    if os.name != 'nt' and adapters is None: problem = 'PC Ethernet capture currently requires Windows and Npcap.'
    return options, problem or f'{len(options)} PC Ethernet interfaces found (includes virtual Ethernet interfaces).'


def discover_intrepid(api=None):
    try:
        if api is None: import ics as api
        from signal_explorer.lin.intrepid import is_valuecan_el, serial_number
        options = []
        for device in api.find_devices():
            if not is_valuecan_el(device): continue
            serial = serial_number(api, device)
            busy = bool(device.NumberOfClients)
            options.append(EthernetAdapter(
                f'Intrepid ValueCAN4-2EL | {serial} | Ethernet 1' + (' | in use' if busy else ''),
                'neovi', dict(serial=serial, channel=1), True,
                'Requires external 12 V and an Ethernet link; uses installed neoVI Ethernet settings. '
                'Disconnect other CAN/LIN/Vehicle Spy sessions on this device before connecting.'))
        return options, f'{len(options)} ValueCAN4-2EL Ethernet interfaces found.'
    except ImportError:
        return [], 'Intrepid: install pip install -e ".[intrepid]" plus Intrepid drivers / 64-bit icsneo40.dll.'
    except Exception as exc:
        return [], f'Intrepid Ethernet discovery failed: {exc}'


def discover_adapters():
    options = [virtual_adapter()]; reports = []
    for discover in (discover_pc, discover_intrepid):
        try:
            found, report = discover(); options.extend(found); reports.append(report)
        except Exception as exc:
            reports.append(str(exc))
    return options, '\n'.join(reports)
