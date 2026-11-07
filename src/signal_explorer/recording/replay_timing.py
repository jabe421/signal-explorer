"""Bounded host-side replay measurements, independent of protocol and Qt."""

from collections import deque
import math

MODES = {"balanced": 0.0, "precision": 0.0005}


class TimingSamples:
    """Whole-run count/mean/max; percentiles from the most recent 4096 samples."""

    def __init__(self, capacity=4096):
        self.samples = deque(maxlen=capacity)
        self.count = 0
        self.total = self.maximum = 0.0

    def add(self, seconds):
        seconds = max(0.0, seconds)
        self.count += 1
        self.total += seconds
        self.maximum = max(self.maximum, seconds)
        self.samples.append(seconds)

    def raw_snapshot(self):
        return (
            self.count,
            self.total,
            self.maximum,
            list(self.samples),
            self.samples.maxlen,
        )

    def snapshot(self):
        return self.summarize(self.raw_snapshot())

    @staticmethod
    def summarize(raw):
        count, total, maximum, samples, capacity = raw
        ordered = sorted(samples)

        def percentile(fraction):
            return (
                ordered[max(0, math.ceil(len(ordered) * fraction) - 1)] * 1000
                if ordered
                else None
            )

        return dict(
            count=count,
            mean_ms=total / count * 1000 if count else None,
            max_ms=maximum * 1000 if count else None,
            p50_ms=percentile(0.5),
            p95_ms=percentile(0.95),
            p99_ms=percentile(0.99),
            percentile_samples=len(ordered),
            percentile_capacity=capacity,
        )
