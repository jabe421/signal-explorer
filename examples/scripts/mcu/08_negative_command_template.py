"""MCU TEMPLATE: verify a documented unsupported-opcode response."""
FIRMWARE_CONTRACT_CONFIRMED = False
se.check(FIRMWARE_CONTRACT_CONFIRMED, 'Review the MCU contract before TX')
transaction = 9
# 7E is reserved as unsupported in this fictional firmware protocol.
se.can.send(0x500, [0x7E, 0, 0, 0, 0, 0, 0, transaction])
for attempt in range(5):
    reply = se.can.recv(0.2, arbitration_id=0x501, extended=False)
    if reply is None:
        continue
    se.check(len(reply.data) == 8, 'Negative response length')
    if reply.data[7] == transaction:
        se.check(reply.data[:2] == bytes([0xFF, 0x11]), 'Unsupported opcode')
        break
else:
    raise RuntimeError('No matching rejection response')
# FF/11 here is our fictional application response, not a UDS packet.

