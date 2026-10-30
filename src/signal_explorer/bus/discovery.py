"""Vendor enumeration is optional; unavailable SDKs never disable other adapters."""
from dataclasses import dataclass
import os
from pathlib import Path
import struct

from PySide6.QtCore import QSettings


_dll_handles = []


def prepare_vector_library():
    """Expose an installed XL DLL to python-can before its first vendor import."""
    if os.name != "nt":
        return
    configured = os.environ.get("SIGNAL_EXPLORER_VECTOR_DLL") or QSettings().value("drivers/vector_dll", "")
    filename = "vxlapi64.dll" if struct.calcsize("P") == 8 else "vxlapi.dll"
    candidates = [Path(configured)] if configured else []
    public = Path(os.environ.get("PUBLIC", "C:/Users/Public")) / "Documents/Vector"
    if public.exists():
        candidates.extend(public.glob(f"XL Driver Library*/bin/{filename}"))
    for path in candidates:
        if path.is_file() and path.name.lower() == filename:
            directory = str(path.resolve().parent)
            if directory not in os.environ.get("PATH", "").split(os.pathsep):
                os.environ["PATH"] = directory + os.pathsep + os.environ.get("PATH", "")
                _dll_handles.append(os.add_dll_directory(directory))
            return


@dataclass(frozen=True)
class ChannelOption:
    label: str
    config: dict


def discover_channels(interface):
    """Return selectable configurations and an actionable status message."""
    try:
        if interface == "nixnet":
            from signal_explorer.bus.ni import discover_ni_channels
            return discover_ni_channels()
        if interface in {"kvaser", "pcan"}:
            if interface == "kvaser":
                from can.interfaces.kvaser import canlib
                if canlib.__dict__.get("__canlib") is None:
                    return [], "Kvaser CANlib API missing. Install the 64-bit Kvaser drivers."
                # Refresh CANlib's channel list after USB hotplug.
                if hasattr(canlib, "canEnumHardwareEx"):
                    import ctypes
                    canlib.canEnumHardwareEx(ctypes.byref(ctypes.c_int()))
                configs = canlib.KvaserBus._detect_available_configs()
                options = [ChannelOption(
                    f"{cfg.get('device_name', 'Kvaser')} | serial {cfg.get('serial', '?')} | channel {cfg['channel']}",
                    {"channel": cfg["channel"], **({"supports_fd": cfg["supports_fd"]} if "supports_fd" in cfg else {})}) for cfg in configs]
                vendor = "Kvaser"
            else:
                from can.interfaces.pcan import PcanBus
                from can.interfaces.pcan.basic import PCANBasic
                PCANBasic()  # Distinguish a missing API from an unplugged adapter.
                configs = PcanBus._detect_available_configs()
                options = [ChannelOption(
                    f"{cfg.get('device_name', 'PCAN')} | {cfg['channel']} | controller {cfg.get('controller_number', '?')}",
                    {"channel": cfg["channel"], **({"supports_fd": cfg["supports_fd"]} if "supports_fd" in cfg else {})}) for cfg in configs]
                vendor = "PEAK PCAN"
            return options, (f"{len(options)} {vendor} CAN channels detected." if options else
                             f"{vendor} API loaded; no channels detected. Connect an adapter and Refresh devices.")
        if interface == "vector":
            prepare_vector_library()
            from can.interfaces.vector import canlib
            if canlib.xldriver is None:
                return [], "Vector XL API missing. Use Vector DLL to select your installed vxlapi64.dll, then restart Signal Explorer."
            configs = canlib.VectorBus._detect_available_configs()
            options = []
            for cfg in configs:
                name = cfg["vector_channel_config"].name
                options.append(ChannelOption(
                    f"{name} | serial {cfg['serial']} | CAN {cfg['channel'] + 1}",
                    {"channel": cfg["channel"], "serial": cfg["serial"],
                     "channel_index": cfg["channel_index"], "app_name": None,
                     **({"supports_fd": cfg["supports_fd"]} if "supports_fd" in cfg else {})}))
            return options, f"{len(options)} Vector CAN channels detected." if options else "No Vector CAN channels detected. Check USB and Vector drivers."
        if interface == "neovi":
            import ics
            from can.interfaces.ics_neovi import NeoViBus
            options = []
            for device in ics.find_devices():
                serial = NeoViBus.get_serial_number(device)
                # Other Intrepid models accept manual network IDs; only known
                # ValueCAN4 variants receive automatic physical channel choices.
                name = device.Name
                networks = ["HSCAN"]
                if "ValueCAN4" in name:
                    networks += ["HSCAN2"] if "-1" not in name else []
                    if "-4" in name:
                        networks += ["HSCAN3", "HSCAN4"]
                for network in networks:
                    options.append(ChannelOption(f"{name} | {serial} | {network}",
                                                 {"channel": network, "serial": serial, **({"supports_fd": False} if "ValueCAN3" in name else {})}))
            return options, f"{len(options)} Intrepid CAN channels detected." if options else "No Intrepid devices detected. Check USB and Intrepid drivers."
    except ImportError as exc:
        hint = "Install the intrepid optional dependency." if interface == "neovi" else "Install the vendor's matching API and drivers."
        return [], f"Vendor API unavailable: {exc}. {hint}"
    except Exception as exc:
        return [], f"Device scan failed: {exc}"
    return [], ""
