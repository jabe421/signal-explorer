"""Take ownership of a CAN frame without recursive copying of scalar fields."""
from copy import copy


def snapshot_message(message):
    frame = copy(message)
    # python-can's shallow copy deliberately shares its mutable bytearray.
    frame.data = bytearray(message.data)
    return frame
