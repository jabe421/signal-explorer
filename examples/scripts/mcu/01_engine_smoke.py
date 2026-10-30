"""Built-in Virtual CAN: decode five EngineData frames."""
se.check(se.is_simulated, 'Use Virtual CAN with demo traffic enabled')
for sample in range(5):
    frame = se.can.recv(2.0, arbitration_id=0x200, extended=False)
    se.check(frame is not None, 'EngineData arrived')
    se.check(len(frame.data) == 8, 'Expected eight bytes')
    # Little-endian unsigned 16-bit values, scaled as in demo.dbc.
    rpm = int.from_bytes(frame.data[0:2], 'little') * 0.25
    maf = int.from_bytes(frame.data[2:4], 'little') * 0.01
    o2 = int.from_bytes(frame.data[4:6], 'little') * 0.001
    se.check(800 <= rpm <= 2800, 'RPM within demo limits')
    se.check(10 <= maf <= 50, 'MAF within demo limits')
    se.check(0.10 <= o2 <= 0.80, 'O2 within demo limits')
    se.log(f'Sample {sample + 1}: {rpm:.0f} rpm, {maf:.2f} g/s')

