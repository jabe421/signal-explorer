import os
os.environ["QT_QPA_PLATFORM"] = "offscreen"
import unittest
from unittest.mock import patch
from types import SimpleNamespace
from PySide6.QtWidgets import QApplication
from signal_explorer.bus.backends import BACKENDS
from signal_explorer.bus.discovery import discover_channels, ChannelOption
from signal_explorer.bus.service import BusService
from signal_explorer.ui.main_window import MainWindow

app = QApplication.instance() or QApplication([])

class VendorAdapters(unittest.TestCase):
    def test_original_adapters_preserved(self):
        self.assertTrue({"virtual", "slcan", "gs_usb", "kvaser", "vector", "ics_neovi", "pcan"}.issubset({b.key for b in BACKENDS}))
        self.assertEqual(next(b for b in BACKENDS if b.key == "ics_neovi").interface, "neovi")

    def test_intrepid_serial_and_network_options(self):
        with patch("ics.find_devices", return_value=[SimpleNamespace(Name="ValueCAN4-2", SerialNumber=1879029185)]):
            options, note = discover_channels("neovi")
        self.assertEqual([o.config["channel"] for o in options], ["HSCAN", "HSCAN2"])
        self.assertTrue(all(o.config["serial"] == "V2Q475" for o in options))

    def test_vector_physical_channel_config(self):
        from can.interfaces.vector import canlib
        config = dict(channel=1, serial=123, channel_index=5,
                      vector_channel_config=SimpleNamespace(name="VN1610"))
        with patch.object(canlib, "xldriver", object()), patch.object(canlib.VectorBus, "_detect_available_configs", return_value=[config]):
            options, note = discover_channels("vector")
        self.assertEqual(options[0].config, dict(channel=1, serial=123, channel_index=5, app_name=None))
        self.assertIn("CAN 2", options[0].label)

    def test_kvaser_u100_channel_discovery(self):
        from can.interfaces.kvaser import canlib
        cfg = dict(channel=3, device_name="Kvaser U100-C", serial=1234)
        with patch.dict(canlib.__dict__, {"__canlib": object()}), patch.object(canlib.KvaserBus, "_detect_available_configs", return_value=[cfg]):
            options, note = discover_channels("kvaser")
        self.assertEqual(options[0].config, {"channel": 3})
        self.assertIn("U100", options[0].label)

    def test_pcan_dual_channel_discovery_and_absent_hardware(self):
        from can.interfaces.pcan import PcanBus
        cfgs = [dict(channel=f"PCAN_USBBUS{n}", device_name="PCAN-USB Pro FD", controller_number=n-1)
                for n in (1, 2)]
        with patch("can.interfaces.pcan.basic.PCANBasic"), patch.object(PcanBus, "_detect_available_configs", return_value=cfgs):
            options, note = discover_channels("pcan")
        self.assertEqual([o.config['channel'] for o in options], ['PCAN_USBBUS1', 'PCAN_USBBUS2'])
        with patch("can.interfaces.pcan.basic.PCANBasic"), patch.object(PcanBus, "_detect_available_configs", return_value=[]):
            options, note = discover_channels("pcan")
        self.assertEqual(options, [])
        self.assertIn('API loaded', note)
        with patch("can.interfaces.pcan.basic.PCANBasic", side_effect=OSError('Missing PCANBasic.dll')):
            options, note = discover_channels("pcan")
        self.assertEqual(options, [])
        self.assertIn('Missing PCANBasic.dll', note)

    def test_pcan_selected_channel_reaches_bus(self):
        service = BusService()
        backend = next(b for b in BACKENDS if b.key == "pcan")
        with patch("signal_explorer.bus.service.can.Bus", side_effect=RuntimeError("test stop")) as factory:
            service.connect_bus(backend, "PCAN_USBBUS2", 250000, False,
                                {"channel": "PCAN_USBBUS2"})
        self.assertEqual(factory.call_args.kwargs['interface'], 'pcan')
        self.assertEqual(factory.call_args.kwargs['channel'], 'PCAN_USBBUS2')
        self.assertEqual(factory.call_args.kwargs['bitrate'], 250000)

    def test_selected_configuration_reaches_bus(self):
        service = BusService()
        backend = next(b for b in BACKENDS if b.key == "vector")
        with patch("signal_explorer.bus.service.can.Bus", side_effect=RuntimeError("test stop")) as factory:
            service.connect_bus(backend, "1", 500000, False,
                                dict(channel=1, serial=123, channel_index=5, app_name=None))
        kwargs = factory.call_args.kwargs
        self.assertEqual(kwargs["serial"], 123)
        self.assertEqual(kwargs["channel_index"], 5)
        self.assertIsNone(kwargs["app_name"])
        self.assertFalse(kwargs["receive_own_messages"])
        self.assertTrue(kwargs["ignore_config"])

    def test_ui_selection_and_manual_channel(self):
        window = MainWindow()
        try:
            with patch("signal_explorer.ui.main_window.discover_channels", return_value=(
                [ChannelOption("ValueCAN4-2 | V2Q475 | HSCAN2", dict(channel="HSCAN2", serial="V2Q475"))], "Found")):
                window._backend.setCurrentIndex(window._backend.findData("ics_neovi"))
            self.assertEqual(window._channel_value(), "HSCAN2")
            window._channel.setEditText("HSCAN")
            self.assertEqual(window._channel_value(), "HSCAN")
        finally:
            window.close()

if __name__ == "__main__":
    unittest.main()
