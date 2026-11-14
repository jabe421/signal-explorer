"""Native silent-mode implementations, selected by the backend strategy."""
import ctypes
import can
from signal_explorer.bus.strategies import strategy_for


def open_bus(*, monitor_only=False, **kwargs):
    strategy = strategy_for(kwargs['interface'])
    if not monitor_only:
        return strategy.open_active(kwargs)
    if not strategy.listen_only:
        raise ValueError('Hardware listen-only setup is not supported by this backend. No channel was opened.')
    return strategy.open_silent(kwargs)


def _native_args(kwargs):
    args = dict(kwargs)
    args.pop('interface')
    args.pop('ignore_config', None)
    return args


def open_vector_silent(kwargs):
    args = _native_args(kwargs)
    from can.interfaces.vector.canlib import VectorBus

    class SilentVectorBus(VectorBus):
        def _set_output_mode(self, channel_mask, listen_only):
            if listen_only and channel_mask & self.permission_mask != channel_mask:
                self.xldriver.xlClosePort(self.port_handle)
                self._is_shutdown = True
                raise can.CanInitializationError('Listen-only requires initialization access to every selected Vector channel. Close other clients.')
            super()._set_output_mode(channel_mask, listen_only)

    return SilentVectorBus(**args, listen_only=True)


def open_peak_silent(kwargs):
    args = _native_args(kwargs)
    from can.interfaces.pcan.pcan import PcanBus
    from can.interfaces.pcan.basic import PCAN_LISTEN_ONLY, PCAN_PARAMETER_ON, PCAN_ERROR_OK

    class SilentPcanBus(PcanBus):
        @PcanBus.state.setter
        def state(self, value):
            # python-can's setter ignores this native status. Check the
            # preset before Initialize/InitializeFD can put the bus online.
            result = self.m_objPCANBasic.SetValue(self.m_PcanHandle, PCAN_LISTEN_ONLY, PCAN_PARAMETER_ON)
            if result != PCAN_ERROR_OK:
                raise can.CanInitializationError(f'PCAN refused listen-only preset: {result}')
            self._state = value

    return SilentPcanBus(**args, state=can.BusState.PASSIVE)


def open_kvaser_silent(kwargs):
    args = _native_args(kwargs)
    from can.interfaces.kvaser import canlib, constants
    caps = ctypes.c_uint32()
    canlib.canGetChannelData(int(args['channel']), constants.canCHANNELDATA_CHANNEL_CAP,
                            ctypes.byref(caps), ctypes.sizeof(caps))
    if not caps.value & 0x00200000:  # CANlib canCHANNEL_CAP_SILENT_MODE
        raise can.CanInitializationError('This Kvaser channel does not report silent-mode capability.')
    return can.Bus(**kwargs, driver_mode=False, exclusive=True)


def open_ni_silent(kwargs):
    # NI configures can_lstn_only before starting either stream session.
    bus = can.Bus(**kwargs, listen_only=True)
    try:
        if not bus._interface.can_lstn_only:
            raise can.CanInitializationError('NI-XNET did not confirm listen-only mode.')
    except Exception:
        bus.shutdown()
        raise
    return bus
