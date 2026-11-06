"""Bounded, loss-accounted event history shared by script transport adapters."""
from collections import OrderedDict
from threading import RLock
import time


class ReceiveMailbox:
    def __init__(self, capacity):
        if capacity < 1:
            raise ValueError('Receive capacity must be positive.')
        self.capacity = capacity
        self._lock = RLock()
        self._events = OrderedDict()
        self._sequence = 0
        self.received = self.dropped = self.filtered = self.flushed = self.consumed = 0

    def append(self, event):
        with self._lock:
            self.received += 1
            self._sequence += 1
            if len(self._events) == self.capacity:
                self._events.popitem(last=False)
                self.dropped += 1
            self._events[self._sequence] = event, time.monotonic()

    def reject(self):
        with self._lock:
            self.filtered += 1

    def after(self, cursor):
        # Snapshot only retained records. The scan is bounded by capacity even
        # if a long-lived unmatched frame predates millions of consumed events.
        with self._lock:
            return [(sequence, event, arrived) for sequence, (event, arrived) in self._events.items()
                    if sequence > cursor]

    def take(self, sequence):
        with self._lock:
            record = self._events.pop(sequence, None)
            if record is not None:
                self.consumed += 1
                return record[0]

    def flush(self, keep=None):
        with self._lock:
            remove = [key for key, (event, _) in self._events.items() if keep is None or not keep(event)]
            for key in remove:
                del self._events[key]
            self.flushed += len(remove)
            return len(remove)

    def stats(self):
        with self._lock:
            return dict(capacity=self.capacity, buffered=len(self._events), received=self.received,
                        consumed=self.consumed, dropped=self.dropped, filtered=self.filtered, flushed=self.flushed)
