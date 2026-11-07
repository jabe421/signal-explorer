"""Validated CAN timing requests, independent of widgets and native drivers."""
from dataclasses import dataclass
import math
import can
from signal_explorer.bus.strategies import interfaces_supporting, strategy_for

SILENT_INTERFACES = interfaces_supporting('listen_only')
ADVANCED_INTERFACES = interfaces_supporting('advanced_timing')
SCAN_INTERFACES = SILENT_INTERFACES - {'virtual'}
SCAN_RATES = (500000, 250000, 125000, 1000000, 100000, 50000, 20000, 10000)


@dataclass(frozen=True)
class TimingSettings:
    bitrate: int = 500000
    data_bitrate: int = 2000000
    advanced: bool = False
    sample_point: float = 80.0
    sjw: int = 1
    data_sample_point: float = 75.0
    data_sjw: int = 1

    def build(self, interface, fd=False):
        for value, maximum, name in ((self.bitrate, 1000000, 'Arbitration bitrate'),
                                     (self.data_bitrate, 20000000, 'Data bitrate')):
            if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= maximum:
                raise ValueError(f'{name} must be an integer from 1 to {maximum} bit/s.')
        if fd and self.data_bitrate < self.bitrate:
            raise ValueError('Data bitrate must be at least the arbitration bitrate.')
        if not self.advanced:
            return None
        if interface not in ADVANCED_INTERFACES:
            raise ValueError('Detailed timing is unavailable for this backend; use driver timing defaults.')
        for sample, sjw in ((self.sample_point, self.sjw), (self.data_sample_point, self.data_sjw)):
            if not math.isfinite(sample) or not 50 <= sample < 100:
                raise ValueError('Sample point must be from 50 to less than 100 percent.')
            if not isinstance(sjw, int) or isinstance(sjw, bool) or sjw < 1:
                raise ValueError('SJW must be a positive integer number of time quanta.')
        clock = strategy_for(interface).timing_clock(fd)
        candidates = []
        if fd:
            for t in can.BitTimingFd.iterate_from_sample_point(clock, self.bitrate, self.sample_point,
                                                              self.data_bitrate, self.data_sample_point):
                try:
                    candidate = can.BitTimingFd(clock, t.nom_brp, t.nom_tseg1, t.nom_tseg2, self.sjw,
                                                t.data_brp, t.data_tseg1, t.data_tseg2, self.data_sjw, strict=True)
                    if candidate.nom_bitrate == self.bitrate and candidate.data_bitrate == self.data_bitrate:
                        candidates.append(candidate)
                except ValueError:
                    pass
            score = lambda t: abs(t.nom_sample_point-self.sample_point)+abs(t.data_sample_point-self.data_sample_point)
        else:
            for t in can.BitTiming.iterate_from_sample_point(clock, self.bitrate, self.sample_point):
                try:
                    candidate = can.BitTiming(clock, t.brp, t.tseg1, t.tseg2, self.sjw, strict=True)
                    if candidate.bitrate == self.bitrate:
                        candidates.append(candidate)
                except ValueError:
                    pass
            score = lambda t: abs(t.sample_point-self.sample_point)
        if not candidates:
            raise ValueError('No valid timing for this bitrate, controller clock and SJW. Choose different values.')
        return min(candidates, key=score)
