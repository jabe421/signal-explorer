"""Built-in Virtual CAN: boundary and walking-bit payload tests."""
se.check(se.is_simulated, 'Use Virtual CAN with demo traffic enabled')
vectors = [bytes(8), bytes([0xFF] * 8), bytes([0xAA, 0x55] * 4)]
vectors += [bytes([1 << bit] * 8) for bit in range(8)]
for index, payload in enumerate(vectors):
    se.can.send(0x321, payload)
    reply = se.can.recv(2.0, arbitration_id=0x221, extended=False)
    se.check(reply is not None, 'Echo response received')
    se.check(reply.data == payload, f'Eight-byte vector {index + 1}')
    se.log(f'321 -> 221: {payload.hex(" ").upper()}')
    se.sleep(100)  # Milliseconds; Pause/Stop interrupt this wait.

