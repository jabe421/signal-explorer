import json
import sys

import can
import pytest

from signal_explorer.bus import nican_bridge
from signal_explorer.bus.backends import BACKENDS
from signal_explorer.bus.service import BusService


def test_nican_discovery_and_classic_frame_roundtrip_across_process(monkeypatch):
    script = """
from types import SimpleNamespace
import sys
from signal_explorer.bus import nican_worker as worker

class Driver:
    def __call__(self, card, port, attr, size, value):
        value._obj.value = {
            worker.NUM_CARDS: 1, worker.FORM_FACTOR: 4, worker.SERIES: 3,
            worker.SERIAL: 0xABCD, worker.NUM_PORTS: 1,
            worker.TRANSCEIVER: 0, worker.INTERFACE_NUMBER: 2,
        }[attr]
        return 0

class Bus:
    def __init__(self, channel, bitrate):
        assert channel == 'CAN2' and bitrate == 500000
        self.frames = []
    def send(self, frame):
        frame.timestamp = 123.5
        self.frames.append(frame)
    def recv(self, timeout):
        return self.frames.pop(0) if self.frames else None
    def shutdown(self):
        pass

worker.serve(SimpleNamespace(nican=SimpleNamespace(ncGetHardwareInfo=Driver()),
                             NicanBus=Bus), sys.stdin, sys.stdout)
"""
    monkeypatch.setattr(nican_bridge, "helper_command", lambda: [sys.executable, "-c", script])
    options, _ = nican_bridge.discover_nican_channels()
    assert len(options) == 1
    config = options[0].config
    assert config["device_model"] == "USB-8473s"
    assert config["channel"] == "CAN2"
    backend = next(item for item in BACKENDS if item.key == "nican")
    result = BusService._open_connection(backend, "CAN2", 500000, False, config)
    bus = result["bus"]
    try:
        assert bus.protocol == can.CanProtocol.CAN_20
        assert bus.recv(timeout=0) is None
        bus.send(can.Message(arbitration_id=0x321, is_extended_id=True, data=[0xAB]))
        frame = bus.recv(timeout=0.1)
        assert (frame.channel, frame.timestamp, frame.arbitration_id) == ("CAN2", 123.5, 0x321)
        assert frame.is_extended_id and frame.data == b"\xAB"
        bus.send(can.Message(arbitration_id=0x123, is_remote_frame=True, dlc=8))
        remote = bus.recv(timeout=0.1)
        assert remote.is_remote_frame and remote.dlc == 8 and not remote.data
        with pytest.raises(can.CanOperationError, match="Classic CAN"):
            bus.send(can.Message(arbitration_id=1, is_fd=True))
    finally:
        bus.shutdown()
    assert bus._bridge.process.poll() == 0
    assert not bus._bridge._reader.is_alive()

    with pytest.raises(RuntimeError, match="adapter changed"):
        BusService._open_connection(backend, "CAN2", 500000, False, dict(config, serial="wrong"))


def test_helper_reports_missing_driver_without_hanging(monkeypatch):
    script = f"print({json.dumps(json.dumps({'error': 'NI-CAN driver unavailable'}))}, flush=True)"
    monkeypatch.setattr(nican_bridge, "helper_command", lambda: [sys.executable, "-c", script])
    with pytest.raises(can.CanError, match="driver unavailable"):
        nican_bridge.discover_nican_channels()
