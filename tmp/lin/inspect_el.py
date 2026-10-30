import ics
import time

devices=[d for d in ics.find_devices() if d.DeviceType==ics.NEODEVICE_VCAN42_EL and not d.NumberOfClients]
if not devices:
    raise SystemExit('No free ValueCAN4-2EL; no device opened.')
device=devices[0]
ics.open_device(device)
try:
    seen=errors=0
    deadline=time.monotonic()+2
    while time.monotonic()<deadline:
        messages,count=ics.get_messages(device,True,.1)
        errors+=count
        for m in messages:
            if m.Protocol==ics.SPY_PROTOCOL_LIN:
                seen+=1
                if seen<=3:
                    print(dict(header=m.Header,data=m.Data,ack=m.AckBytes,
                               status=hex(m.StatusBitField),status2=hex(m.StatusBitField2),
                               timestamp=ics.get_timestamp_for_msg(device,m)))
    print(f'LIN reports={seen}; API errors={errors}; no settings writes or transmit calls.')
finally:
    ics.close_device(device)
