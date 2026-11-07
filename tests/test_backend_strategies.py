"""Backend policy stays centralized without importing proprietary SDKs eagerly."""

import json
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from signal_explorer.bus.backends import BACKENDS, BackendSpec
from signal_explorer.bus.canfd import FD_INTERFACES
from signal_explorer.bus.listen_only import open_bus
from signal_explorer.bus.service import BusService, TX_REPORT_INTERFACES
from signal_explorer.bus.strategies import (
    BackendStrategy,
    STRATEGIES,
    interfaces_supporting,
    strategy_for,
)
from signal_explorer.bus.timing import ADVANCED_INTERFACES, SILENT_INTERFACES


@pytest.mark.parametrize(
    "interface,fd,tx,silent,advanced,shared,clock",
    [
        ("virtual", True, False, True, False, False, "host_receipt"),
        ("slcan", False, False, False, False, False, "host_receipt"),
        ("gs_usb", False, False, False, False, False, "driver_epoch"),
        ("vector", True, True, True, True, True, "driver_epoch"),
        ("neovi", True, True, False, False, True, "device_relative"),
        ("kvaser", True, True, True, True, False, "driver_epoch"),
        ("pcan", True, True, True, True, False, "driver_epoch"),
        ("nixnet", True, True, True, True, False, "driver_epoch"),
    ],
)
def test_existing_capability_matrix(interface, fd, tx, silent, advanced, shared, clock):
    strategy = strategy_for(interface)
    assert (
        strategy.supports_fd,
        strategy.tx_reports,
        strategy.listen_only,
        strategy.advanced_timing,
        strategy.shared_port,
        strategy.timestamp_source,
    ) == (fd, tx, silent, advanced, shared, clock)
    assert strategy.needs_lease == shared


def test_every_ui_backend_has_one_registered_strategy_and_derived_capability_sets():
    assert {backend.interface for backend in BACKENDS} == set(STRATEGIES)
    assert FD_INTERFACES == interfaces_supporting("supports_fd")
    assert TX_REPORT_INTERFACES == interfaces_supporting("tx_reports")
    assert SILENT_INTERFACES == interfaces_supporting("listen_only")
    assert ADVANCED_INTERFACES == interfaces_supporting("advanced_timing")
    assert FD_INTERFACES == {"virtual", "vector", "neovi", "kvaser", "pcan", "nixnet"}


@pytest.mark.parametrize(
    "interface,value,connection,native",
    [
        ("virtual", "0123", "0123", "0123"),
        ("slcan", "COM3", "COM3", "COM3"),
        ("gs_usb", "2", 2, 2),
        ("vector", "3", 3, 3),
        ("kvaser", "4", 4, 4),
        ("pcan", "PCAN_USBBUS2", "PCAN_USBBUS2", "PCAN_USBBUS2"),
        ("pcan", "1", 1, "1"),
        ("nixnet", "CAN2", "CAN2", "CAN2"),
    ],
)
def test_connection_and_native_channel_types_preserve_existing_semantics(
    interface, value, connection, native
):
    strategy = strategy_for(interface)
    actual = strategy.connection_channel(value)
    assert actual == connection and type(actual) is type(connection)
    actual = strategy.native_channel(value)
    assert actual == native and type(actual) is type(native)


def test_intrepid_native_netid_conversion_is_lazy_and_separate_from_driver_channel(
    monkeypatch,
):
    convert = Mock(return_value=42)
    monkeypatch.setitem(
        sys.modules,
        "can.interfaces.ics_neovi",
        SimpleNamespace(NeoViBus=SimpleNamespace(channel_to_netid=convert)),
    )
    strategy = strategy_for("neovi")
    assert strategy.connection_channel("HSCAN2") == "HSCAN2"
    convert.assert_not_called()
    assert strategy.native_channel("HSCAN2") == 42
    convert.assert_called_once_with("HSCAN2")


@pytest.mark.parametrize(
    "interface,classic,fd",
    [
        ("vector", 16000000, 80000000),
        ("kvaser", 16000000, 80000000),
        ("pcan", 8000000, 80000000),
        ("nixnet", 40000000, 40000000),
    ],
)
def test_advanced_timing_uses_existing_controller_clocks(interface, classic, fd):
    assert strategy_for(interface).timing_clock(False) == classic
    assert strategy_for(interface).timing_clock(True) == fd


