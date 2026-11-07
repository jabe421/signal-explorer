"""Force recorder backpressure without physical hardware or timing races."""

import os

os.environ["QT_QPA_PLATFORM"] = "offscreen"

from dataclasses import replace
import json
import threading
import time
from unittest.mock import patch

import can
from asammdf import MDF
import pytest
from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import QApplication

from signal_explorer.ethernet.codec import EthernetCodec
from signal_explorer.ethernet.model import preset
from signal_explorer.lin.codec import LINCodec
from signal_explorer.lin.model import LinFrame
from signal_explorer.recording.codecs import BusEvent, CANCodec
from signal_explorer.recording.recorder import MF4Recorder
from signal_explorer.ui.recording_panel import RecordingPanel

app = QApplication.instance() or QApplication([])


def wait_for(predicate, timeout=5, gui=False):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if gui:
            app.processEvents()
        if predicate():
            return True
        time.sleep(0.005)
    return False


class GatedCodec:
    def __init__(self, codec):
        self.inner = codec
        self.protocol = codec.protocol
        self.entered = threading.Event()
        self.release = threading.Event()
        self.copies = 0

    def capture(self, payload):
        self.copies += 1
        return self.inner.capture(payload)

    def writer(self, path, database=None):
        self.entered.set()
        if not self.release.wait(5):
            raise RuntimeError("Test writer was not released")
        return self.inner.writer(path, database)


def frame(protocol, index, direction="rx"):
    timestamp = 1_800_000_000.0 + index * 0.01
    if protocol == "CAN":
        return can.Message(
            timestamp=timestamp,
            channel=1,
            arbitration_id=0x100 + index,
            is_extended_id=False,
            data=[index % 256],
            is_rx=direction == "rx",
        )
    if protocol == "LIN":
        return LinFrame(timestamp, 16, bytes([index % 256]), direction)
    return replace(preset("Raw test (88B5)", index, direction), timestamp=timestamp)


def metadata(path):
    with MDF(path) as mdf:
        for index in range(len(mdf.attachments)):
            payload = json.loads(mdf.extract_attachment(index)[0])
            if "overflow_policy" in payload:
                return payload
    raise AssertionError("Session metadata missing")


@pytest.mark.parametrize(
    "codec", [CANCodec(), LINCodec(), EthernetCodec()], ids=["CAN", "LIN", "Ethernet"]
)
def test_continue_drops_incoming_preserves_queued_events_and_recovers(tmp_path, codec):
    gated = GatedCodec(codec)
    recorder = MF4Recorder(
        queue_size=2, temp_directory=tmp_path, codec=gated, overflow_policy="continue"
    )
    recorder.start()
    assert gated.entered.wait(2)
    first = [frame(codec.protocol, 0), frame(codec.protocol, 1, "tx")]
    try:
        for event in first:
            recorder.capture(event)
        for index in range(2, 8):
            recorder.capture(
                frame(codec.protocol, index, "rx" if index % 2 == 0 else "tx")
            )
        assert recorder.recording and recorder.dropped == 6 and recorder.accepted == 2
        assert gated.copies == 2  # Dropped payloads were never copied/encoded.
        with pytest.raises(RuntimeError, match="Stop recording"):
            recorder.overflow_policy = "stop"
        gated.release.set()
        assert wait_for(lambda: recorder.count == 2)
        recorder.capture(frame(codec.protocol, 8))
        assert wait_for(lambda: recorder.count == 3)
    finally:
        gated.release.set()
        recorder.stop()
        recorder._thread.join(5)
    assert not recorder.busy and recorder.pending_path is not None
    assert gated.copies == 3
    saved = list(codec.read(recorder.pending_path))
    assert len(saved) == 3
    assert [event.direction for event in saved] == ["rx", "tx", "rx"]
    assert [event.timestamp for event in saved] == pytest.approx(
        [first[0].timestamp, first[1].timestamp, frame(codec.protocol, 8).timestamp]
    )
    info = metadata(recorder.pending_path)
    assert info["overflow_policy"] == "continue" and info["incomplete"]
    assert info["accepted_events"] == info["recorded_frames"] == 3
    assert info["dropped_events"] == 6
    assert info["dropped_rx_events"] == info["dropped_tx_events"] == 3
    assert info["first_dropped_timestamp"] == frame(codec.protocol, 2).timestamp
    assert info["last_dropped_timestamp"] == frame(codec.protocol, 7).timestamp
    # Saving and starting a new session resets loss counts and warning state.
    recorder.save_as(tmp_path / f"{codec.protocol}-saved.mf4")
    recorder.start()
    try:
        recorder.capture(frame(codec.protocol, 9))
    finally:
        recorder.stop()
        recorder._thread.join(5)
    assert recorder.dropped == 0 and not recorder.problem
    clean = metadata(recorder.pending_path)
    assert clean["dropped_events"] == 0 and not clean["incomplete"]
    assert clean["first_dropped_timestamp"] is None


