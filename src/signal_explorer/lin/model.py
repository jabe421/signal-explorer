"""LIN identifiers, checksums and events, independent of GUI and device APIs."""
from dataclasses import dataclass
from typing import Protocol


def protected_id(identifier):
    if not 0 <= identifier <= 63:
        raise ValueError("LIN frame ID must be 00-3F (six bits, not a protected ID).")
    b = [(identifier >> n) & 1 for n in range(6)]
    p0 = b[0] ^ b[1] ^ b[2] ^ b[4]
    p1 = 1 ^ b[1] ^ b[3] ^ b[4] ^ b[5]
    return identifier | (p0 << 6) | (p1 << 7)


def checksum_model(identifier, model):
    if model not in {"classic", "enhanced"}:
        raise ValueError("Select classic or enhanced checksum.")
    return "classic" if identifier in (60, 61) else model


def lin_checksum(identifier, data, model="enhanced"):
    if not 1 <= len(data) <= 8:
        raise ValueError("LIN responses require 1-8 bytes.")
    total = protected_id(identifier) if checksum_model(identifier, model) == "enhanced" else 0
    for byte in data:
        total += byte
        total = (total & 255) + (total >> 8)
    return (~total) & 255


@dataclass(frozen=True)
class LinFrame:
    timestamp: float
    frame_id: int
    data: bytes = b""
    direction: str = "rx"
    kind: str = "frame"
    checksum: int | None = None
    model: str = "enhanced"
    publisher: str = ""
    channel: int = 1
    pid: int | None = None

    def __post_init__(self):
        protected_id(self.frame_id)
        object.__setattr__(self, "data", bytes(self.data))
        if self.direction not in {"rx", "tx"}:
            raise ValueError("LIN direction must be rx or tx.")
        if len(self.data) > 8:
            raise ValueError("LIN payload is limited to 8 bytes.")
        if self.pid is None:
            object.__setattr__(self, "pid", protected_id(self.frame_id))


@dataclass(frozen=True)
class Response:
    data: bytes
    model: str = "enhanced"


@dataclass(frozen=True)
class ScheduleSlot:
    frame_id: int
    delay_ms: float


def validate_schedule(slots, lengths, baudrate):
    if not slots:
        raise ValueError("Add at least one schedule slot.")
    import math
    for slot in slots:
        if not 0 <= slot.frame_id <= 61:
            raise ValueError("Transmit IDs must be 00-3D; 3E and 3F are reserved.")
        if slot.frame_id not in lengths:
            raise ValueError(f"Define the response length for ID {slot.frame_id:02X} first.")
        minimum = 1.4 * (34 + 10 * (lengths[slot.frame_id] + 1)) * 1000 / baudrate
        if not math.isfinite(slot.delay_ms) or not minimum <= slot.delay_ms <= 2_147_483_647:
            raise ValueError(f"Slot {slot.frame_id:02X} needs at least {minimum:.2f} ms at {baudrate} bit/s.")


class LinTransport(Protocol):
    """Hardware backends must buffer responses in the adapter, not the GUI."""
    def header(self, frame_id: int): ...
    def set_response(self, frame_id: int, response: Response): ...
    def remove_response(self, frame_id: int): ...
    def sleep(self): ...
    def wake(self): ...
    def close(self): ...
    responses: dict[int, Response]
