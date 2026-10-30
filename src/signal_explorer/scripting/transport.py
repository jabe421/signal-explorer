"""Host-side transport boundary. Scripts never acquire native adapter handles.

Another protocol can implement this interface without changing the editor,
process control, RPC framing, timers or test output. Only CAN is exposed today.
"""
from abc import abstractmethod
from copy import copy
from PySide6.QtCore import QObject, Signal, Qt
import can


class ScriptTransport(QObject):
    traffic = Signal(object)
    about_to_disconnect = Signal()
    connection_changed = Signal()
    protocol = ''

    @property
    @abstractmethod
    def is_connected(self): ...

    @abstractmethod
    def session(self): ...

    @abstractmethod
    def encode(self, event): ...

    @abstractmethod
    def send(self, parameters): ...

    @abstractmethod
    def matches(self, event, parameters): ...


class CanScriptTransport(ScriptTransport):
    protocol = 'CAN'

    def __init__(self, service, parent=None):
        super().__init__(parent)
        self.service = service
        service.traffic.connect(self.traffic.emit, Qt.ConnectionType.DirectConnection)
        service.about_to_disconnect.connect(self.about_to_disconnect.emit)
        service.connected.connect(lambda *_:self.connection_changed.emit())
        service.disconnected.connect(self.connection_changed.emit)

    @property
    def is_connected(self):
        return self.service.is_connected

    def session(self):
        return {**self.service.session_info, 'protocol': self.protocol,
                'simulated': self.service.session_info.get('interface') == 'virtual'}

    def encode(self, event):
        message = copy(event)
        return dict(protocol=self.protocol, timestamp=message.timestamp,
                    channel=str(message.channel), direction='rx' if message.is_rx else 'tx',
                    arbitration_id=message.arbitration_id, data=bytes(message.data).hex(),
                    is_extended_id=message.is_extended_id, is_fd=message.is_fd,
                    bitrate_switch=message.bitrate_switch, is_error_frame=message.is_error_frame,
                    is_remote_frame=message.is_remote_frame)

    def send(self, parameters):
        message = can.Message(arbitration_id=parameters['arbitration_id'],
                              data=bytes.fromhex(parameters['data']),
                              is_extended_id=parameters.get('extended', False),
                              is_fd=parameters.get('fd', False),
                              bitrate_switch=parameters.get('brs', False), check=True)
        # Keep the service's FD validation and its single RX/TX/logging path.
        if not self.service.send_message(message, report_errors=False):
            raise RuntimeError('CAN send failed. Check the connection, FD settings and adapter.')
        return True

    def matches(self, event, parameters):
        if event.get('is_error_frame') or event.get('is_remote_frame'): return False
        identifier = parameters.get('arbitration_id')
        if identifier is not None and event['arbitration_id'] != identifier:return False
        extended = parameters.get('extended')
        if extended is not None and event['is_extended_id'] != extended:return False
        direction = parameters.get('direction', 'rx')
        return direction == 'all' or event['direction'] == direction
