"""The 32-bit NI-CAN helper. Its standard streams carry newline-delimited JSON."""

import ctypes
import json
import re
import struct
import sys

import can


# NI-CAN Frame API attributes from nican.h.
NUM_CARDS = 0x80020002
SERIAL = 0x80020003
FORM_FACTOR = 0x80020004
SERIES = 0x80020005
NUM_PORTS = 0x80020006
TRANSCEIVER = 0x80020007
INTERFACE_NUMBER = 0x80020008


def hardware_channels(driver):
    query = driver.ncGetHardwareInfo
    query.argtypes = [ctypes.c_uint32] * 4 + [ctypes.POINTER(ctypes.c_uint32)]
    query.restype = ctypes.c_int32

    def read(card, port, attribute):
        value = ctypes.c_uint32()
        status = query(card, port, attribute, ctypes.sizeof(value), ctypes.byref(value))
        if status < 0:
            raise RuntimeError(f"NI-CAN hardware query failed (0x{status & 0xffffffff:08X}).")
        return value.value

    channels = []
    for card in range(1, read(1, 1, NUM_CARDS) + 1):
        if read(card, 1, FORM_FACTOR) != 4:  # USB
            continue
        series = read(card, 1, SERIES)
        if series not in (2, 3):  # NI USB-847x and USB-847xs
            continue
        serial = read(card, 1, SERIAL)
        model = "USB-8473s" if series == 3 else "USB-8473"
        for port in range(1, read(card, 1, NUM_PORTS) + 1):
            if read(card, port, TRANSCEIVER) == 0:  # high-speed CAN
                name = f"CAN{read(card, port, INTERFACE_NUMBER)}"
                channels.append(dict(channel=name, serial=f"{serial:X}", device_serial=f"{serial:X}",
                                     device_model=model, physical_port=port,
                                     channel_label=name, supports_fd=False))
    return channels


def frame_dict(frame):
    return dict(timestamp=frame.timestamp, arbitration_id=frame.arbitration_id,
                is_extended_id=frame.is_extended_id, is_remote_frame=frame.is_remote_frame,
                is_error_frame=frame.is_error_frame, dlc=frame.dlc,
                data=[] if frame.is_remote_frame else list(frame.data))


def serve(driver_module, source, output):
    bus = None
    try:
        for line in source:
            closing = False
            try:
                request = json.loads(line)
                operation = request["operation"]
                result = None
                if operation == "scan":
                    result = hardware_channels(driver_module.nican)
                elif operation == "open" and bus is None:
                    channel = request["channel"]
                    if not re.fullmatch(r"CAN[0-9]+", channel):
                        raise ValueError("Enter the NI MAX interface name, such as CAN0.")
                    serial = request.get("serial")
                    if serial and not any(
                        item["channel"] == channel and item["device_serial"] == serial
                        for item in hardware_channels(driver_module.nican)
                    ):
                        raise ValueError("The selected NI-CAN adapter changed. Refresh devices.")
                    bus = driver_module.NicanBus(channel=channel, bitrate=request["bitrate"])
                elif operation == "receive" and bus is not None:
                    result = []
                    frame = bus.recv(timeout=min(0.01, max(0, request["timeout"])))
                    # Batch a burst while still giving pending TX a turn.
                    while frame is not None:
                        result.append(frame_dict(frame))
                        if len(result) == 64:
                            break
                        frame = bus.recv(timeout=0)
                elif operation == "send" and bus is not None:
                    bus.send(can.Message(**request["frame"], check=True))
                elif operation == "close":
                    closing = True
                    if bus is not None:
                        opened, bus = bus, None
                        opened.shutdown()
                else:
                    raise ValueError("Unexpected NI-CAN helper request.")
                response = {"result": result}
            except Exception as exc:
                response = {"error": str(exc)}
            print(json.dumps(response), file=output, flush=True)
            if closing:
                break
    finally:
        if bus is not None:
            bus.shutdown()


def main():
    if sys.argv[1:] == ["--probe"]:
        print(struct.calcsize("P") * 8)
        return
    try:
        if sys.platform != "win32" or struct.calcsize("P") != 4:
            raise RuntimeError("NI-CAN requires a 32-bit Python process on Windows.")
        from can.interfaces import nican

        if nican.nican is None:
            raise RuntimeError("NI-CAN could not load nican.dll. Check the driver installation and NI MAX.")
    except Exception as exc:
        print(json.dumps({"error": str(exc)}), flush=True)
        return
    serve(nican, sys.stdin, sys.stdout)


if __name__ == "__main__":
    main()
