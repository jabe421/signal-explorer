"""Align a connection's hardware clock with host-epoch TX timestamps."""
import math
import time


class SessionClock:
    """A host Unix-epoch anchor advanced only by a monotonic clock."""
    def __init__(self, wall=None, monotonic=None):
        self._monotonic = monotonic or time.monotonic
        self._epoch = (wall or time.time)()
        self._start = self._monotonic()

    def now(self):
        return self._epoch + (self._monotonic()-self._start)


class HardwareClock:
    """Anchor the first device timestamp to host receipt time.

    Later frames retain their hardware timestamp differences. This avoids
    assuming that the device's date/epoch is synchronized with the PC. The
    absolute anchor includes first-frame driver latency; it is not PTP/GPS sync.
    A backwards step exceeding one second denotes a device clock reset.
    Create a fresh instance for every connection.
    """
    def __init__(self, host_now=None):
        self.offset = None
        self._last_raw = None
        self._host_now = host_now or (lambda: time.time())
        self.resets = 0

    def to_unix(self, raw):
        if not math.isfinite(raw) or raw < 0:
            raise ValueError('Invalid hardware timestamp received from adapter.')
        if self.offset is None or (self._last_raw is not None and raw < self._last_raw - 1):
            if self.offset is not None:
                self.resets += 1
            self.offset = self._host_now() - raw
        self._last_raw = raw
        return raw + self.offset
