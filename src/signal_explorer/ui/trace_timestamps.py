"""Display formats only: never normalize or mutate captured frame clocks."""

from datetime import datetime, timezone
import math

# key, menu label, column heading, explanation
TIMESTAMP_FORMATS = (
    ("epoch", "Epoch seconds", "Timestamp", "Original Unix-epoch seconds."),
    (
        "absolute",
        "Absolute time (UTC)",
        "Timestamp (UTC)",
        "UTC date and time with microseconds.",
    ),
    (
        "relative",
        "Relative to connection start",
        "Relative time (s)",
        "Seconds since this CAN connection started; continues through monitor pauses and Clear.",
    ),
    (
        "delta",
        "Delta: previous monitored frame",
        "Delta (s)",
        "Timestamp difference from the previous monitored frame, before ID filtering, across all enabled CAN channels.",
    ),
    (
        "delta_id",
        "Delta: same channel and message ID",
        "Same-ID delta (s)",
        "Timestamp difference for the same CAN channel, message ID and standard/extended format; includes RX and TX.",
    ),
)
FORMAT_DETAILS = {
    key: (label, heading, explanation)
    for key, label, heading, explanation in TIMESTAMP_FORMATS
}


def valid_timestamp(value):
    return isinstance(value, (int, float)) and math.isfinite(value)


def format_timestamp(mode, timestamp, relative, delta, delta_id):
    if mode == "absolute":
        if not valid_timestamp(timestamp):
            return "n/a"
        try:
            return datetime.fromtimestamp(timestamp, timezone.utc).strftime(
                "%Y-%m-%d %H:%M:%S.%fZ"
            )
        except (ValueError, OverflowError, OSError):
            return "n/a"
    value = {
        "epoch": timestamp,
        "relative": relative,
        "delta": delta,
        "delta_id": delta_id,
    }[mode]
    return f"{value:.6f}" if valid_timestamp(value) else "n/a"
