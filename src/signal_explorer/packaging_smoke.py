"""Private build verification: only virtual buses, no physical device opens."""
from dataclasses import replace
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import traceback


def main(report_path):
    os.environ['QT_QPA_PLATFORM'] = 'offscreen'
    from PySide6.QtCore import QSettings
    from PySide6.QtGui import QIcon, QPixmap
    from PySide6.QtWidgets import QApplication, QTextBrowser
    from signal_explorer.bus.backends import BACKENDS
    from signal_explorer.bus.service import BusService
    from signal_explorer.bus.transmit import TxEntry, TransmitEngine
    from signal_explorer.ethernet.codec import EthernetCodec
    from signal_explorer.ethernet.model import preset
    from signal_explorer.ethernet.service import EthernetService
    from signal_explorer.lin.codec import LINCodec
    from signal_explorer.lin.service import LinService
    from signal_explorer.recording.codecs import CANCodec
    from signal_explorer.recording.recorder import MF4Recorder
    from signal_explorer.recording.playback import MF4Playback
    from signal_explorer.scripting.controller import ScriptController
    from signal_explorer.scripting.transport import CanScriptTransport
    from signal_explorer.ui.main_window import MainWindow
    from signal_explorer.ui.about_dialog import AboutDialog
    import can
    import cantools

    report = dict(ok=False, frozen=bool(getattr(sys, 'frozen', False)), checks=[])
    app = QApplication.instance() or QApplication(['Signal Explorer packaging check'])
    app.setOrganizationName('Signal Explorer Packaging Smoke')
    app.setApplicationName('Signal Explorer Packaging Smoke')
    bus, lin, ethernet = BusService(), LinService(), EthernetService()
    controller = ScriptController(CanScriptTransport(bus))
    engine = TransmitEngine(bus)
    window, replay = None, None

    def wait(predicate, timeout=20):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            app.processEvents()
            if predicate():
                return
            time.sleep(.005)
        raise TimeoutError('Packaging smoke check timed out')

    def check(condition, label):
        if not condition:
            raise AssertionError(label)
        report['checks'].append(label)

    def roundtrip(codec, events, folder, metadata):
        recorder = MF4Recorder(temp_directory=folder, codec=codec)
        recorder.start(metadata)
        for event in events:
            recorder.capture(event)
        recorder.stop()
        wait(lambda: not recorder.busy)
        check(not recorder.problem and recorder.pending_path is not None, codec.protocol+' MF4 writer')
        target = folder/(codec.protocol+'.mf4')
        recorder.save_as(target)
        check(bool(list(codec.read(target))), codec.protocol+' MF4 reader')
        return target

    try:
        import ics

        check(hasattr(ics.structures, 's_extended_data_flash_header') and callable(ics.find_devices),
              'Intrepid Python binding and generated structures import')
        with tempfile.TemporaryDirectory(prefix='SignalExplorer-packaging-') as folder:
            folder = Path(folder)
            QSettings.setDefaultFormat(QSettings.Format.IniFormat)
            QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(folder))
            package = Path(__file__).parent
            assets = package/'assets'
            check(not QIcon(str(assets/'signal-explorer.ico')).isNull(), 'Application icon')
            check(not QPixmap(str(assets/'signal-explorer-splash.png')).isNull(), 'Splash image')
            check((package/'lin/demo.ldf').is_file(), 'Bundled LIN demo')
            check((package/'scripting/demo_can.py').is_file(), 'Bundled script source')
            window = MainWindow()
            check(window._current_backend().interface == 'virtual', 'Fresh GUI defaults to Virtual')
            window.show()
            app.processEvents()
            about = AboutDialog(window._running_version, window)
            driver_setup = about.findChild(QTextBrowser)
            check(driver_setup.openExternalLinks() and
                  'download.ni-can.html' in driver_setup.toHtml() and
                  'download.ni-xnet.html' in driver_setup.toHtml(),
                  'About dialog includes both NI driver links')
            about.close()

            install = Path(sys.executable).parent if report['frozen'] else package.parents[1]
            database = cantools.database.load_file(str(install/'examples/demo.dbc'))
            check(database.get_message_by_frame_id(0x200).name == 'EngineData', 'DBC loader')
            demo_board = cantools.database.load_file(str(install/'examples/demo_board.dbc'))
            with can.MF4Reader(install/'examples/demo_board.mf4') as demo_log:
                demo_frame = next(iter(demo_log))
                decoded = demo_board.decode_message(demo_frame.arbitration_id, demo_frame.data)
            check(bool(decoded), 'Demo board MF4 frame decodes with bundled DBC')
            events = []
            bus.traffic.connect(events.append)
            bus.connect_bus(next(b for b in BACKENDS if b.interface == 'virtual'),
                            'packaging-smoke', 500000, True, fd=True)
            output = []
            controller.output.connect(output.append)
            controller.start('''payload = bytes(range(8))
se.can.send(0x321, payload, fd=True, brs=True)
frame = se.can.recv(3.0, arbitration_id=0x221, extended=False)
se.check(frame is not None and frame.data == payload, "FD virtual echo")
se.check(frame.is_fd and frame.bitrate_switch, "FD/BRS preserved")
''')
            wait(lambda: not controller.active)
            check(controller.outcome == 'passed', 'Separate-process Python FD echo: '+''.join(output))
            entry = TxEntry(identifier=0x321, data=bytes(range(8)), cyclic=True, period_ms=30)
            engine.add(entry)
            engine.start([entry.uid], True)
            wait(lambda: engine.state(entry.uid)[0] >= 2)
            engine.update(replace(entry, data=b'abcdefgh'))
            wait(lambda: any(m.arbitration_id == 0x321 and bytes(m.data) == b'abcdefgh' for m in events))
            engine.stop_all()
            check(True, 'Cyclic TX and live payload edit')
            capture = roundtrip(CANCodec(), list(events), folder, bus.session_info)
            replayed = []
            replay = MF4Playback(capture, lambda frame: replayed.append(frame) or True, fd_enabled=True)
            replay.start()
            wait(lambda: not replay.isRunning())
            check(bool(replayed) and replay.sent > 0, 'Packaged MF4 replay')

            lin_events = []
            lin.traffic.connect(lin_events.append)
            lin.connect_simulated('master', 19200, demo_peer=True)
            lin.send_header(0x20)
            wait(lambda: len(lin_events) >= 2)
            roundtrip(LINCodec(), list(lin_events), folder, lin.session_info)
            check(True, 'Simulated LIN request/response')
            ethernet_events = []
            ethernet.traffic.connect(ethernet_events.append)
            ethernet.connect_simulated(demo=False, peer_replies=True)
            check(ethernet.send_message(preset('VLAN UDP')), 'Simulated Ethernet TX')
            wait(lambda: len(ethernet_events) >= 2)
            roundtrip(EthernetCodec(), list(ethernet_events), folder, ethernet.session_info)
            report['ok'] = True
    except BaseException:
        report['error'] = traceback.format_exc()
    finally:
        if replay and replay.isRunning():
            replay.stop()
            replay.wait(5000)
        controller.stop()
        try:
            wait(lambda: not controller.active, 5)
        finally:
            engine.shutdown()
            bus.disconnect_bus()
            lin.disconnect_bus()
            ethernet.disconnect_bus()
            if window:
                window.close()
            app.processEvents()
        target = Path(report_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(report, indent=2), encoding='utf-8')
        print(json.dumps(report, indent=2))
    return 0 if report['ok'] else 1
