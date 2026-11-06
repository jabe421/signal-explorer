import os
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
from threading import Thread
import time
from unittest.mock import patch
from uuid import uuid4

import can
import pytest
from PySide6.QtTest import QSignalSpy
from PySide6.QtWidgets import QApplication
from signal_explorer.bus.backends import BACKENDS
from signal_explorer.bus.service import BusService
from signal_explorer.scripting.controller import ScriptController
from signal_explorer.scripting.mailbox import ReceiveMailbox
from signal_explorer.scripting.transport import CanScriptTransport

app = QApplication.instance() or QApplication([])


def wait_for(predicate, timeout=8):
    end = time.monotonic()+timeout
    while time.monotonic() < end:
        app.processEvents()
        if predicate(): return True
        time.sleep(.002)
    return predicate()


@pytest.fixture
def runner():
    service = BusService()
    service.connect_bus(next(b for b in BACKENDS if b.interface == 'virtual'),
                        'script-buffer-'+uuid4().hex, 500000, False)
    controller = ScriptController(CanScriptTransport(service))
    output = []; controller.output.connect(output.append)
    yield controller, service, output
    controller.stop()
    assert wait_for(lambda: not controller.active)
    service.disconnect_bus()


def inject(service, identifier, value=0, **flags):
    service.traffic.emit(can.Message(arbitration_id=identifier, is_extended_id=False,
                                    data=bytes([value % 256]), **flags))


def test_unmatched_b_before_a_survives_next_receive(runner):
    controller, service, output = runner
    controller.start('''se.log("waiting")
a = se.can.recv(2, arbitration_id=0x100)
b = se.can.recv(0, arbitration_id=0x200)
se.check(a is not None and a.data == bytes([1]), "A matched")
se.check(b is not None and b.data == bytes([2]), "Earlier B retained")
se.check(b.timestamp <= a.timestamp, "Original arrival order")
''')
    assert wait_for(lambda: controller._pending is not None)
    inject(service, 0x200, 2); inject(service, 0x100, 1)
    assert wait_for(lambda: not controller.active), ''.join(output)
    assert controller.outcome == 'passed', ''.join(output)


def test_sleep_overflow_retains_newest_and_reports_loss_without_killing(runner):
    controller, service, output = runner
    with patch.object(controller, 'MAX_RECEIVE', 8):
        controller.start('''se.log("sleeping")
se.sleep(300)
s = se.can.stats()
se.check(s["dropped"] == 492 and s["buffered"] == 8, "Drops counted")
f = se.can.recv(0)
se.check(f.data == bytes([492 % 256]), "Oldest retained frame")
se.check(se.can.flush() == 7, "Flush remaining buffer")
se.check(se.can.stats()["dropped"] == 492, "Flush preserves loss count")
''')
    assert wait_for(lambda: 'sleeping' in ''.join(output))
    for i in range(500): inject(service, 0x200, i)
    assert wait_for(lambda: not controller.active)
    assert controller.outcome == 'passed', ''.join(output)
    assert '492 oldest frame(s) dropped' in ''.join(output)


def test_pause_overflow_is_bounded_and_resume_keeps_latest(runner):
    controller, service, output = runner
    with patch.object(controller, 'MAX_RECEIVE', 5):
        controller.start('''se.log("ready")
se.sleep(400)
se.check(se.can.stats()["dropped"] == 95, "Pause drops counted")
se.check(se.can.recv(0).data == bytes([95]), "Newest retained history")
''')
    assert wait_for(lambda: 'ready' in ''.join(output))
    controller.pause(); assert wait_for(lambda: controller.state == 'paused')
    for i in range(100): inject(service, 0x200, i)
    app.processEvents()
    assert controller.active and controller.state == 'paused'
    controller.resume(); assert wait_for(lambda: not controller.active)
    assert controller.outcome == 'passed', ''.join(output)


