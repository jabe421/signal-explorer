"""Shared CAN backend policy; vendor SDK imports stay lazy.

Capabilities describe a backend ceiling. Discovered channel/transceiver
capabilities may restrict it further. Strategies never own live bus handles.
"""

import can


class BackendStrategy:
    supports_fd = False
    tx_reports = False
    listen_only = False
    advanced_timing = False
    shared_port = False
    needs_lease = False
    supports_termination = False
    enumerates_channels = False
    simulated = False
    timestamp_source = "driver_epoch"
    classic_clock = 16000000
    fd_clock = 80000000
    connection_hint = ""

    def connection_channel(self, channel):
        try:
            return int(channel)
        except ValueError:
            return channel

    def native_channel(self, channel):
        return channel

    def prepare_connection(self, kwargs, bitrate, driver_tx_reports, config):
        kwargs["bitrate"] = bitrate

    def channel_options(self, kwargs, config):
        return dict(kwargs)

    def fd_configuration(self, bitrate, data_bitrate):
        if not self.supports_fd:
            raise ValueError("CAN FD is not enabled for this backend.")
        return {"fd": True, "data_bitrate": data_bitrate}

    def timing_clock(self, fd):
        return self.fd_clock if fd else self.classic_clock

    def validate_channels(self, configs, fd):
        for config in configs:
            if (
                config.get("can_termination")
                and config.get("can_term_available") is False
            ):
                raise ValueError("An enabled NI channel has no internal termination.")
            if fd and config.get("supports_fd") is False:
                raise ValueError(
                    "An enabled channel does not support CAN FD. Disable that channel or use Classic CAN."
                )

    def validate_connection(self, config, configs, fd):
        self.validate_channels(configs, fd)
        if fd and config.get("supports_fd") is False:
            raise ValueError("The selected channel does not support CAN FD.")

    def bus_number(self, config, index):
        label = config.get("channel_label", "")
        return (
            int(label[3:])
            if label.startswith("CAN") and label[3:].isdigit()
            else index + 1
        )

    def session_details(self, config, kwargs):
        return {}

    def setup_health(self, bus, health):
        pass

    def open_silent(self, kwargs):
        raise ValueError(
            "Hardware listen-only setup is not supported by this backend. No channel was opened."
        )


class IntegerChannelStrategy(BackendStrategy):
    def native_channel(self, channel):
        return int(channel)


class VirtualStrategy(BackendStrategy):
    supports_fd = listen_only = simulated = True
    timestamp_source = "host_receipt"

    def connection_channel(self, channel):
        return channel

    def prepare_connection(self, kwargs, bitrate, driver_tx_reports, config):
        kwargs["receive_own_messages"] = False

    def fd_configuration(self, bitrate, data_bitrate):
        return {"protocol": can.CanProtocol.CAN_FD}

    def open_silent(self, kwargs):
        return can.Bus(**kwargs)


class SlcanStrategy(BackendStrategy):
    timestamp_source = "host_receipt"

    def connection_channel(self, channel):
        return channel

    def prepare_connection(self, kwargs, bitrate, driver_tx_reports, config):
        super().prepare_connection(kwargs, bitrate, driver_tx_reports, config)
        kwargs.setdefault("tty_baudrate", 115200)


class VectorStrategy(IntegerChannelStrategy):
    enumerates_channels = True
    supports_fd = tx_reports = listen_only = advanced_timing = True
    shared_port = needs_lease = True

    def prepare_connection(self, kwargs, bitrate, driver_tx_reports, config):
        from signal_explorer.bus.discovery import prepare_vector_library

        prepare_vector_library()
        super().prepare_connection(kwargs, bitrate, driver_tx_reports, config)

    def bus_number(self, config, index):
        return int(config["channel"]) + 1

    def setup_health(self, bus, health):
        from signal_explorer.bus.health_native import install_vector_events

        install_vector_events(bus, health, health.generation)

    def open_silent(self, kwargs):
        from signal_explorer.bus.listen_only import open_vector_silent

        return open_vector_silent(kwargs)


