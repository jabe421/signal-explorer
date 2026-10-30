"""A deterministic logical LIN bus; no electrical timing or native drivers."""
from dataclasses import replace
from threading import RLock
import time

from signal_explorer.lin.model import LinFrame, Response, protected_id, lin_checksum, checksum_model


class SimulatedLinBus:
    def __init__(self, baudrate=19200):
        self.baudrate = baudrate
        self.nodes = []
        self.awake = True
        self.next_fault = ""
        self.lock = RLock()
        self._clock = 0.0

    def attach(self, name, role, callback):
        with self.lock:
            if role not in {"master", "slave", "monitor"}:
                raise ValueError("Unknown LIN role.")
            if role == "master" and any(n.role == "master" for n in self.nodes):
                raise ValueError("A LIN bus can have only one active master.")
            node = SimulatedEndpoint(self, name, role, callback)
            self.nodes.append(node)
            return node

    def _broadcast(self, event, transmitter=None):
        for node in tuple(self.nodes):
            node.callback(replace(event, direction="tx" if node is transmitter else "rx"))

    def request(self, master, identifier):
        with self.lock:
            if master not in self.nodes or master.role != "master":
                raise ValueError("Only the master can send LIN headers.")
            if not self.awake:
                raise ValueError("LIN bus is asleep. Wake the bus before sending headers.")
            if not 0 <= identifier <= 61:
                raise ValueError("Use a frame ID from 00 to 3D.")
            now = max(time.time(), self._clock)
            fault, self.next_fault = self.next_fault, ""
            event = LinFrame(now, identifier, kind="header", publisher=master.name)
            if fault == "parity":
                self._broadcast(replace(event, kind="parity_error", pid=event.pid ^ 64), master)
                return
            self._broadcast(event, master)
            responders = [n for n in self.nodes if n.role != "monitor" and identifier in n.responses]
            if not responders or fault == "no_response":
                self._clock = now + (34 + 90) / self.baudrate
                self._broadcast(replace(event, timestamp=self._clock, kind="no_response"))
                return
            if len(responders) > 1 or fault == "collision":
                self._clock = now + (34 + 90) / self.baudrate
                self._broadcast(replace(event, timestamp=self._clock, kind="collision"))
                return
            publisher = responders[0]
            response = publisher.responses[identifier]
            self._clock = now + (34 + (len(response.data) + 1) * 10) / self.baudrate
            check = lin_checksum(identifier, response.data, response.model)
            self._broadcast(LinFrame(self._clock, identifier, response.data,
                                     kind="checksum_error" if fault == "checksum" else "frame",
                                     checksum=check ^ 1 if fault == "checksum" else check,
                                     model=checksum_model(identifier, response.model), publisher=publisher.name), publisher)


class SimulatedEndpoint:
    def __init__(self, bus, name, role, callback):
        self.bus, self.name, self.role, self.callback = bus, name, role, callback
        self.responses = {}

    def header(self, frame_id):
        self.bus.request(self, frame_id)

    def set_response(self, frame_id, response):
        if self.role == "monitor":
            raise ValueError("Monitor mode cannot publish responses.")
        if not 0 <= frame_id <= 61:
            raise ValueError("Response ID must be 00-3D.")
        if frame_id in (60, 61) and len(response.data) != 8:
            raise ValueError("LIN diagnostic frames 3C/3D require eight bytes.")
        lin_checksum(frame_id, response.data, response.model)
        with self.bus.lock:
            self.responses[frame_id] = Response(bytes(response.data), response.model)

    def remove_response(self, frame_id):
        with self.bus.lock:
            self.responses.pop(frame_id, None)

    def sleep(self):
        if self.role != "master":
            raise ValueError("Only the master can request bus sleep.")
        with self.bus.lock:
            self.bus.awake = False
            self.bus._clock = max(time.time(), self.bus._clock)
            self.bus._broadcast(LinFrame(self.bus._clock, 60, b"\x00" + b"\xff" * 7,
                                        kind="sleep", model="classic", publisher=self.name), self)

    def wake(self):
        if self.role == "monitor":
            raise ValueError("Monitor mode cannot transmit a wake-up pulse.")
        with self.bus.lock:
            self.bus.awake = True
            self.bus._clock = max(time.time(), self.bus._clock)
            self.bus._broadcast(LinFrame(self.bus._clock, 0, kind="wake", publisher=self.name), self)

    def close(self):
        with self.bus.lock:
            if self in self.bus.nodes:
                self.bus.nodes.remove(self)
