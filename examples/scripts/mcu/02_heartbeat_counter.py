"""Built-in Virtual CAN: monitor counter continuity and period."""
se.check(se.is_simulated, 'Use Virtual CAN with demo traffic enabled')
previous = None
for sample in range(8):
    frame = se.can.recv(2.0, arbitration_id=0x100, extended=False)
    se.check(frame is not None, 'Heartbeat received')
    se.check(len(frame.data) == 8, 'Heartbeat length')
    if previous is not None:
        # Modulo arithmetic accepts the FF -> 00 rollover.
        delta = (frame.data[0] - previous.data[0]) & 0xFF
        se.check(delta == 1, 'No missing or repeated demo counter')
        period = frame.timestamp - previous.timestamp
        # Broad simulation tolerance, not a production timing limit.
        se.check(0.02 <= period <= 0.50, 'Demo heartbeat period')
    previous = frame
se.log('Eight consecutive heartbeat frames checked')

