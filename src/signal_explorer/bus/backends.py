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
        label="Kvaser (U100 / CANlib)",
        interface="kvaser",
        default_channel="0",
        notes="Requires Kvaser CANlib drivers.",
    ),
    BackendSpec(
        key="pcan",
        label="PEAK (PCAN-USB Pro FD / PCAN)",
        interface="pcan",
        default_channel="PCAN_USBBUS1",
        notes="Requires PEAK drivers and PCAN-Basic API. Select a detected CAN channel or enter PCAN_USBBUS1 / PCAN_USBBUS2. Enable CAN FD for FD-capable hardware; Classic CAN remains the default.",
    ),
    BackendSpec(
        key="vector",
        label="Vector (VN16xx / CANXL)",
        interface="vector",
        default_channel="0",
        extra_kwargs={"app_name": None},
        notes="Select a detected physical CAN channel. Requires Vector XL Driver Library (64-bit); no application mapping is needed.",
    ),
    BackendSpec(
        key="ics_neovi",
        label="Intrepid (ValueCAN / neoVI)",
        interface="neovi",
        default_channel="1",
        notes="Uses Intrepid native API, not its COM port. HSCAN is CAN1; HSCAN2 is CAN2 on ValueCAN4-2. Requires the intrepid optional dependency.",
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
