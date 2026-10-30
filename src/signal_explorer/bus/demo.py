"""Synthetic engineering values, not an OBD-II diagnostic exchange."""
import math
import struct
import can


def demo_reply(request: can.Message) -> can.Message | None:
    """Echo an eight-byte standard 321-hex request from the virtual tester."""
    if (request.arbitration_id != 0x321 or request.is_extended_id
            or request.is_remote_frame or request.is_error_frame
            or request.dlc != 8 or len(request.data) != 8):
        return None
    return can.Message(arbitration_id=0x221, is_extended_id=False,
                       data=bytes(request.data), is_fd=request.is_fd,
                       bitrate_switch=request.bitrate_switch, check=True)


def demo_frames(tick, fd=False):
    phase = tick / 15
    rpm = round((1800 + 1000 * math.sin(phase)) / 0.25)
    maf = round((30 + 20 * math.sin(phase)) / 0.01)
    o2 = round((0.45 + 0.35 * math.sin(phase * 2)) / 0.001)
    oxygen_lambda = round((1 + 0.06 * math.sin(phase * 2)) / 0.001)
    frames = [
        can.Message(arbitration_id=0x100, is_extended_id=False,
                    data=[tick & 255, 110, 2, 3, 0xDE, 0xAD, 0xBE, 0xEF]),
        can.Message(arbitration_id=0x200, is_extended_id=False,
                    data=struct.pack('<HHHH', rpm, maf, o2, oxygen_lambda)),
    ]
    if fd:
        payload = bytearray(32)
        struct.pack_into('<HHHH', payload, 0, rpm, maf, o2, oxygen_lambda)
        struct.pack_into('<HHH', payload, 8, tick & 65535, 900 - o2, oxygen_lambda)
        frames.append(can.Message(arbitration_id=0x300, is_extended_id=False,
                                  is_fd=True, bitrate_switch=True, data=payload, check=True))
    return frames
