"""In-process leases until protocols share a multi-protocol native session."""
from threading import RLock

_lock = RLock()
_leases = []


class DeviceLease:
    def __init__(self, vendor, serial, protocol):
        self.vendor, self.serial, self.protocol = vendor, str(serial) if serial is not None else None, protocol
        with _lock:
            for lease in _leases:
                if lease.vendor == vendor and (lease.serial is None or self.serial is None or lease.serial == self.serial):
                    raise RuntimeError(f'{vendor} device is already open for {lease.protocol}. '
                                       'Disconnect that session before switching CAN/LIN/Ethernet on this device.')
            _leases.append(self)

    def release(self):
        with _lock:
            if self in _leases:
                _leases.remove(self)