def test_capture_filters_exclude_bus_noise_and_invalid_edit_is_atomic(runner):
    controller, service, output = runner
    controller.start('''se.can.set_filters([{"can_id": 0x200, "can_mask": 0x7FF, "extended": False, "direction": "rx"}])
try:
    se.can.set_filters([{"can_id": 0x100}])
except RuntimeError:
    pass
else:
    raise AssertionError("Invalid filter accepted")
se.can.flush()
se.log("filtered")
se.sleep(300)
s = se.can.stats()
se.check(s["filtered"] == 5000, "Noise filtered before buffering")
se.check(s["buffered"] == 2 and s["dropped"] == 0, "Only replies buffered")
se.check(se.can.recv(0).data == bytes([17]), "First response")
se.check(se.can.recv(0).data == bytes([18]), "Second response")
se.can.set_filters(None)
se.log("all frames")
f = se.can.recv(2, arbitration_id=0x123)
se.check(f is not None, "Filters reset")
''')
    assert wait_for(lambda: 'filtered' in ''.join(output))
    for i in range(5000): inject(service, 0x100)
    inject(service, 0x200, 17); inject(service, 0x200, 18)
    assert wait_for(lambda: 'all frames' in ''.join(output))
    inject(service, 0x123)
    assert wait_for(lambda: not controller.active)
    assert controller.outcome == 'passed', ''.join(output)


def test_pruning_filters_does_not_erase_loss_counters(runner):
    controller, service, output = runner
    controller.start('''se.log("ready")
se.sleep(200)
n = se.can.set_filters([{"can_id": 0x200, "can_mask": 0x7FF}])
se.check(n == 1, "Old unrelated traffic pruned")
se.check(se.can.stats()["flushed"] == 1, "Intentional discard counted")
se.check(se.can.recv(0).arbitration_id == 0x200, "Matching history retained")
''')
    assert wait_for(lambda: 'ready' in ''.join(output))
    inject(service, 0x100); inject(service, 0x200)
    assert wait_for(lambda: not controller.active)
    assert controller.outcome == 'passed', ''.join(output)


def test_send_timestamp_matches_local_tx_event_and_log_clock(runner):
    controller, service, output = runner
    controller.start('''tx = se.can.send(0x321, bytes(range(8)))
f = se.can.recv(0, arbitration_id=0x321, direction="tx")
se.check(isinstance(tx, float) and tx > 1000000000, "TX Unix timestamp")
se.check(f.timestamp == tx, "Same timestamp as observed TX")
''')
    assert wait_for(lambda: not controller.active)
    assert controller.outcome == 'passed', ''.join(output)


def test_capture_wakes_pending_receive_without_controller_timer(runner):
    controller, service, output = runner
    controller.start('''f = se.can.recv(None, arbitration_id=0x123)
se.check(f is not None, "Event-driven response")
''')
    assert wait_for(lambda: controller._pending is not None)
    controller._timer.stop()
    producer = Thread(target=lambda: inject(service, 0x123))
    producer.start(); producer.join()
    assert wait_for(lambda: not controller.active)
    assert controller.outcome == 'passed', ''.join(output)


def test_unrelated_busy_traffic_does_not_queue_per_frame_wakeups(runner):
    controller, service, output = runner
    wakes = QSignalSpy(controller._receive_ready)
    controller.start('''f = se.can.recv(2, arbitration_id=0x123)
se.check(f is not None, "Target received among noise")
''')
    assert wait_for(lambda: controller._pending is not None)
    for _ in range(3000): inject(service, 0x200)
    assert wakes.count() == 0
    inject(service, 0x123)
    assert wakes.count() == 1
    assert wait_for(lambda: not controller.active)
    assert controller.outcome == 'passed', ''.join(output)


def test_receive_deadline_uses_capture_arrival_and_preserves_late_frames(runner):
    controller, service, output = runner
    controller.start('''f = se.can.recv(.05, arbitration_id=0x100)
se.check(f is None, "Late arrival times out")
se.check(se.can.recv(0, arbitration_id=0x100) is not None, "Late frame remains for next receive")
''')
    assert wait_for(lambda: controller._pending is not None)
    controller._timer.stop()
    time.sleep(.08)  # Hold the parent event loop past the deadline deliberately.
    inject(service, 0x100)
    assert wait_for(lambda: not controller.active)
    assert controller.outcome == 'passed', ''.join(output)


def test_mailbox_long_lived_unmatched_frame_and_accounting():
    mailbox = ReceiveMailbox(3)
    mailbox.append({'id': 'keep'})
    for i in range(5000):
        mailbox.append({'id': i})
        sequence = mailbox.after(0)[-1][0]
        assert mailbox.take(sequence)['id'] == i
    assert mailbox.after(0)[0][1] == {'id': 'keep'}
    stats = mailbox.stats()
    assert stats['received'] == stats['buffered']+stats['consumed']+stats['dropped']+stats['flushed']
