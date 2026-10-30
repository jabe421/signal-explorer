"""Optional NI-XNET enumeration; scanning never opens a bus session."""
from signal_explorer.bus.discovery import ChannelOption


def discover_ni_channels():
    try:
        from nixnet import constants
        from nixnet.system import System
    except ImportError:
        return [], ('NI Python API unavailable. Install with: '
                    '.venv\\Scripts\\python.exe -m pip install ".[nixnet]". '
                    'Also install the 64-bit NI-XNET driver separately.')
    except Exception as exc:
        return [], f"NI-XNET API unavailable: {exc}. Check the NI-XNET driver installation."

    try:
        options = []
        with System() as system:
            devices = {}
            for device in system.dev_refs:
                for port in device.intf_refs:
                    devices[str(port)] = (device.product_name, str(device.ser_num))
            for port in system.intf_refs_can:
                # Only the high-speed/FD physical layer is integrated here.
                if port.can_tcvr_cap != constants.CanTcvrCap.HS:
                    continue
                channel = str(port)
                name, serial = devices.get(channel, ("NI-XNET", "?"))
                options.append(ChannelOption(
                    f"{name} | serial {serial} | {channel} | port {port.port_num}",
                    dict(channel=channel, supports_fd=True,
                         can_term_available=port.can_term_cap == constants.CanTermCap.YES)))
        return options, (f"{len(options)} NI-XNET high-speed CAN/FD channels detected."
                         if options else "NI-XNET API loaded; no high-speed CAN channels detected. "
                         "Check USB and NI MAX, then Refresh devices.")
    except Exception as exc:
        return [], (f"NI-XNET scan failed: {exc}. Check the 64-bit NI-XNET driver "
                    "and verify the device in NI MAX.")
