"""Compare replay waits without hardware or global system timer changes."""

import argparse
import json
from pathlib import Path
import platform
import statistics
import sys
from PySide6.QtCore import QCoreApplication
from signal_explorer.recording.codecs import BusEvent
from signal_explorer.recording.playback import MF4Playback


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--frames", type=int, default=600)
    parser.add_argument("--interval-ms", type=float, default=2)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if (
        not 2 <= args.frames <= 50000
        or not 0 < args.interval_ms <= 1000
        or not 1 <= args.repeats <= 10
    ):
        parser.error(
            "Use 2–50000 frames, a positive interval up to 1000 ms, and 1–10 repeats."
        )
    duration = (args.frames - 1) * args.interval_ms / 1000
    if duration > 60:
        parser.error("Each benchmark run must fit within 60 seconds.")
    app = QCoreApplication.instance() or QCoreApplication([])

    class Codec:
        protocol = "BENCHMARK"

        def read(self, path):
            for index in range(args.frames):
                yield BusEvent(
                    1000 + index * args.interval_ms / 1000,
                    1,
                    "tx",
                    self.protocol,
                    index,
                )

        def replayable(self, event):
            return True

        def prepare_tx(self, event, options):
            return event.payload

    configurations = [
        ("condition", "balanced", "condition"),
        ("balanced", "balanced", "auto"),
        ("precision", "precision", "auto"),
    ]
    runs = []
    for repeat in range(args.repeats):
        ordered = configurations if repeat % 2 == 0 else list(reversed(configurations))
        for name, mode, backend in ordered:
            worker = MF4Playback(
                "synthetic",
                lambda _: True,
                codec=Codec(),
                timing_mode=mode,
                wait_backend=backend,
            )
            worker.start()
            if not worker.wait(int(duration * 1000 + 5000)):
                worker.stop()
                worker.wait(2000)
                raise RuntimeError("Replay benchmark did not finish.")
            app.processEvents()
            report = worker.timing_snapshot()
            if report["state"] != "completed" or report["sent"] != args.frames:
                raise RuntimeError(f"Incomplete replay benchmark: {report}")
            runs.append(dict(configuration=name, repeat=repeat + 1, **report))
            print(
                json.dumps(
                    dict(
                        configuration=name,
                        p95_ms=report["scheduler_lateness"]["p95_ms"],
                        cpu_ms=report["worker_cpu_ms"],
                    )
                ),
                flush=True,
            )
    summary = {}
    for name, _, _ in configurations:
        selected = [run for run in runs if run["configuration"] == name]
        summary[name] = dict(
            median_p95_ms=statistics.median(
                r["scheduler_lateness"]["p95_ms"] for r in selected
            ),
            median_cpu_ms=statistics.median(r["worker_cpu_ms"] for r in selected),
        )
    result = dict(
        python=sys.version,
        platform=platform.platform(),
        frames=args.frames,
        interval_ms=args.interval_ms,
        repeats=args.repeats,
        summary=summary,
        runs=runs,
        limits="Synthetic host scheduler only. CPU accounting may be quantized; short runs can read zero. No wire-timing guarantees.",
    )
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print("Summary:", json.dumps(summary))


if __name__ == "__main__":
    main()
