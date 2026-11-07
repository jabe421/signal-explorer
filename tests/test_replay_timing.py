"""Replay scheduling, native-wait fallback and host-metric semantics."""

import os

os.environ["QT_QPA_PLATFORM"] = "offscreen"

import json
import sys
from threading import Event, Thread
import time
from unittest.mock import patch
import uuid

import can
import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from signal_explorer.bus.backends import BACKENDS
from signal_explorer.recording.codecs import BusEvent
from signal_explorer.recording.playback import MF4Playback
from signal_explorer.recording.replay_timing import TimingSamples
from signal_explorer.recording.recorder import MF4Recorder
from signal_explorer.ui.main_window import MainWindow

app = QApplication.instance() or QApplication([])


class Codec:
    protocol = "TEST"

    def __init__(self, gaps=(0, 0.1, 0.2)):
        self.gaps = gaps

    def read(self, path):
        for i, gap in enumerate(self.gaps):
            yield BusEvent(1000 + gap, 1, "tx", "TEST", i)

    def replayable(self, event):
        return True

    def prepare_tx(self, event, options):
        return event.payload


def wait_for(predicate, timeout=4):
    deadline = time.perf_counter() + timeout
    while time.perf_counter() < deadline:
        app.processEvents()
        if predicate():
            return True
        time.sleep(0.005)
    return False


def test_bounded_percentiles_and_full_run_totals():
    samples = TimingSamples(capacity=3)
    assert samples.snapshot()["p95_ms"] is None
    for seconds in (0.001, 0.002, 0.003, 0.004):
        samples.add(seconds)
    result = samples.snapshot()
    assert result["count"] == 4 and result["percentile_samples"] == 3
    assert result["mean_ms"] == pytest.approx(2.5)
    assert result["max_ms"] == pytest.approx(4)
    assert result["p50_ms"] == pytest.approx(3)
    assert result["p95_ms"] == result["p99_ms"] == pytest.approx(4)


@pytest.mark.parametrize("failure", [None, False, "exception"])
def test_scheduler_duration_and_return_lateness_are_distinct_even_on_send_failure(
    failure,
):
    clock = [10.0]

    def send(frame):
        clock[0] += 0.003
        if failure == "exception":
            raise RuntimeError("controlled send failure")
        return failure is None

    worker = MF4Playback("none", send, codec=Codec((0, 0.1)), wait_backend="condition")

    def deadline(target):
        if target:
            clock[0] = target + worker._paused_time + 0.002
        return True

    worker._wait_until = deadline
    with patch(
        "signal_explorer.recording.playback.time.perf_counter",
        side_effect=lambda: clock[0],
    ):
        worker.run()
    report = worker.timing_snapshot()
    assert report["scheduler_lateness"]["mean_ms"] == pytest.approx(2)
    assert report["send_duration"]["mean_ms"] == pytest.approx(3)
    assert report["send_return_lateness"]["mean_ms"] == pytest.approx(5)
    assert worker.max_lateness == pytest.approx(0.002)
    assert report["state"] == ("completed" if failure is None else "failed")
    assert report["sent"] == (2 if failure is None else 0)
    assert report["send_duration"]["count"] == (2 if failure is None else 1)


@pytest.mark.parametrize("mode", ["balanced", "precision"])
@pytest.mark.parametrize("backend", ["auto", "condition"])
def test_no_send_before_recorded_deadline(mode, backend):
    sent = []
    worker = MF4Playback(
        "none",
        lambda frame: sent.append(time.perf_counter()) or True,
        codec=Codec(tuple(i * 0.002 for i in range(30))),
        timing_mode=mode,
        wait_backend=backend,
    )
    worker.start()
    assert worker.wait(3000)
    assert len(sent) == 30 and worker.timing_snapshot()["state"] == "completed"
    for actual, offset in zip(sent, Codec(tuple(i * 0.002 for i in range(30))).gaps):
        assert actual >= worker._replay_start + offset - 1e-6


