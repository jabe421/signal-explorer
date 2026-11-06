"""Cancellable, receive-only Classic CAN candidate scan. No TX probing."""
import time
from threading import Event
import can
from PySide6.QtCore import QThread, Signal
from signal_explorer.bus.listen_only import open_bus
from signal_explorer.bus.timing import SCAN_INTERFACES
from signal_explorer.bus.discovery import prepare_vector_library
from signal_explorer.bus.ownership import DeviceLease


def scan_rates(kwargs, rates, dwell, minimum, cancelled, report, factory=open_bus):
    if kwargs.get('interface') not in SCAN_INTERFACES:
        raise ValueError('Passive detection is unavailable for this backend. Virtual traffic cannot determine a physical bitrate.')
    if not rates or len(rates) > 32 or any(type(r) is not int or not 1 <= r <= 1000000 for r in rates):
        raise ValueError('Provide 1-32 Classic CAN candidate rates from 1 to 1000000 bit/s.')
    if not .1 <= dwell <= 10 or not 2 <= minimum <= 1000:
        raise ValueError('Invalid observation interval or minimum frame count.')
    matches = []
    for rate in dict.fromkeys(rates):
        if cancelled.is_set():
            break
        bus = None
        try:
            bus = factory(monitor_only=True, **dict(kwargs, bitrate=rate, receive_own_messages=False))
            # Discard any queued events left from initialization/previous use.
            for _ in range(10000):
                if cancelled.is_set() or bus.recv(0) is None:
                    break
            frames = errors = 0
            end = time.monotonic()+dwell
            while not cancelled.is_set() and time.monotonic() < end:
                msg = bus.recv(min(.05, max(0, end-time.monotonic())))
                if msg is None:
                    continue
                if msg.is_error_frame:
                    errors += 1
                elif msg.is_rx and not msg.is_fd and not msg.is_remote_frame:
                    frames += 1
            report(f'{rate} bit/s: {frames} valid Classic data frames, {errors} error events')
            if not cancelled.is_set() and frames >= minimum and errors == 0:
                matches.append(rate)
        finally:
            if bus is not None:
                bus.shutdown()
    return [] if cancelled.is_set() else matches


class BitrateScanner(QThread):
    progress = Signal(str)
    result = Signal(object)
    failed = Signal(str)

    def __init__(self, kwargs, rates, dwell, minimum, parent=None):
        super().__init__(parent)
        self.kwargs, self.rates, self.dwell, self.minimum = kwargs, rates, dwell, minimum
        self.cancelled = Event()

    def cancel(self):
        self.cancelled.set()

    def run(self):
        lease = None
        try:
            if self.kwargs['interface'] == 'vector':
                prepare_vector_library()
                lease = DeviceLease('vector', self.kwargs.get('serial'), 'CAN')
            found = scan_rates(self.kwargs, self.rates, self.dwell, self.minimum,
                               self.cancelled, self.progress.emit)
            self.result.emit(found)
        except Exception as exc:
            self.failed.emit(f'Passive scan stopped: {exc}')
        finally:
            if lease:
                lease.release()
