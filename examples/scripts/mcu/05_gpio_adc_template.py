"""MCU TEMPLATE: requires the guide's fictional command/response firmware."""
FIRMWARE_CONTRACT_CONFIRMED = False
se.check(FIRMWARE_CONTRACT_CONFIRMED, 'Review the MCU contract before TX')

def set_output(state, transaction):
    # 500: opcode, output state, five reserved bytes, transaction.
    request = bytes([0x01, state, 0, 0, 0, 0, 0, transaction])
    se.can.send(0x500, request)
    for attempt in range(10):
        reply = se.can.recv(0.2, arbitration_id=0x501, extended=False)
        if reply is None:
            continue
        se.check(len(reply.data) == 8, 'MCU response has eight bytes')
        if reply.data[7] == transaction and reply.data[0] == 0x81:
            return reply
    raise RuntimeError('No matching GPIO acknowledgement in ten waits')

for transaction, state in enumerate((0, 1, 0), start=1):
    reply = set_output(state, transaction)
    se.check(reply.data[1] == state, 'Commanded output state acknowledged')
    adc = int.from_bytes(reply.data[2:4], 'little')
    se.check(0 <= adc <= 4095, 'ADC fits a 12-bit reading')
    # Assumes a known midpoint stimulus on the independent ADC input.
    se.check(1000 <= adc <= 3000, 'ADC within this fixture acceptance band')
    voltage = adc * 3.3 / 4095
    se.log(f'Output={state}, ADC={adc}, nominal voltage={voltage:.3f} V')
    se.sleep(100)
# Stop does not reset MCU outputs. Use firmware timeout/manual cleanup too.

