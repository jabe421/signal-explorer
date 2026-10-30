"""Built-in Virtual CAN: demonstrate a controlled receive timeout."""
se.check(se.is_simulated, 'Use a private Virtual CAN demo session')
# The built-in demo does not emit 7FF. This sends no frames.
frame = se.can.recv(0.2, arbitration_id=0x7FF, extended=False)
se.check(frame is None, 'Expected no 7FF response in the demo')
se.log('Timeout handling passed; this does not diagnose an ECU fault')

