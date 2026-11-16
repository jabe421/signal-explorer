"""Summarize signal samples without retaining the whole capture."""

import csv
from dataclasses import dataclass, field
from datetime import datetime
from io import StringIO


def range_fault(value, minimum, maximum):
    if minimum is not None and value < minimum:
        return "Below minimum"
    if maximum is not None and value > maximum:
        return "Above maximum"
    return ""


@dataclass
class SignalResult:
    name: str
    unit: str
    minimum: float | None
    maximum: float | None
    samples: int = 0
    faults: int = 0
    observed_min: float | None = None
    observed_max: float | None = None
    first_fault: float | None = None

    def observe(self, value):
        self.samples += 1
        self.observed_min = value if self.observed_min is None else min(value, self.observed_min)
        self.observed_max = value if self.observed_max is None else max(value, self.observed_max)
        if range_fault(value, self.minimum, self.maximum):
            self.faults += 1
            if self.first_fault is None:
                self.first_fault = value

    def outcome(self, dropped):
        if self.faults:
            return "FAIL"
        if not self.samples:
            return "NO DATA"
        if self.minimum is None and self.maximum is None:
            return "NO LIMITS"
        return "INCOMPLETE" if dropped else "PASS"


@dataclass
class SignalTestReport:
    channel: str
    direction: str
    results: list[SignalResult]
    started: datetime = field(default_factory=lambda: datetime.now().astimezone())
    stopped: datetime | None = None
    reason: str = ""
    dropped: int = 0

    def to_csv(self):
        output = StringIO(newline="")
        writer = csv.writer(output)
        writer.writerow(("Result", "Signal", "Unit", "Minimum", "Maximum", "Samples",
                         "Out of range samples", "Observed minimum", "Observed maximum",
                         "First failing value", "Channel", "Direction", "Started", "Stopped",
                         "Stop reason", "Dropped CAN display frames"))
        order = {"FAIL": 0, "INCOMPLETE": 1, "NO DATA": 2, "NO LIMITS": 3, "PASS": 4}
        for result in sorted(self.results, key=lambda item: order[item.outcome(self.dropped)]):
            writer.writerow((result.outcome(self.dropped), result.name, result.unit, result.minimum,
                             result.maximum, result.samples, result.faults, result.observed_min,
                             result.observed_max, result.first_fault, self.channel, self.direction,
                             self.started.isoformat(timespec="seconds"),
                             self.stopped.isoformat(timespec="seconds"), self.reason, self.dropped))
        return output.getvalue()
