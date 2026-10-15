from __future__ import annotations

from dataclasses import dataclass, field

# CANable / CANable 2 often enumerate as MCS (0x16D0) USB serial.
_CANABLE_VID = 0x16D0


@dataclass(frozen=True)
class BackendSpec:
    """One python-can interface the UI can offer."""

    key: str
    label: str
    interface: str
    default_channel: str = "0"
    needs_serial: bool = False
    extra_kwargs: dict = field(default_factory=dict)
    notes: str = ""


BACKENDS: tuple[BackendSpec, ...] = (
    BackendSpec(
        key="virtual",
        label="Virtual (no hardware)",
        interface="virtual",
        default_channel="signal-explorer",
        notes="Two python-can virtual buses on this channel share frames.",
    ),
    BackendSpec(
        key="slcan",
        label="CANable (SLCAN serial)",
        interface="slcan",
        default_channel="COM3",
        needs_serial=True,
        extra_kwargs={"tty_baudrate": 115200, "sleep_after_open": 2},
        notes="CANable in SLCAN firmware. Use the COM port (COM3 on this PC). Bitrate must match the bus.",
    ),
    BackendSpec(
        key="gs_usb",
        label="CANable / candleLight (gs_usb)",
        interface="gs_usb",
        default_channel="0",
        notes="CANable Pro / candleLight USB devices.",
    ),
    BackendSpec(
        key="kvaser",
        label="Kvaser",
        interface="kvaser",
        default_channel="0",
        notes="Requires Kvaser CANlib drivers.",
    ),
    BackendSpec(
        key="vector",
        label="Vector",
        interface="vector",
        default_channel="0",
        extra_kwargs={"app_name": "Signal Explorer"},
        notes="Requires Vector drivers and XL Library. Assign a channel to this app name in Vector Hardware Config, or use CANalyzer's assignment.",
    ),
    BackendSpec(
        key="ics_neovi",
        label="Intrepid neoVI",
        interface="ics_neovi",
        default_channel="1",
        notes="Requires Intrepid drivers. Install optional extra: pip install python-ics",
    ),
)

BITRATES = (125000, 250000, 500000, 1000000)


def list_serial_ports() -> list[tuple[str, str]]:
    """Return (device, description) for USB serial adapters."""
    from serial.tools.list_ports import comports

    return [(port.device, port.description or port.device) for port in comports()]


def preferred_slcan_port() -> str | None:
    from serial.tools.list_ports import comports

    ports = list(comports())
    for port in ports:
        if port.vid == _CANABLE_VID:
            return port.device
    for port in ports:
        if port.device.upper() == "COM3":
            return port.device
    return ports[0].device if ports else None
