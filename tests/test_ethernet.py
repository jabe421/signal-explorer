import os
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
from dataclasses import replace
from pathlib import Path
import struct
import tempfile
import time
import unittest
from unittest.mock import patch

from asammdf import MDF
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt
from signal_explorer.ethernet.model import (
    EthernetFrame, LOCAL_MAC, PEER_MAC, PRESETS, checksum, decode,
    make_frame, mac_bytes, preset, udp_payload,
)
from signal_explorer.ethernet.codec import EthernetCodec, NAME
from signal_explorer.ethernet.service import EthernetService
from signal_explorer.recording.playback import MF4Playback
from signal_explorer.ui.main_window import MainWindow
from signal_explorer.ui.ethernet_trace_model import EthernetTraceModel, EthernetFilter

app = QApplication.instance() or QApplication([])


def wait_for(predicate, timeout=4):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return True
        time.sleep(.005)
    return False


def write_log(path, frames):
    codec = EthernetCodec()
    writer = codec.writer(path)
    for frame in frames:
        writer.write(codec.capture(frame))
    writer.finish({})


class EthernetTests(unittest.TestCase):
    def test_frame_validation_padding_vlan_and_mtu(self):
        frame = make_frame(LOCAL_MAC, PEER_MAC, 0x88b5, b'abc')
        self.assertEqual(len(frame.raw), 60)
        self.assertEqual(frame.raw[:6], mac_bytes(PEER_MAC))
        self.assertEqual(frame.payload, b'abc' + bytes(43))
        tagged = make_frame(LOCAL_MAC, PEER_MAC, 0x88b5, bytes(1500), 4094, 7)
        self.assertEqual((len(tagged.raw), tagged.vlan, tagged.pcp), (1518, 4094, 7))
        for kwargs in ({'payload': bytes(1501)}, {'vlan': 4095}, {'pcp': 8}, {'ethertype': 0x8100}, {'ethertype': 42}, {'source': 'FF:FF:FF:FF:FF:FF'}):
            args = dict(source=LOCAL_MAC, destination=PEER_MAC, ethertype=0x88b5)
            args.update(kwargs)
            with self.assertRaises(ValueError):
                make_frame(**args)
        for address in ('00:01:02', '02:00-00:00:00:01', 'gg:00:00:00:00:01'):
            with self.assertRaises(ValueError): mac_bytes(address)
        with self.assertRaises(ValueError): EthernetFrame(0, bytes(13))
        with self.assertRaises(ValueError): replace(frame, direction='unknown')
        with self.assertRaises(ValueError): replace(frame, channel=256)

    def test_presets_checksum_and_corruption_detection(self):
        for name in PRESETS:
            frame = preset(name)
            protocol, summary = decode(frame)
            self.assertNotIn('Malformed', summary)
            self.assertNotIn('bad ', summary)
            self.assertIn(protocol, ('UDP', 'ARP', '0x88B5'))
        packet = udp_payload(b'odd')
        self.assertEqual(checksum(packet[:20]), 0)
        pseudo = packet[12:20] + struct.pack('!BBH', 0, 17, len(packet)-20)
        self.assertEqual(checksum(pseudo + packet[20:]), 0)
        broken = bytearray(preset('UDP telemetry').raw)
        broken[-1] ^= 1
        self.assertIn('bad UDP checksum', decode(EthernetFrame(0, broken))[1])
        broken[24] ^= 1
        self.assertIn('bad IPv4 checksum', decode(EthernetFrame(0, broken))[1])
        self.assertIn('192.0.2.1 -> 192.0.2.2', decode(preset('ARP request'))[1])

    def test_simulated_exchange_never_opens_a_socket(self):
        service = EthernetService(); events = []
        service.traffic.connect(events.append)
        with patch('socket.socket', side_effect=AssertionError('Network access forbidden')):
            try:
                service.connect_simulated(False, True)
                self.assertTrue(service.send_message(preset('VLAN UDP')))
                self.assertEqual([f.direction for f in events], ['tx', 'rx'])
                self.assertEqual(events[-1].destination, LOCAL_MAC)
                self.assertEqual(events[-1].vlan, 100)
                self.assertIn('ACK:EngineRPM', decode(events[-1])[1])
                self.assertNotIn('bad ', decode(events[-1])[1])
                service.send_message(make_frame(LOCAL_MAC, PEER_MAC, 0x0800, udp_payload(b'Malformed and bad are valid payload words')))
                self.assertEqual(events[-1].direction, 'rx')
                service.inject_test('ARP request')
                self.assertEqual(events[-1].direction, 'rx')
                before = len(events)
                service.send_message(make_frame(LOCAL_MAC, '02:00:00:00:00:03', 0x88b5, b'test'))
                self.assertEqual(len(events), before+1)
            finally: service.disconnect_bus()
        self.assertFalse(service.send_message(preset('UDP telemetry'), report_errors=False))

    def test_demo_can_be_disabled_and_disconnect_stops_it(self):
        service = EthernetService(); events = []
        service.message.connect(events.append)
        try:
            service.connect_simulated(True, False)
            self.assertTrue(wait_for(lambda: len(events) >= 4))
            self.assertTrue(all(f.direction == 'rx' for f in events))
            service.disconnect_bus(); count = len(events)
            wait_for(lambda: False, .15)
            self.assertEqual(len(events), count)
            service.connect_simulated(False, False)
            service.send_message(preset('UDP telemetry'))
            self.assertEqual(len(events), count+1)
        finally: service.disconnect_bus()

    def test_history_flow_counts_and_filters(self):
        model = EthernetTraceModel(); proxy = EthernetFilter(); proxy.setSourceModel(model)
        model.append(preset('UDP telemetry', 1)); model.append(preset('UDP telemetry', 2))
        model.append(preset('VLAN UDP', direction='rx'))
        self.assertEqual(model.rowCount(), 3)
        model.set_history_mode(False)
        self.assertEqual(model.rowCount(), 2)
        self.assertEqual(model.data(model.index(0, 1)), '2')
        proxy.configure('EngineRPM 192.0.2.1', '0800', 'rx')
        self.assertEqual(proxy.rowCount(), 1)
        with self.assertRaises(ValueError): proxy.configure('', 'fffff')
        self.assertEqual(proxy.rowCount(), 1)
        proxy.configure('', '0806')
        self.assertEqual(proxy.rowCount(), 0)
        model.clear(); self.assertEqual(model.rowCount(), 0)

    def test_mf4_roundtrip_raw_frames_metadata_and_timing(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'ethernet.mf4'
            start = time.time()
            frames = [replace(preset(name), timestamp=start+i*.04, direction='rx' if i%2 else 'tx') for i, name in enumerate(PRESETS)]
            write_log(path, frames)
            restored = list(EthernetCodec().read(path))
            self.assertEqual([e.payload.raw for e in restored], [f.raw for f in frames])
            self.assertEqual([e.direction for e in restored], [f.direction for f in frames])
            for event, frame in zip(restored, frames): self.assertAlmostEqual(event.timestamp, frame.timestamp, places=5)
            with MDF(path) as mdf:
                self.assertIn(NAME, mdf.channels_db)
                metadata = mdf.extract_attachment(index=0)[0].decode()
                self.assertIn('"simulated": true', metadata)
                self.assertIn('no physical network transmission', metadata)
            sent = []; errors = []
            worker = MF4Playback(path, lambda f: sent.append((time.monotonic(), f)) or True, direction='tx', codec=EthernetCodec())
            worker.failed.connect(errors.append); worker.start()
            self.assertTrue(wait_for(lambda: worker.isFinished()))
            worker.wait(); app.processEvents()
            self.assertEqual(errors, [])
            self.assertEqual(len(sent), 2)
            self.assertGreaterEqual(sent[1][0]-sent[0][0], .065)
            self.assertTrue(all(f.direction=='tx' for _, f in sent))

    def test_reject_non_ethernet_log_before_any_transmission(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'empty.mf4'
            with MDF(version='4.10') as mdf: mdf.save(path, overwrite=True)
            with self.assertRaisesRegex(ValueError, 'Ethernet MF4'):
                list(EthernetCodec().read(path))

    def test_ui_pause_logging_repeat_stop_playback_and_tabs(self):
        window = MainWindow(); panel = window._ethernet
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / 'session.mf4')
            try:
                self.assertEqual([window._tabs.tabText(i) for i in range(window._tabs.count())], ['CAN / CAN FD', 'LIN', 'Ethernet'])
                window._tabs.setCurrentIndex(2)
                panel.demo.setChecked(False); panel.connect_button.click()
                self.assertTrue(panel.service.is_connected)
                panel.recording.log.click()
                panel.explore.click()  # Recording continues while trace is paused.
                panel.send.click()
                self.assertEqual(panel.model.rowCount(), 0)
                panel.explore.click(); panel.interval.setText('20'); panel.send.click()
                self.assertEqual(panel.send.text(), 'Stop')
                panel.table.selectRow(0)
                self.assertTrue(wait_for(lambda: panel.tx >= 3))
                self.assertEqual(len(panel.table.selectionModel().selectedRows()), 1)
                panel.send.click(); count = panel.tx
                self.assertEqual(panel.send.text(), 'Send')
                wait_for(lambda: False, .06); self.assertEqual(panel.tx, count)
                with patch('signal_explorer.ui.recording_panel.QFileDialog.getSaveFileName', return_value=(path, '')):
                    panel.recording.log.click()
                    self.assertTrue(wait_for(lambda: Path(path).exists() and not panel.recording.recorder.busy))
                events = list(EthernetCodec().read(path))
                self.assertEqual(len(events), 2*(panel.tx+1))
                panel.interval.setText('20'); panel.send.click()
                with patch('signal_explorer.ui.recording_panel.QFileDialog.getOpenFileName', return_value=(path, '')):
                    panel.recording.replay.click()
                self.assertFalse(panel._repeat.isActive())
                self.assertTrue(wait_for(lambda: not panel.recording.playback_busy))
                panel.interval.setText('20'); panel.send.click(); panel.connect_button.click()
                self.assertFalse(panel._repeat.isActive())
                self.assertFalse(panel.service.is_connected)
            finally:
                panel.service.disconnect_bus(); window.close()


if __name__ == '__main__': unittest.main()
