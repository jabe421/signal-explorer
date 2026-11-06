"""Read-only CAN diagnostics. Never open another device or drain the RX FIFO."""
import ctypes
import os
from functools import lru_cache


def query_health(bus, interface):
    if interface == 'pcan':
        status = int(bus.status())
        # Other non-bus status bits must not be rendered as a healthy bus.
        state = ('Bus off' if status & 0x10 else 'Error passive' if status & 0x40000 else
                 'Error warning' if status & 0xC else 'Status fault' if status & ~0x63 else 'No bus-error flag')
        return dict(state=state, status_hex=f'0x{status:X}', overrun_flag=bool(status & 0x42),
                    source='PCAN GetStatus; numeric TEC/REC unavailable')
    if interface == 'nixnet':
        comm = bus._session_send.can_comm
        state = comm.state.name
        return dict(state=state.replace('_',' ').title(),
                    tec=None if state=='BUS_OFF' else int(comm.tx_err_count), rec=int(comm.rx_err_count),
                    last_error=comm.last_err.name, source='NI-XNET CAN communication state')
    if interface == 'kvaser':
        return kvaser_health(bus)
    if interface == 'vector':
        # The normal reader delivers the response through the public event hook.
        bus.xldriver.xlCanRequestChipState(bus.port_handle, bus.mask)
        return None
    return dict(source='Controller diagnostics unavailable for this backend')


def kvaser_health(bus):
    result = dict(source='CANlib reported (error counters may be driver estimates)',
                  tec=None, rec=None, overruns=None, state=None, driver_load=None)
    notes = []
    try:
        library = kvaser_library()
        error_counters = library.canReadErrorCounters
        error_counters.argtypes = [ctypes.c_int, ctypes.POINTER(ctypes.c_uint),
                                   ctypes.POINTER(ctypes.c_uint), ctypes.POINTER(ctypes.c_uint)]
        error_counters.restype = ctypes.c_int
        tec, rec, overruns = ctypes.c_uint(), ctypes.c_uint(), ctypes.c_uint()
        status = error_counters(bus._write_handle, ctypes.byref(tec), ctypes.byref(rec), ctypes.byref(overruns))
        if status < 0: raise RuntimeError(f'canReadErrorCounters status {status}')
        result.update(tec=tec.value, rec=rec.value, overruns=overruns.value)
        read_status = library.canReadStatus
        read_status.argtypes = [ctypes.c_int, ctypes.POINTER(ctypes.c_ulong)]
        read_status.restype = ctypes.c_int
        flags = ctypes.c_ulong()
        status = read_status(bus._write_handle, ctypes.byref(flags))
        if status < 0: raise RuntimeError(f'canReadStatus status {status}')
        result['state'] = ('Bus off' if flags.value & 2 else 'Error passive' if flags.value & 1 else
                           'Error warning' if flags.value & 4 else 'Error active' if flags.value & 8 else 'Not reported')
        if flags.value & 2: result['tec'] = None
    except Exception as exc:
        notes.append(str(exc))
    try:
        stats = bus.get_stats()
        result['driver_load'] = stats.bus_load/100 if 0<=stats.bus_load<=10000 else None
        result['statistics_overruns'] = stats.overruns
    except Exception as exc:
        notes.append(str(exc))
    result['note'] = '; '.join(notes)
    return result


@lru_cache(maxsize=1)
def kvaser_library():
    return ctypes.WinDLL('canlib32') if os.name=='nt' else ctypes.CDLL('libcanlib.so')


def install_vector_events(bus, health, generation):
    if not all(hasattr(bus, name) for name in ('handle_can_event', 'handle_canfd_event', 'index_to_channel')):
        return
    old_can, old_fd = bus.handle_can_event, bus.handle_canfd_event

    def event_callback(event, fd):
        tag = int(event.tag)
        index = event.chanIndex
        channel = bus.index_to_channel.get(index)
        if channel is None: return
        if tag == (1033 if fd else 4):
            chip = event.tagData.canChipState if fd else event.tagData.chipState
            flags = int(chip.busStatus)
            state = ('Bus off' if flags & 1 else 'Error passive' if flags & 2 else
                     'Error warning' if flags & 4 else 'Error active' if flags & 8 else 'Not reported')
            health.native_update(channel, dict(state=state,
                tec=None if flags & 1 else int(chip.txErrorCounter), rec=int(chip.rxErrorCounter),
                source='Vector XL chip-state event'), generation)
        elif fd and tag in (1025, 1026):
            health.native_update(channel, dict(last_error=f'Vector FD error event, code {int(event.tagData.canError.errorCode)}',
                                               source='Vector XL CAN FD event'), generation, error_event=True)

    def classic(event):
        event_callback(event, False); old_can(event)

    def fd(event):
        event_callback(event, True); old_fd(event)

    bus.handle_can_event, bus.handle_canfd_event = classic, fd