class IntrepidStrategy(BackendStrategy):
    enumerates_channels = True
    supports_fd = tx_reports = shared_port = needs_lease = True
    timestamp_source = "device_relative"

    def native_channel(self, channel):
        from can.interfaces.ics_neovi import NeoViBus

        return NeoViBus.channel_to_netid(channel)


class KvaserStrategy(IntegerChannelStrategy):
    enumerates_channels = True
    supports_fd = tx_reports = listen_only = advanced_timing = True

    def prepare_connection(self, kwargs, bitrate, driver_tx_reports, config):
        super().prepare_connection(kwargs, bitrate, driver_tx_reports, config)
        if driver_tx_reports:
            kwargs["single_handle"] = False

    def open_silent(self, kwargs):
        from signal_explorer.bus.listen_only import open_kvaser_silent

        return open_kvaser_silent(kwargs)


class PeakStrategy(BackendStrategy):
    enumerates_channels = True
    supports_fd = tx_reports = listen_only = advanced_timing = True
    classic_clock = 8000000

    def fd_configuration(self, bitrate, data_bitrate):
        timing = can.BitTimingFd.from_sample_point(
            f_clock=80000000,
            nom_bitrate=bitrate,
            nom_sample_point=80,
            data_bitrate=data_bitrate,
            data_sample_point=75,
        )
        return {"fd": True, "timing": timing}

    def open_silent(self, kwargs):
        from signal_explorer.bus.listen_only import open_peak_silent

        return open_peak_silent(kwargs)


class NiStrategy(BackendStrategy):
    enumerates_channels = True
    supports_fd = tx_reports = listen_only = advanced_timing = supports_termination = (
        True
    )
    classic_clock = fd_clock = 40000000
    connection_hint = (
        " Check the nixnet Python extra, the 64-bit NI-XNET driver, "
        "and the exact CAN interface name in NI MAX; close other bus sessions."
    )

    def connection_channel(self, channel):
        return channel

    def prepare_connection(self, kwargs, bitrate, driver_tx_reports, config):
        super().prepare_connection(kwargs, bitrate, driver_tx_reports, config)
        kwargs["can_termination"] = bool(config.get("can_termination", False))

    def channel_options(self, kwargs, config):
        return dict(kwargs, can_termination=bool(config.get("can_termination", False)))

    def fd_configuration(self, bitrate, data_bitrate):
        return {"fd": True, "fd_bitrate": data_bitrate}

    def validate_connection(self, config, configs, fd):
        self.validate_channels(configs, fd)
        if config.get("can_termination") and config.get("can_term_available") is False:
            raise ValueError(
                "The selected NI channel has no internal termination. Use external termination."
            )
        if fd and config.get("supports_fd") is False:
            raise ValueError("The selected channel does not support CAN FD.")

    def session_details(self, config, kwargs):
        return dict(
            can_termination=kwargs["can_termination"],
            serial=config.get("device_serial"),
            device_model=config.get("device_model"),
            physical_port=config.get("physical_port"),
        )

    def open_silent(self, kwargs):
        from signal_explorer.bus.listen_only import open_ni_silent

        return open_ni_silent(kwargs)


STRATEGIES = {
    "virtual": VirtualStrategy(),
    "slcan": SlcanStrategy(),
    "gs_usb": IntegerChannelStrategy(),
    "vector": VectorStrategy(),
    "neovi": IntrepidStrategy(),
    "kvaser": KvaserStrategy(),
    "pcan": PeakStrategy(),
    "nixnet": NiStrategy(),
}


def strategy_for(interface):
    try:
        return STRATEGIES[interface]
    except KeyError:
        raise ValueError(f"No backend strategy registered for {interface!r}.") from None


def interfaces_supporting(capability):
    return frozenset(
        name for name, strategy in STRATEGIES.items() if getattr(strategy, capability)
    )
