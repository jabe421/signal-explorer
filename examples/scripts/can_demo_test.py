"""Continuous CAN echo test - run inside Signal Explorer's Script Editor.

1. Select Virtual (no hardware) in CAN / CAN FD.
2. Enable Inject demo traffic, then Connect.
3. Open this example and press Run. Pause/Resume or Stop in the editor.

IDs below are hexadecimal. The virtual peer echoes 321 as 221 with eight bytes.
The se context uses the existing connection; do not open another can.Bus here.
"""

# Refuse to send this simulation example on physical hardware.
se.check(se.is_simulated, 'This example requires Virtual CAN')
se.log('Starting 321 -> 221 echo loop. Press Stop to finish.')

# Each byte increments independently, wrapping from FF to 00.
payload = bytes(range(8))
cycle = 0

while True:
    # Classic eight-byte frames also work on an FD-enabled virtual connection.
    # Both TX and the peer RX response appear in the monitor and active logging.
    se.can.send(0x321, payload)
    response = se.can.recv(timeout=2.0, arbitration_id=0x221,
                           extended=False, direction='rx')
    se.check(response is not None,
             '221 received within two seconds (enable Inject demo traffic)')
    se.check(len(response.data) == 8 and response.data == payload,
             '221 echoes all eight request bytes')
    cycle += 1
    se.log(f'Cycle {cycle}: TX 321 / RX 221 = {payload.hex(" ").upper()}')

    # Only advance after a verified response. Stop interrupts receive or sleep;
    # Pause freezes script operations while ordinary demo traffic keeps running.
    payload = bytes((value + 1) & 0xFF for value in payload)
    se.sleep(100)
