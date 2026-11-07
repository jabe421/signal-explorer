"""CAN FD policy shared by the connection service and message editor."""
from signal_explorer.bus.strategies import interfaces_supporting, strategy_for

FD_INTERFACES = interfaces_supporting('supports_fd')
FD_LENGTHS = (*range(9), 12, 16, 20, 24, 32, 48, 64)
DATA_BITRATES = (500000, 1000000, 2000000, 4000000, 5000000, 8000000)


def fd_configuration(interface, bitrate, data_bitrate):
    if interface not in FD_INTERFACES:
        raise ValueError("CAN FD is not enabled for this backend. Use Classic CAN or a supported FD adapter.")
    if data_bitrate < bitrate:
        raise ValueError("Data bitrate must be at least the arbitration bitrate.")
    return strategy_for(interface).fd_configuration(bitrate, data_bitrate)


def validate_frame(msg, fd_enabled):
    if msg.dlc != len(msg.data) and not msg.is_remote_frame:
        raise ValueError("Payload length does not match the message length.")
    if msg.is_fd and not fd_enabled:
        raise ValueError("Connect with CAN FD enabled before sending an FD frame.")
    if msg.is_fd and (msg.is_remote_frame or msg.dlc not in FD_LENGTHS):
        raise ValueError("CAN FD payload length must be 0-8, 12, 16, 20, 24, 32, 48 or 64 bytes; remote frames are not supported.")
    if not msg.is_fd and (len(msg.data) > 8 or msg.bitrate_switch or msg.error_state_indicator):
        raise ValueError("Classic CAN supports at most 8 bytes and cannot use BRS or ESI.")
