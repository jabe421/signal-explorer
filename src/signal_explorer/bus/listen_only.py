"""Backend-specific silent connection setup; unsupported paths never fall back."""
import ctypes
import can
from signal_explorer.bus.timing import SILENT_INTERFACES


def open_bus(*, monitor_only=False, **kwargs):
    interface = kwargs['interface']
    if not monitor_only:
        return can.Bus(**kwargs)
    if interface not in SILENT_INTERFACES:
        raise ValueError('Hardware listen-only setup is not supported by this backend. No channel was opened.')
    if interface == 'virtual':
        return can.Bus(**kwargs)
    args = dict(kwargs)
    args.pop('interface'); args.pop('ignore_config', None)
    if interface == 'vector':
        from can.interfaces.vector.canlib import VectorBus

        class SilentVectorBus(VectorBus):
            def _set_output_mode(self, channel_mask, listen_only):
                if listen_only and channel_mask & self.permission_mask != channel_mask:
                    self.xldriver.xlClosePort(self.port_handle)
                    self._is_shutdown = True
                    raise can.CanInitializationError('Listen-only requires initialization access to every selected Vector channel. Close other clients.')
                super()._set_output_mode(channel_mask, listen_only)

        return SilentVectorBus(**args, listen_only=True)
    if interface == 'pcan':
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
    if interface == 'kvaser':
        from can.interfaces.kvaser import canlib, constants
        caps = ctypes.c_uint32()
        canlib.canGetChannelData(int(args['channel']), constants.canCHANNELDATA_CHANNEL_CAP,
                                ctypes.byref(caps), ctypes.sizeof(caps))
        if not caps.value & 0x00200000:  # CANlib canCHANNEL_CAP_SILENT_MODE
            raise can.CanInitializationError('This Kvaser channel does not report silent-mode capability.')
        return can.Bus(**kwargs, driver_mode=False, exclusive=True)
    # NI configures can_lstn_only before starting either stream session.
    bus = can.Bus(**kwargs, listen_only=True)
    try:
        if not bus._interface.can_lstn_only:
            raise can.CanInitializationError('NI-XNET did not confirm listen-only mode.')
    except Exception:
        bus.shutdown()
        raise
    return bus
