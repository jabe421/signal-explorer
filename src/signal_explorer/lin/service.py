"""LIN session orchestration; logical simulator is the first transport."""
import math
import struct
from PySide6.QtCore import QObject, QTimer, QThread, Signal, Qt

from signal_explorer.lin.model import Response, ScheduleSlot, validate_schedule, lin_checksum
from signal_explorer.lin.simulation import SimulatedLinBus


class _NativeReader(QThread):
    failed = Signal(str)

    def __init__(self, endpoint):
        super().__init__()
        self.endpoint = endpoint

    def run(self):
        while not self.isInterruptionRequested():
            try:
                self.endpoint.poll()
            except Exception as exc:
                self.failed.emit(str(exc))
                return
            self.msleep(5)


class LinService(QObject):
    connected = Signal(str)
    disconnected = Signal()
    about_to_disconnect = Signal()
    message = Signal(object)
    traffic = Signal(object)
    error = Signal(str)
    schedule_changed = Signal(bool)
    fd_enabled = False  # Compatibility with protocol-neutral recording controls.

    def __init__(self, parent=None):
        super().__init__(parent)
        self.endpoint = None
        self.bus = None
        self.role = "monitor"
        self.baudrate = 19200
        self.lengths = {16: 2, 32: 4}
        self.models = {16: "enhanced", 32: "enhanced"}
        self.session_info = {}
        self.database = None
        self.configured_responses = {}
        self._schedule = QTimer(self)
        self._schedule.setSingleShot(True)
        self._schedule.setTimerType(Qt.TimerType.PreciseTimer)
        self._schedule.timeout.connect(self._run_slot)
        self._slots = []
        self._slot_index = 0
        self.schedule_running = False
        self._peers = []
        self._demo = QTimer(self)
        self._demo.setInterval(50)
        self._demo.timeout.connect(self._demo_tick)
        self._tick = 0
        self._reader = None

    @property
    def is_connected(self):
        return self.endpoint is not None

    @property
    def can_replay(self):
        return self.is_connected and self.role != "monitor"

    def set_database(self, database):
        if self.is_connected:
            raise ValueError("Disconnect LIN before changing the network description.")
        self.database = database
        self.lengths = {i: f.length for i, f in database.frames.items()}
        self.models = {i: database.model for i in self.lengths}

    def connect_simulated(self, role, baudrate, demo_peer=True, responses=None, bus=None):
        if self.is_connected:
            raise ValueError("Disconnect the current LIN session first.")
        if role not in {"master", "slave", "monitor"}:
            raise ValueError("Unknown LIN role.")
        if baudrate not in (2400, 4800, 9600, 10400, 19200, 20000):
            raise ValueError("Unsupported LIN baud rate.")
        self.role, self.baudrate = role, baudrate
        self.bus = bus or SimulatedLinBus(baudrate)
        self.endpoint = self.bus.attach("Signal Explorer", role, self._on_event)
        try:
            self.configured_responses = dict(responses or {})
            self.restore_responses()
            if demo_peer:
                if role != "master":
                    self._peers.append(self.bus.attach("DemoMaster", "master", lambda event: None))
                self._peers.append(self.bus.attach("DemoSlave", "slave", lambda event: None))
                self._demo_tick(poll=False)
                self._demo.start()
            self.session_info = dict(protocol="LIN", adapter="Simulated LIN", native_channel="Virtual LIN 1",
                                     role=role, bitrate=baudrate, simulated=True)
        except Exception:
            self.disconnect_bus()
            raise
        self.connected.emit(f"Simulated LIN | {role} | {baudrate} bit/s")

    def _on_event(self, event):
        self.traffic.emit(event)
        self.message.emit(event)

    def connect_hardware(self, adapter, config, role, baudrate, responses=None):
        if self.is_connected:
            raise ValueError('Disconnect the current LIN session first.')
        if role not in {'master', 'slave', 'monitor'} or not 1000 <= baudrate <= 20000:
            raise ValueError('Invalid LIN role or baud rate.')
        if adapter == 'peak':
            from signal_explorer.lin.peak import PeakLinTransport as transport
            label = 'PEAK PLIN'
        elif adapter == 'vector':
            from signal_explorer.lin.vector import VectorLinTransport as transport
            label = 'Vector XL LIN'
        elif adapter == 'neovi':
            from signal_explorer.lin.intrepid import IntrepidLinTransport as transport
            label = 'Intrepid ValueCAN4-2EL'
        else:
            raise ValueError('Unknown native LIN adapter.')
        self.role, self.baudrate, self.bus = role, baudrate, None
        self.configured_responses = dict(responses or {})
        try:
            self.endpoint = transport(config, role, baudrate, self.lengths, self.models, self._on_event)
            self.restore_responses()
            self.session_info = dict(protocol='LIN', adapter=label,
                                     native_channel=str(config['channel']), serial=config.get('serial'),
                                     role=role, bitrate=baudrate, simulated=False)
            self._reader = _NativeReader(self.endpoint)
            self._reader.failed.connect(self._native_failed)
            self._reader.start()
        except Exception:
            self.disconnect_bus()
            raise
        self.connected.emit(f'{label} | {role} | {baudrate} bit/s')

    def _native_failed(self, text):
        if self.sender() is not self._reader:
            return  # A queued failure from a closed session must not close a new one.
        self.disconnect_bus()
        self.error.emit(f'LIN receive stopped: {text}')

    def set_response(self, identifier, data, model="enhanced", remember=True):
        if not self.is_connected:
            raise ValueError("Connect LIN first.")
        if identifier not in self.lengths:
            raise ValueError("Define this ID's response length first.")
        if len(data) != self.lengths[identifier]:
            raise ValueError(f"ID {identifier:02X} requires {self.lengths[identifier]} bytes.")
        response = Response(bytes(data), model)
        self.endpoint.set_response(identifier, response)
        if remember:
            self.configured_responses[identifier] = response

    def remove_response(self, identifier):
        if self.endpoint:
            self.endpoint.remove_response(identifier)
        self.configured_responses.pop(identifier, None)

    def restore_responses(self):
        if not self.endpoint:
            return
        # Validate the complete table before changing any native response entry.
        for identifier, response in self.configured_responses.items():
            if self.role == 'monitor':
                raise ValueError('Monitor sessions cannot publish responses.')
            if not 0 <= identifier <= 61 or len(response.data) != self.lengths.get(identifier):
                raise ValueError('Configured response ID/length differs from the network definition.')
            lin_checksum(identifier, response.data, response.model)
            if identifier in (60, 61) and len(response.data) != 8:
                raise ValueError('Diagnostic responses require eight bytes.')
        try:
            for identifier in tuple(self.endpoint.responses):
                if identifier not in self.configured_responses:
                    self.endpoint.remove_response(identifier)
            for identifier, response in self.configured_responses.items():
                self.endpoint.set_response(identifier, response)
        except Exception:
            if self.bus is None:
                self.disconnect_bus()  # Do not leave a partly applied hardware table running.
            raise

    def send_header(self, identifier):
        if not self.endpoint:
            raise ValueError("Connect LIN first.")
        if identifier not in self.lengths:
            raise ValueError("Define the frame length before requesting it.")
        self.endpoint.header(identifier)

    def start_schedule(self, slots):
        if not self.is_connected or self.role != "master":
            raise ValueError("Only a connected LIN master can run a schedule.")
        validate_schedule(slots, self.lengths, self.baudrate)
        self._slots, self._slot_index = list(slots), 0
        self.schedule_running = True
        self.schedule_changed.emit(True)
        self._run_slot()

    def _run_slot(self):
        if not self.schedule_running:
            return
        slot = self._slots[self._slot_index]
        try:
            self.send_header(slot.frame_id)
        except Exception as exc:
            self.stop_schedule()
            self.error.emit(str(exc))
            return
        self._slot_index = (self._slot_index + 1) % len(self._slots)
        self._schedule.start(max(1, round(slot.delay_ms)))

    def stop_schedule(self):
        self._schedule.stop()
        self.schedule_running = False
        self.schedule_changed.emit(False)

    def send_message(self, operation, report_errors=True):
        """Playback sends headers or updates buffered responses according to role."""
        try:
            if not self.endpoint or operation[0] != self.role:
                raise ValueError("LIN role changed or session disconnected.")
            if self.role == "master":
                self.send_header(operation[1])
            elif self.role == "slave":
                self.set_response(operation[1], operation[2], operation[3], remember=False)
            else:
                raise ValueError("Monitor mode cannot transmit.")
            return True
        except Exception as exc:
            if report_errors:
                self.error.emit(str(exc))
            return False

    def _demo_tick(self, poll=True):
        if not self.endpoint:
            return
        self._tick += 1
        # Only unconditional frames can be supplied automatically from an LDF.
        for peer in self._peers:
            for identifier in self.lengths:
                if self.database and identifier not in self.database.frames:
                    continue
                if identifier in self.configured_responses:
                    peer.remove_response(identifier)
                    continue
                if self.database:
                    definition = self.database.frames[identifier]
                    owns = (definition.publisher.name == self.database.master) == (peer.role == "master")
                    data = self.database.initial_data(identifier)
                    if "EngineRPM" in {s.name for _, s in definition.signal_map}:
                        data = bytes(definition.encode_raw({"EngineRPM": 1800 + self._tick % 1000}))
                else:
                    owns = (identifier == 16) == (peer.role == "master")
                    data = bytes([25, 1]) if identifier == 16 else struct.pack('<HBB', 1800 + self._tick % 1000, 110, 25)
                if owns:
                    peer.set_response(identifier, Response(data, self.models[identifier]))
            if poll and peer.role == "master" and self.bus.awake:
                ids = list(self.lengths)
                if ids:
                    peer.header(ids[self._tick % len(ids)])

    def disconnect_bus(self):
        if self.endpoint:
            self.about_to_disconnect.emit()
        self.stop_schedule()
        self._demo.stop()
        if self._reader:
            self._reader.requestInterruption()
            self._reader.wait()
            self._reader = None
        for peer in self._peers:
            peer.close()
        self._peers.clear()
        endpoint, self.endpoint = self.endpoint, None
        try:
            if endpoint:
                endpoint.close()
        except Exception as exc:
            self.error.emit(f'LIN cleanup failed: {exc}')
        finally:
            self.bus = None
            self.disconnected.emit()
