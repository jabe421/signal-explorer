"""Host-side transport boundary. Scripts never acquire native adapter handles.

Another protocol can implement this interface without changing the editor,
process control, RPC framing, timers or test output. Only CAN is exposed today.
"""
from abc import abstractmethod
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

    def receive_matcher(self, parameters):
        return lambda event: self.matches(event, parameters)

    def capture_filter(self, filters):
        if filters:
            raise ValueError('Capture filters are not available for this transport.')
        return lambda event: True


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
        message = event  # The service owns the snapshot; encoding never mutates it.
        return dict(protocol=self.protocol, timestamp=message.timestamp,
                    channel=str(message.channel), direction='rx' if message.is_rx else 'tx',
                    arbitration_id=message.arbitration_id, data=bytes(message.data).hex(),
                    is_extended_id=message.is_extended_id, is_fd=message.is_fd,
                    bitrate_switch=message.bitrate_switch, is_error_frame=message.is_error_frame,
                    is_remote_frame=message.is_remote_frame,
                    timestamp_source=getattr(message, 'timestamp_source', 'unspecified'),
                    raw_timestamp=getattr(message, 'raw_timestamp', None),
                    tx_confirmation=getattr(message, 'tx_confirmation', 'none'))

    def _message(self, parameters):
        message = can.Message(arbitration_id=parameters['arbitration_id'],
                              channel=parameters.get('channel'),
                              data=bytes.fromhex(parameters['data']),
                              is_extended_id=parameters.get('extended', False),
                              is_fd=parameters.get('fd', False),
                              bitrate_switch=parameters.get('brs', False), check=True)
        return message

    def send_async(self, parameters):
        return self.service.submit_message(self._message(parameters))

    def send(self, parameters):
        message = self._message(parameters)
        # Keep the service's FD validation and its single RX/TX/logging path.
        timestamp = self.service.send_message(message, report_errors=False, return_timestamp=True)
        if timestamp is False:
            raise RuntimeError('CAN send failed. Check the connection, FD settings and adapter.')
        return timestamp

    def _channel(self, channel):
        if channel is None:
            return None
        selection = next((c for c in self.service.channels if str(channel) in
                          (str(c['native_channel']), c['label'])), None)
        if selection is None:
            raise ValueError('Script channel is not enabled: '+str(channel))
        return str(selection['native_channel'])

    def receive_matcher(self, parameters):
        identifier = parameters.get('arbitration_id')
        if identifier is not None and (type(identifier) is not int or not 0 <= identifier <= 0x1fffffff):
            raise ValueError('Receive ID must be a CAN identifier or None.')
        extended = parameters.get('extended')
        if extended is not None and type(extended) is not bool:
            raise ValueError('extended must be True, False or None.')
        direction = parameters.get('direction', 'rx')
        if direction not in ('rx', 'tx', 'all'):
            raise ValueError('Direction must be rx, tx or all.')
        channel = self._channel(parameters.get('channel'))
        return lambda event: (not event.get('is_error_frame') and not event.get('is_remote_frame')
                              and (channel is None or event['channel'] == channel)
                              and (identifier is None or event['arbitration_id'] == identifier)
                              and (extended is None or event['is_extended_id'] == extended)
                              and (direction == 'all' or event['direction'] == direction))

    def capture_filter(self, filters):
        if filters is not None and not isinstance(filters, list):
            raise ValueError('CAN filters must be a list of filter dictionaries, or None.')
        if filters and len(filters) > 256:
            raise ValueError('At most 256 capture filters are supported.')
        compiled = []
        for spec in filters or []:
            if not isinstance(spec, dict) or set(spec) - {'can_id', 'can_mask', 'extended', 'direction', 'channel'}:
                raise ValueError('Invalid CAN capture filter fields.')
            identifier, mask = spec.get('can_id'), spec.get('can_mask')
            if any(type(v) is not int or not 0 <= v <= 0x1fffffff for v in (identifier, mask)):
                raise ValueError('Capture filters require valid can_id and can_mask integers.')
            match = self.receive_matcher({key: value for key, value in spec.items()
                                          if key not in ('can_id', 'can_mask')} | {'direction': spec.get('direction', 'all')})
            compiled.append((identifier, mask, match))
        def accepts(event):
            if event.get('is_error_frame') or event.get('is_remote_frame'):
                return False
            return not compiled or any((event['arbitration_id'] & mask) == (identifier & mask) and match(event)
                                       for identifier, mask, match in compiled)
        return accepts

    def matches(self, event, parameters):
        channel = parameters.get('channel')
        if channel is not None:
            selection = next((c for c in self.service.channels if str(channel) in (str(c['native_channel']), c['label'])), None)
            if selection is None or str(event['channel']) != str(selection['native_channel']):
                return False
        if event.get('is_error_frame') or event.get('is_remote_frame'): return False
        identifier = parameters.get('arbitration_id')
        if identifier is not None and event['arbitration_id'] != identifier:return False
        extended = parameters.get('extended')
        if extended is not None and event['is_extended_id'] != extended:return False
        direction = parameters.get('direction', 'rx')
        return direction == 'all' or event['direction'] == direction
