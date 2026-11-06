"""Take ownership of a CAN frame without recursive copying of scalar fields."""
import can


_FIELDS = ('timestamp', 'arbitration_id', 'is_extended_id', 'is_remote_frame',
           'is_error_frame', 'channel', 'dlc', 'is_fd', 'is_rx', 'bitrate_switch',
           'error_state_indicator')


class ObservedMessage(can.Message):
    """CAN data plus observation provenance, without changing vendor APIs."""
    __slots__ = ('timestamp_source', 'raw_timestamp', 'tx_confirmation')

    def __init__(self, message, *, copy_data=True):
        super().__init__(**{name: getattr(message, name) for name in _FIELDS},
                         data=bytearray(message.data) if copy_data else message.data)
        self.timestamp_source = getattr(message, 'timestamp_source', 'unspecified')
        self.raw_timestamp = getattr(message, 'raw_timestamp', message.timestamp)
        self.tx_confirmation = getattr(message, 'tx_confirmation', 'none')

    def __copy__(self):
        return ObservedMessage(self, copy_data=False)

    def __deepcopy__(self, memo):
        return ObservedMessage(self)


def snapshot_message(message):
    return ObservedMessage(message)