@pytest.mark.parametrize("mode", ["balanced", "precision"])
def test_pause_ack_stops_sends_resume_keeps_gap_and_stop_interrupts_long_wait(mode):
    sent = []
    paused = Event()
    worker = MF4Playback(
        "none",
        lambda frame: sent.append(time.perf_counter()) or True,
        codec=Codec((0, 0.3, 30)),
        timing_mode=mode,
    )
    worker.paused.connect(paused.set, Qt.ConnectionType.DirectConnection)
    worker.start()
    try:
        assert wait_for(lambda: len(sent) == 1)
        time.sleep(0.03)
        worker.pause()
        assert paused.wait(1)
        assert worker.timing_snapshot()["state"] == "paused"
        time.sleep(0.08)
        assert len(sent) == 1
        worker.resume()
        assert wait_for(lambda: len(sent) == 2)
        assert sent[1] - sent[0] >= 0.35
        began = time.perf_counter()
        worker.stop()
        assert worker.wait(1000)
        assert time.perf_counter() - began < 1
        assert len(sent) == 2 and worker.timing_snapshot()["state"] == "stopped"
    finally:
        worker.stop()
        worker.wait(2000)


@pytest.mark.parametrize("mode", ["balanced", "precision"])
def test_stop_while_paused_before_preflight_sends_nothing(mode):
    sent = []
    paused = Event()
    worker = MF4Playback(
        "none",
        lambda frame: sent.append(frame) or True,
        codec=Codec(),
        timing_mode=mode,
    )
    worker.paused.connect(paused.set, Qt.ConnectionType.DirectConnection)
    worker.pause()
    worker.start()
    try:
        assert paused.wait(1)
        worker.stop()
        assert worker.wait(1000)
        assert not sent and worker.timing_snapshot()["send_duration"]["count"] == 0
    finally:
        worker.stop()
        worker.wait(2000)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows high-resolution wait API")
def test_native_wait_wakes_before_deadline_and_closes_idempotently():
    from signal_explorer.recording.windows_wait import WindowsReplayWait

    waiter = WindowsReplayWait()
    entered = Event()
    done = Event()

    def waiting():
        entered.set()
        waiter.wait_until(time.perf_counter() + 30)
        done.set()

    thread = Thread(target=waiting)
    try:
        thread.start()
        assert entered.wait(1)
        waiter.wake()
        assert done.wait(1)
        thread.join(1)
    finally:
        if thread.is_alive():
            waiter.wake()
            thread.join(1)
        waiter.close()
        waiter.close()
    assert waiter.timer is waiter.event is None


@pytest.mark.skipif(sys.platform != "win32", reason="Windows native timer fallback")
def test_unavailable_native_timer_falls_back_without_losing_frames():
    worker = MF4Playback("none", lambda _: True, codec=Codec((0, 0.002)))
    with patch(
        "signal_explorer.recording.windows_wait.WindowsReplayWait",
        side_effect=OSError("timer unavailable"),
    ):
        worker.run()
    report = worker.timing_snapshot()
    assert report["state"] == "completed" and report["sent"] == 2
    assert "unavailable" in report["wait_backend"]


@pytest.mark.skipif(sys.platform != "win32", reason="Windows native timer failure")
def test_failed_native_wait_closes_and_replays_once_via_condition():
    class BrokenWait:
        closed = False

        def wait_until(self, deadline):
            raise OSError("timer failed")

        def close(self):
            self.closed = True

        def wake(self):
            pass

    native = BrokenWait()
    sent = []
    worker = MF4Playback(
        "none", lambda f: sent.append(f) or True, codec=Codec((0, 0.002, 0.004))
    )
    with patch(
        "signal_explorer.recording.windows_wait.WindowsReplayWait", return_value=native
    ):
        worker.run()
    assert sent == [0, 1, 2] and native.closed
    assert "failed" in worker.timing_snapshot()["wait_backend"]


def test_preflight_failure_has_no_send_samples_and_invalid_options_rejected():
    worker = MF4Playback("none", lambda _: True, codec=Codec(()))
    worker.run()
    assert worker.timing_snapshot()["state"] == "failed"
    assert worker.timing_summary() == "No replay send attempts measured."
    with pytest.raises(ValueError):
        MF4Playback("none", lambda _: True, timing_mode="invalid")
    with pytest.raises(ValueError):
        MF4Playback("none", lambda _: True, wait_backend="invalid")


