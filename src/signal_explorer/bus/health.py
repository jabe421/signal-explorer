"""Bounded, thread-safe CAN health observations. No widgets or driver calls."""
from collections import OrderedDict
from dataclasses import dataclass, field
from threading import RLock
import math
import time


def frame_time_range(msg, bitrate, data_bitrate):
    """Framing estimate for observed data/RTR frames, including intermission.

    Classic uses the unstuffed length and worst-case dynamic stuffing count.
    FD uses an approximate phase split and a conservative stuffing allowance;
    it is not bit-exact ISO FD encoding or a guaranteed physical bound.
    Error/retransmission occupancy cannot be inferred from a raw error record.
    """
    if msg.is_error_frame or bitrate <= 0:
        return 0.0, 0.0
    length = 0 if msg.is_remote_frame else msg.dlc
    if not msg.is_fd:
        fixed = 67 if msg.is_extended_id else 47
        stuffable = (54 if msg.is_extended_id else 34)+8*length
        lower = fixed+8*length
        return lower/bitrate, (lower+(stuffable-1)//4)/bitrate
    # Nominal phase includes the arbitration/control prefix and ACK/EOF/IFS.
    nominal = (41 if msg.is_extended_id else 22)+13
    # ESI+DLC, payload, CRC17/21, SBC/parity and fixed CRC stuffing allowance.
    fast = 5+8*length+(17 if length<=16 else 21)+4+(5 if length<=16 else 6)+1
    fast_rate = data_bitrate if msg.bitrate_switch else bitrate
    lower = nominal/bitrate+fast/fast_rate
    upper = (nominal+math.ceil(nominal/4))/bitrate+(fast+math.ceil(fast/4))/fast_rate
    return lower, upper


@dataclass
class _Channel:
    label: str
    rx: int = 0
    tx: int = 0
    error_frames: int = 0
    native_events: int = 0
    send_failures: int = 0
    last_error: str = ''
    buckets: OrderedDict = field(default_factory=OrderedDict)
    native: dict = field(default_factory=dict)
    native_at: float | None = None


class BusHealth:
    WINDOW = 2.0
    BUCKET = .1

    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.lock = RLock()
        self.generation = 0
        self.active = False
        self.channels = {}
        self.started = self.stopped = clock()
        self.session = {}
        self.fault = ''

    def start(self, channels, session):
        with self.lock:
            self.generation += 1
            self.active = True
            self.started = self.clock()
            self.channels = {str(c['native_channel']): _Channel(c['label']) for c in channels}
            self.session = dict(session)
            self.fault = ''
            return self.generation

    def stop(self):
        with self.lock:
            self.stopped = self.clock()
            self.active = False

    def reset_observations(self):
        with self.lock:
            for channel in self.channels.values():
                channel.rx = channel.tx = channel.error_frames = channel.native_events = channel.send_failures = 0
                channel.last_error = ''
                channel.buckets.clear()
            self.started = self.clock() if self.active else self.stopped

    def observe(self, msg):
        with self.lock:
            channel = self.channels.get(str(msg.channel))
            if not self.active or channel is None:
                return
            if msg.is_error_frame:
                channel.error_frames += 1
                channel.last_error = 'Driver-reported CAN error frame; detailed cause not available in this record.'
                return
            if msg.is_rx: channel.rx += 1
            else: channel.tx += 1
            tick = int(self.clock()/self.BUCKET)
            bucket = channel.buckets.setdefault(tick, [0, 0.0, 0.0])
            lo, hi = frame_time_range(msg, self.session['bitrate'], self.session.get('data_bitrate') or self.session['bitrate'])
            bucket[0] += 1; bucket[1] += lo; bucket[2] += hi
            while len(channel.buckets)>int(self.WINDOW/self.BUCKET)+1:
                channel.buckets.popitem(last=False)

    def send_failed(self, channel, error):
        with self.lock:
            row = self.channels.get(str(channel))
            if self.active and row is not None:
                row.send_failures += 1
                row.last_error = str(error)

    def native_update(self, channel, values, generation=None, error_event=False, observed_at=None):
        with self.lock:
            if not self.active or (generation is not None and generation != self.generation):
                return
            row = self.channels.get(str(channel))
            if row is None: return
            row.native.update(values)
            if 'state' in values or 'driver_load' in values:
                row.native_at = self.clock() if observed_at is None else observed_at
            if error_event:
                row.native_events += 1
                row.last_error = values.get('last_error', 'Native CAN error event')

    def snapshot(self):
        with self.lock:
            now = self.clock() if self.active else self.stopped
            elapsed = max(0, min(self.WINDOW, now-self.started))
            first_tick = int((now-self.WINDOW)/self.BUCKET)+1
            rows = []
            for key, row in self.channels.items():
                buckets = [b for tick,b in row.buckets.items() if tick>=first_tick]
                count = sum(b[0] for b in buckets)
                lo, hi = (sum(b[i] for b in buckets) for i in (1,2))
                native = dict(row.native)
                rows.append(dict(channel=key, label=row.label, rx=row.rx, tx=row.tx,
                    error_frames=row.error_frames, native_events=row.native_events, send_failures=row.send_failures,
                    last_error=row.last_error, rate=count/elapsed if elapsed>=.1 else None,
                    load_low=100*lo/elapsed if elapsed>=.1 else None,
                    load_high=100*hi/elapsed if elapsed>=.1 else None,
                    native=native, native_age=now-row.native_at if row.native_at is not None else None))
            return dict(active=self.active, session=dict(self.session), generation=self.generation,
                        rows=rows, fault=self.fault, window=self.WINDOW)
