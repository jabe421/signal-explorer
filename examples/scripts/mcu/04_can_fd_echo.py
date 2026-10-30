"""Built-in Virtual CAN FD: 32-byte telemetry and eight-byte FD echo."""
se.check(se.is_simulated, 'Use Virtual CAN with demo traffic enabled')
se.check(se.session.get('fd'), 'Enable CAN FD before connecting')
telemetry = se.can.recv(2.0, arbitration_id=0x300, extended=False)
se.check(telemetry is not None, 'FD telemetry arrived')
se.check(telemetry.is_fd and len(telemetry.data) == 32, '32-byte FD frame')

# The built-in echo peer accepts exactly eight bytes, including in FD mode.
payload = bytes(range(8))
se.can.send(0x321, payload, fd=True, brs=True)
reply = se.can.recv(2.0, arbitration_id=0x221, extended=False)
se.check(reply is not None, 'FD echo response arrived')
se.check(reply.is_fd and reply.bitrate_switch, 'FD/BRS flags retained')
se.check(reply.data == payload, 'Eight-byte FD payload echoed')

