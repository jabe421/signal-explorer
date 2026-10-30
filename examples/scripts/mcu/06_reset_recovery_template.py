"""MCU TEMPLATE: query boot count, request reset, confirm new boot report."""
FIRMWARE_CONTRACT_CONFIRMED = False
se.check(FIRMWARE_CONTRACT_CONFIRMED, 'Review the MCU contract before TX')

def boot_report(transaction, timeout):
    for attempt in range(3):
        frame = se.can.recv(timeout, arbitration_id=0x502, extended=False)
        se.check(frame is not None, 'Boot report received')
        se.check(len(frame.data) == 8, 'Boot report length')
        if frame.data[0] == 0xB0 and frame.data[7] == transaction:
            return int.from_bytes(frame.data[1:3], 'little')
    raise RuntimeError('Boot report transaction did not match')

# Fictional command 03 queries the current retained boot counter.
se.can.send(0x500, [0x03, 0, 0, 0, 0, 0, 0, 1])
before = boot_report(1, 1.0)
# Fictional command 02 resets; firmware echoes transaction after restart.
se.can.send(0x500, [0x02, 0, 0, 0, 0, 0, 0, 2])
after = boot_report(2, 3.0)
se.check(after == ((before + 1) & 0xFFFF), 'One MCU reboot observed')
se.log(f'Boot count changed from {before} to {after}')