def test_default_stop_counts_triggering_loss_and_drains_queued_data(tmp_path):
    codec = GatedCodec(CANCodec())
    recorder = MF4Recorder(queue_size=2, temp_directory=tmp_path, codec=codec)
    recorder.start()
    assert codec.entered.wait(2)
    try:
        for index in range(10):
            recorder.capture(frame("CAN", index))
        assert not recorder.recording
        assert recorder.dropped == 1 and recorder.accepted == 2
        assert "overflow" in recorder.problem
    finally:
        codec.release.set()
        recorder.stop()
        recorder._thread.join(5)
    info = metadata(recorder.pending_path)
    assert info["overflow_policy"] == "stop" and info["dropped_events"] == 1
    assert info["recorded_frames"] == 2 and info["incomplete"]
    assert "traffic after logging stops" in info["loss_count_scope"]


def test_busy_burst_has_bounded_notifications_and_no_capture_thread_disk_logging(
    tmp_path,
):
    codec = GatedCodec(CANCodec())
    recorder = MF4Recorder(
        queue_size=1, temp_directory=tmp_path, codec=codec, overflow_policy="continue"
    )
    notices = []
    recorder.changed.connect(
        lambda: notices.append(True), Qt.ConnectionType.DirectConnection
    )
    recorder.start()
    assert codec.entered.wait(2)
    try:
        recorder.capture(frame("CAN", 0))
        notices.clear()
        with (
            patch("signal_explorer.recording.recorder.time.monotonic", return_value=1),
            patch("signal_explorer.recording.recorder.logger") as logger,
        ):
            for _ in range(5000):
                recorder.capture(frame("CAN", 1))
            assert len(notices) == 1
            logger.warning.assert_not_called()
            logger.error.assert_not_called()
        assert recorder.dropped == 5000 and codec.copies == 1
    finally:
        codec.release.set()
        recorder.stop()
        recorder._thread.join(5)


def test_simultaneous_rx_tx_producers_account_for_every_active_capture(tmp_path):
    codec = GatedCodec(CANCodec())
    recorder = MF4Recorder(
        queue_size=2, temp_directory=tmp_path, codec=codec, overflow_policy="continue"
    )
    recorder.start()
    assert codec.entered.wait(2)

    def produce(direction):
        for index in range(200):
            recorder.capture(frame("CAN", index, direction))

    workers = [
        threading.Thread(target=produce, args=(direction,))
        for direction in ("rx", "tx")
    ]
    try:
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(2)
            assert not worker.is_alive()
        stats = recorder.loss_statistics()
        assert stats["accepted_events"] == 2
        assert stats["dropped_events"] == 398
        assert stats["dropped_rx_events"] + stats["dropped_tx_events"] == 398
    finally:
        codec.release.set()
        recorder.stop()
        recorder._thread.join(5)


@pytest.mark.parametrize("queue_size", [0, -1, 1.5, True])
def test_queue_must_remain_bounded(queue_size):
    with pytest.raises(ValueError, match="positive integer"):
        MF4Recorder(queue_size=queue_size)


def test_invalid_policy_is_rejected():
    with pytest.raises(ValueError, match="stop or continue"):
        MF4Recorder(overflow_policy="silent-drop")