@pytest.mark.skipif(sys.platform != "win32", reason="Windows handle cleanup")
def test_partial_native_timer_creation_closes_created_handle():
    from unittest.mock import Mock
    from signal_explorer.recording.windows_wait import WindowsReplayWait

    api = Mock()
    api.CreateWaitableTimerExW.return_value = 123
    api.CreateEventW.return_value = None
    with patch(
        "signal_explorer.recording.windows_wait.ctypes.WinDLL", return_value=api
    ):
        with pytest.raises(OSError):
            WindowsReplayWait()
    api.CancelWaitableTimer.assert_called_once_with(123)
    api.CloseHandle.assert_called_once_with(123)


@pytest.mark.parametrize("protocol", ["LIN", "Ethernet"])
def test_precision_mode_reuses_real_protocol_codecs(tmp_path, protocol):
    from dataclasses import replace
    from signal_explorer.lin.codec import LINCodec
    from signal_explorer.lin.model import LinFrame
    from signal_explorer.ethernet.codec import EthernetCodec
    from signal_explorer.ethernet.model import preset

    codec = LINCodec("master") if protocol == "LIN" else EthernetCodec()
    recorder = MF4Recorder(temp_directory=tmp_path, codec=codec)
    recorder.start()
    try:
        for index in range(3):
            stamp = time.time() + index * 0.02
            event = (
                LinFrame(stamp, 16, direction="tx", kind="header")
                if protocol == "LIN"
                else replace(preset("Raw test (88B5)", index), timestamp=stamp)
            )
            recorder.capture(event)
    finally:
        recorder.stop()
        recorder._thread.join(5)
    assert not recorder.problem
    sent = []
    worker = MF4Playback(
        recorder.pending_path,
        lambda event: sent.append(event) or True,
        codec=codec,
        timing_mode="precision",
        tx_options={"lengths": {16: 2}},
    )
    worker.start()
    assert worker.wait(3000)
    assert worker.timing_snapshot()["state"] == "completed" and len(sent) == 3
    if protocol == "LIN":
        assert sent == [("master", 16)] * 3
    else:
        assert all(event.direction == "tx" and len(event.raw) >= 60 for event in sent)


def test_timing_menu_sets_shared_mode_and_retains_virtual_replay_report(tmp_path):
    path = tmp_path / "source.mf4"
    with can.MF4Writer(path) as writer:
        for index in range(3):
            writer.on_message_received(
                can.Message(
                    timestamp=time.time() + index * 0.05,
                    channel=1,
                    arbitration_id=0x321,
                    is_extended_id=False,
                    is_rx=False,
                    data=[index],
                )
            )
    window = MainWindow()
    try:
        menu = next(
            a.menu() for a in window.menuBar().actions() if a.text() == "Timing"
        )
        next(a for a in menu.actions() if a.text() == "Replay timing...").trigger()
        dialog = window._replay_timing_window
        assert dialog.isVisible() and dialog.mode.currentData() == "balanced"
        dialog.mode.setCurrentIndex(dialog.mode.findData("precision"))
        assert all(
            p.replay_timing_mode == "precision"
            for p in window._replay_panels().values()
        )
        window._service.connect_bus(BACKENDS[0], str(uuid.uuid4()), 500000, False)
        window._recording_panel.start_playback(path)
        dialog.refresh()
        assert not dialog.mode.isEnabled()
        assert wait_for(lambda: not window._recording_panel.playback_busy)
        dialog.refresh()
        report = window._recording_panel.last_replay_timing
        assert (
            report["mode"] == "precision"
            and report["sent"] == 3
            and report["state"] == "completed"
        )
        assert dialog.mode.isEnabled() and dialog.table.item(0, 2).text() == "3"
        assert window._diagnostics_context()["replay_timing"]["CAN"]["sent"] == 3
        original = app.clipboard().text()
        try:
            dialog.copy_report()
            assert json.loads(app.clipboard().text())["CAN"]["sent"] == 3
        finally:
            app.clipboard().setText(original)
        dialog.close()
        assert not dialog._timer.isActive()
    finally:
        if window._recording_panel.playback_busy:
            window._recording_panel.playback.stop()
            window._recording_panel.playback.wait(2000)
            app.processEvents()
        window.close()
