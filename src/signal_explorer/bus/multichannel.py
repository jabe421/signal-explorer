"""One native port for Vector/neoVI; independent handles for other drivers."""
from copy import copy
from queue import Queue, Empty, Full
from threading import Event, Thread
import can


def native_channel(interface, channel):
    if interface == 'neovi':
        from can.interfaces.ics_neovi import NeoViBus
        return NeoViBus.channel_to_netid(channel)
    if interface in {'vector', 'kvaser', 'gs_usb'}:
        return int(channel)
    return channel


def open_channels(factory, interface, configs, kwargs):
    channels = [native_channel(interface, cfg['channel']) for cfg in configs]
    if len(set(map(str, channels))) != len(channels):
        raise ValueError('Each CAN channel may only be enabled once.')
    if interface in {'vector', 'neovi'}:
        serials = {str(cfg.get('serial')) for cfg in configs}
        if len(serials) != 1 or None in [cfg.get('serial') for cfg in configs]:
            raise ValueError('Select channels from one identified physical device.')
        args = dict(kwargs, channel=channels, serial=configs[0]['serial'])
        args.pop('channel_index', None)
        return factory(**args)
    buses = {}
    try:
        for cfg, channel in zip(configs, channels):
            args = dict(kwargs, channel=channel)
            if interface == 'nixnet':
                args['can_termination'] = bool(cfg.get('can_termination', False))
            buses[channel] = factory(**args)
        return ChannelMux(buses)
    except Exception:
        for bus in buses.values():
            try:
                bus.shutdown()
            except Exception:
                pass
        raise


class ChannelMux:
    def __init__(self, buses):
        self.buses = buses
        self.protocol = next(iter(buses.values())).protocol
        self._queue = Queue(maxsize=100000)
        self._stop = Event()
        self._threads = [Thread(target=self._read, args=(channel, bus), daemon=True)
                         for channel, bus in buses.items()]
        for thread in self._threads:
            thread.start()

    def _read(self, channel, bus):
        try:
            while not self._stop.is_set():
                msg = bus.recv(.1)
                if msg is not None:
                    msg = copy(msg)
                    msg.channel = channel
                    self._queue.put_nowait(msg)
        except Exception as exc:
            self._stop.set()
            try:
                self._queue.put_nowait(exc)
            except Full:
                pass

    def recv(self, timeout=None):
        try:
            item = self._queue.get(timeout=timeout)
        except Empty:
            if self._stop.is_set():
                raise can.CanOperationError('Multi-channel capture stopped (receive failure or queue overflow).')
            return None
        if isinstance(item, Exception):
            raise item
        return item

    def send(self, msg, timeout=None):
        bus = self.buses.get(msg.channel)
        if bus is None:
            raise ValueError('TX channel is not enabled.')
        bus.send(msg, timeout=timeout)

    def shutdown(self):
        self._stop.set()
        for thread in self._threads:
            thread.join(1)
        failures = []
        for bus in self.buses.values():
            try:
                bus.shutdown()
            except Exception as exc:
                failures.append(str(exc))
        if failures:
            raise can.CanOperationError('; '.join(failures))