def test_per_device_capabilities_restrict_backend_ceiling():
    for interface in ("vector", "neovi", "kvaser", "pcan", "nixnet"):
        strategy = strategy_for(interface)
        assert strategy.supports_fd
        with pytest.raises(ValueError, match="enabled channel does not support CAN FD"):
            strategy.validate_connection(
                {}, [{"supports_fd": True}, {"supports_fd": False}], True
            )
        strategy.validate_connection({}, [{"supports_fd": False}], False)
    with pytest.raises(ValueError, match="no internal termination"):
        strategy_for("nixnet").validate_connection(
            {}, [{"can_termination": True, "can_term_available": False}], False
        )


def test_independent_ni_channel_termination_and_metadata_do_not_leak_between_sessions():
    strategy = strategy_for("nixnet")
    kwargs = {"interface": "nixnet", "receive_own_messages": False}
    strategy.prepare_connection(kwargs, 500000, False, {"can_termination": True})
    assert (
        strategy.channel_options(kwargs, {"can_termination": False})["can_termination"]
        is False
    )
    assert (
        strategy.channel_options(kwargs, {"can_termination": True})["can_termination"]
        is True
    )
    assert kwargs["can_termination"] is True
    another = {}
    strategy.prepare_connection(another, 250000, False, {})
    assert another == {"bitrate": 250000, "can_termination": False}
    config = {
        "device_serial": "NI123",
        "device_model": "USB-8502",
        "physical_port": "CAN2",
    }
    assert strategy.session_details(config, kwargs) == {
        "serial": "NI123",
        "can_termination": True,
        "device_model": "USB-8502",
        "physical_port": "CAN2",
    }


def test_kvaser_tx_reports_and_slcan_serial_defaults_are_preserved():
    kwargs = {}
    strategy_for("kvaser").prepare_connection(kwargs, 500000, True, {})
    assert kwargs == {"bitrate": 500000, "single_handle": False}
    kwargs = {"tty_baudrate": 230400}
    strategy_for("slcan").prepare_connection(kwargs, 250000, False, {})
    assert kwargs["tty_baudrate"] == 230400
    kwargs = {}
    strategy_for("slcan").prepare_connection(kwargs, 250000, False, {})
    assert kwargs["tty_baudrate"] == 115200


def test_unknown_or_unsupported_silent_backend_never_falls_back_to_active(monkeypatch):
    bad_strategy = BackendStrategy()
    bad_strategy.open_silent = Mock(
        side_effect=AssertionError("Unsupported hook must not run")
    )
    monkeypatch.setitem(STRATEGIES, "unsupported-hook", bad_strategy)
    with patch("signal_explorer.bus.listen_only.can.Bus") as native:
        for interface in (
            "not-registered",
            "slcan",
            "gs_usb",
            "neovi",
            "unsupported-hook",
        ):
            with pytest.raises(ValueError):
                open_bus(interface=interface, channel=0, monitor_only=True)
        with pytest.raises(ValueError, match="No backend strategy"):
            open_bus(interface="not-registered", channel=0)
        native.assert_not_called()
        bad_strategy.open_silent.assert_not_called()


def test_new_strategy_can_customize_shared_connection_without_service_vendor_branches(
    monkeypatch,
):
    class ExampleStrategy(BackendStrategy):
        def prepare_connection(self, kwargs, bitrate, driver_tx_reports, config):
            super().prepare_connection(kwargs, bitrate, driver_tx_reports, config)
            kwargs["example_port_setting"] = config["port_setting"]

        def native_channel(self, channel):
            return f"native:{channel}"

    monkeypatch.setitem(STRATEGIES, "example", ExampleStrategy())
    backend = BackendSpec("example", "Example test backend", "example")
    bus = Mock()
    with patch("signal_explorer.bus.service.open_bus", return_value=bus) as factory:
        result = BusService._open_connection(
            backend, "port-A", 250000, False, {"port_setting": 17}
        )
    assert result["primary_channel"] == "native:port-A"
    assert factory.call_args.kwargs["example_port_setting"] == 17
    assert factory.call_args.kwargs["bitrate"] == 250000
    assert result["bus"] is bus
    BusService._dispose_open_result(result)
    bus.shutdown.assert_called_once()


def test_strategy_import_does_not_import_optional_vendor_sdks():
    source = """
import json, sys
import signal_explorer.bus.strategies
vendors=('can.interfaces.vector', 'can.interfaces.ics_neovi', 'can.interfaces.kvaser', 'can.interfaces.pcan', 'can.interfaces.nixnet', 'ics', 'nixnet')
print(json.dumps([name for name in sys.modules if any(name==v or name.startswith(v+'.') for v in vendors)]))
"""
    result = subprocess.run(
        [sys.executable, "-c", source],
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    )
    assert json.loads(result.stdout) == []