def test_write_failure_stops_continue_mode_and_keeps_loss_metadata(tmp_path):
    class FailedWriter:
        info = None

        def write(self, event):
            raise OSError("test disk failure")

        def finish(self, info):
            self.info = dict(info)

    writer = FailedWriter()

    class Codec(CANCodec):
        def writer(self, path, database=None):
            return writer

    codec = GatedCodec(Codec())
    recorder = MF4Recorder(
        queue_size=1, temp_directory=tmp_path, codec=codec, overflow_policy="continue"
    )
    recorder.start()
    assert codec.entered.wait(2)
    try:
        recorder.capture(frame("CAN", 0))
        recorder.capture(frame("CAN", 1))
        assert recorder.recording
    finally:
        codec.release.set()
        recorder._thread.join(5)
    assert not recorder.busy and not recorder.recording
    assert "test disk failure" in recorder.problem
    assert writer.info["incomplete"]
    assert writer.info["dropped_events"] == 1 and writer.info["recorded_frames"] == 0
    assert "disk failure" in writer.info["incomplete_reason"]


def test_preencoded_event_overflow_with_invalid_timestamp_is_json_safe(tmp_path):
    codec = GatedCodec(CANCodec())
    recorder = MF4Recorder(
        queue_size=1, temp_directory=tmp_path, codec=codec, overflow_policy="continue"
    )
    recorder.start()
    assert codec.entered.wait(2)
    try:
        recorder.capture(frame("CAN", 0))
        recorder.capture(BusEvent(float("nan"), 1, "tx", "CAN", frame("CAN", 1, "tx")))
        recorder.capture(BusEvent(None, 1, "other", "CAN", frame("CAN", 2)))
        assert recorder.dropped == 2 and codec.copies == 1
    finally:
        codec.release.set()
        recorder.stop()
        recorder._thread.join(5)
    info = metadata(recorder.pending_path)
    assert info["dropped_tx_events"] == 1 and info["dropped_other_events"] == 1
    assert info["first_dropped_timestamp"] is info["last_dropped_timestamp"] is None
    json.dumps(info, allow_nan=False)


def test_ui_continue_mode_warns_on_stop_saves_and_resets(tmp_path):
    class Service(QObject):
        traffic = Signal(object)
        connected = Signal(str)
        disconnected = Signal()
        about_to_disconnect = Signal()
        is_connected = can_replay = True
        fd_enabled = False
        session_info = {}

    service = Service()
    panel = RecordingPanel(service)
    codec = GatedCodec(CANCodec())
    panel.recorder.codec = codec
    panel.recorder.queue_size = 1
    panel.recorder.temp_directory = tmp_path
    output = tmp_path / "overflow.mf4"
    with (
        patch("signal_explorer.ui.recording_panel.QMessageBox.warning") as warning,
        patch(
            "signal_explorer.ui.recording_panel.QFileDialog.getSaveFileName",
            side_effect=[(str(output), ""), (str(tmp_path / "clean.mf4"), "")],
        ),
    ):
        try:
            assert panel.overflow.currentData() == "stop"
            panel.overflow.setCurrentIndex(panel.overflow.findData("continue"))
            panel.log.click()
            assert codec.entered.wait(2)
            for index in range(5):
                service.traffic.emit(frame("CAN", index))
            assert panel.recorder.dropped == 4
            assert panel.log.text() == "Stop Logging"
            assert not panel.overflow.isEnabled()
            assert wait_for(lambda: "Dropped 4" in panel.status.text(), gui=True)
            assert "INCOMPLETE" in panel.status.text()
            warning.assert_not_called()  # Do not interrupt ongoing monitoring.
            codec.release.set()
            # Disconnect stops acceptance and saves with the same loss warning.
            service.about_to_disconnect.emit()
            service.is_connected = False
            service.disconnected.emit()
            assert wait_for(
                lambda: output.exists() and not panel.recorder.busy, gui=True
            )
            warning.assert_called_once()
            assert "4 events" in warning.call_args.args[2]
            assert metadata(output)["dropped_events"] == 4
            assert panel.overflow.isEnabled()
            service.is_connected = True
            service.connected.emit("virtual")
            panel.log.click()
            assert panel.recorder.dropped == 0 and not panel.status.styleSheet()
            service.traffic.emit(frame("CAN", 9))
            panel.log.click()
            assert wait_for(lambda: (tmp_path / "clean.mf4").exists(), gui=True)
            assert warning.call_count == 1
        finally:
            codec.release.set()
            if panel.recorder.busy:
                panel.recorder.stop()
                panel.recorder._thread.join(5)
            app.processEvents()
            panel.close()
