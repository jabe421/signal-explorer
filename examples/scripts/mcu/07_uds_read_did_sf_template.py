"""MCU TEMPLATE: Classic CAN, normal-addressed ISO-TP single frames only."""
FIRMWARE_CONTRACT_CONFIRMED = False
se.check(FIRMWARE_CONTRACT_CONFIRMED, 'Review IDs/DID and ECU timing first')
# Example physical request/response IDs and a fictional two-byte DID.
TX_ID, RX_ID = 0x700, 0x708
DID = 0x1234
# ISO-TP length=3, then UDS ReadDataByIdentifier and its two DID bytes.
se.can.send(TX_ID, [3, 0x22, DID >> 8, DID & 0xFF, 0, 0, 0, 0])
for attempt in range(4):
    frame = se.can.recv(1.0, arbitration_id=RX_ID, extended=False)
    se.check(frame is not None, 'UDS response received')
    se.check(not frame.is_fd and len(frame.data) >= 2, 'Classic CAN data')
    pci = frame.data[0]
    se.check(pci >> 4 == 0, 'Single Frame required; no multi-frame support')
    length = pci & 0x0F
    se.check(1 <= length <= 7 and length < len(frame.data), 'SF length')
    pdu = frame.data[1:1 + length]
    if pdu[0] == 0x7F:
        se.check(len(pdu) == 3 and pdu[1] == 0x22, 'Matching negative reply')
        if pdu[2] == 0x78:
            se.log('Response pending; wait again without resending')
            continue
        raise RuntimeError(f'UDS negative response: NRC 0x{pdu[2]:02X}')
    expected = bytes([0x62, DID >> 8, DID & 0xFF])
    se.check(len(pdu) == 5 and pdu[:3] == expected, 'Two-byte DID response')
    value = int.from_bytes(pdu[3:5], 'big')
    se.log(f'DID 0x{DID:04X} value = 0x{value:04X}')
    break
else:
    raise RuntimeError('Response stayed pending beyond four receive waits')
# This teaching example is not a complete ISO-TP/UDS client or VIN reader.

