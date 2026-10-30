"""Common guards for native LIN transports; no vendor imports at startup."""
from threading import RLock
from signal_explorer.lin.model import checksum_model, lin_checksum


class NativeLinTransport:
    supports_wake = True

    def __init__(self, role, lengths, models, callback):
        if role not in {'master', 'slave', 'monitor'}:
            raise ValueError('Unknown LIN role.')
        self.role, self.lengths, self.models = role, dict(lengths), dict(models)
        self.callback, self.responses = callback, {}
        self._lock, self._closed = RLock(), False

    def active(self, role=None):
        if self._closed:
            raise RuntimeError('LIN session is closed.')
        if role and self.role != role:
            raise ValueError(f'This operation requires LIN {role} mode.')

    def validate_response(self, identifier, response):
        self.active()
        if self.role == 'monitor':
            raise ValueError('Monitor sessions cannot publish responses.')
        if not 0 <= identifier <= 61 or len(response.data) != self.lengths.get(identifier):
            raise ValueError('Response ID/length differs from the LIN definition.')
        if identifier in (60, 61) and len(response.data) != 8:
            raise ValueError('Diagnostic responses require eight bytes.')
        lin_checksum(identifier, response.data, response.model)

    def validate_header(self, identifier):
        self.active('master')
        if not 0 <= identifier <= 61 or identifier not in self.lengths:
            raise ValueError('Define the response length for ID 00-3D first.')

    def model(self, identifier):
        return checksum_model(identifier, self.models.get(identifier, 'enhanced'))
