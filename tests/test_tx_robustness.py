import os
os.environ['QT_QPA_PLATFORM']='offscreen'
import errno
import time
from threading import Event, get_ident
from unittest.mock import patch
import can
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
from signal_explorer.bus.service import BusService
from signal_explorer.bus.backends import BACKENDS
from signal_explorer.bus.tx_dispatch import TxDispatcher, TxFailure, send_failure
from signal_explorer.bus.transmit import TransmitEngine, TxEntry
from signal_explorer.scripting.controller import ScriptController
from signal_explorer.scripting.transport import CanScriptTransport
from test_multichannel import LoopBus, wait_for

app=QApplication.instance() or QApplication([])
VIRTUAL=next(b for b in BACKENDS if b.interface=='virtual')


def test_slow_connect_and_cancel_late_result_never_adopts_or_leaks_handle():
    service=BusService(); entered=Event(); release=Event(); wire=LoopBus(); calls=[]
    def factory(**kwargs):
        calls.append(get_ident()); entered.set(); release.wait(2); return wire
    try:
        with patch('signal_explorer.bus.service.can.Bus',side_effect=factory):
            before=time.monotonic(); service.connect_async(VIRTUAL,'cancel-open',500000,False)
            assert time.monotonic()-before < .1 and entered.wait(1)
            pulses=[];timer=QTimer();timer.setInterval(5);timer.timeout.connect(lambda:pulses.append(1));timer.start()
            assert wait_for(lambda:len(pulses)>2)
            service.cancel_connect();release.set()
            assert wait_for(lambda:not service.busy)
            timer.stop()
        assert wire.closed and not service.is_connected and calls[0]!=get_ident()
    finally: release.set();service.disconnect_bus()


def test_async_connect_adopts_on_qt_thread_and_reports_failure():
    service=BusService(); seen=[];errors=[]
    service.connected.connect(lambda text:seen.append(get_ident()));service.error.connect(errors.append)
    try:
        service.connect_async(VIRTUAL,'async-open',500000,False)
        assert wait_for(lambda:service.is_connected)
        assert seen==[get_ident()]
        service.disconnect_async();assert wait_for(lambda:not service.busy)
        with patch('signal_explorer.bus.service.open_bus',side_effect=RuntimeError('open failed')):
            service.connect_async(VIRTUAL,'async-failure',500000,False)
            assert wait_for(lambda:not service.busy)
        assert errors and 'open failed' in errors[-1] and not service.is_connected
    finally:service.disconnect_bus()


def test_bounded_lanes_and_independent_handle_progress():
    dispatcher=TxDispatcher(['A','B']);entered=Event();release=Event()
    def blocked():entered.set();release.wait(2);return 1
    try:
        first=dispatcher.submit('A',blocked);assert entered.wait(1)
        queued=[dispatcher.submit('A',lambda:1) for _ in range(512)]
        rejected=dispatcher.submit('A',lambda:1)
        assert isinstance(rejected.exception(),TxFailure) and rejected.exception().retryable
        assert dispatcher.submit('B',lambda:42).result(.5)==42
        for future in queued:assert future.cancel()
        assert len(dispatcher.lanes['A'].queue)==0
        release.set();assert first.result(1)==1
    finally:release.set();dispatcher.stop();dispatcher.join()


def test_cyclic_rows_retry_explicit_rejection_with_backoff_and_bound():
    service=BusService();service.connect_bus(VIRTUAL,'retry',500000,False)
    engine=TransmitEngine(service);attempts={};healthy=[]
    def send(msg,timeout):
        attempts[msg.arbitration_id]=attempts.get(msg.arbitration_id,0)+1
        if msg.arbitration_id==0x100 and attempts[0x100]<=2:
            raise BlockingIOError(errno.EAGAIN,'queue full')
        if msg.arbitration_id==0x200:raise BlockingIOError(errno.EAGAIN,'queue full')
        if msg.arbitration_id==0x300:healthy.append(time.monotonic())
    try:
        with patch.object(service._bus,'send',side_effect=send):
            rows=[TxEntry(identifier=i,cyclic=True,period_ms=10) for i in (0x100,0x200,0x300)]
            for row in rows:engine.add(row)
            engine.start([r.uid for r in rows],True)
            assert wait_for(lambda:engine.state(rows[0].uid)[0]>=2 and not engine.state(rows[1].uid)[1])
            assert attempts[0x200]==4 and len(healthy)>=3
            assert engine.diagnostics(rows[0].uid)['retries']==2
            assert engine.diagnostics(rows[1].uid)['retries']==3
    finally:engine.shutdown();service.disconnect_bus()


def test_uncertain_timeout_is_not_retried():
    assert not send_failure(can.CanTimeoutError('ACK timeout',error_code=11),'vector').retryable
    service=BusService();service.connect_bus(VIRTUAL,'uncertain',500000,False)
    engine=TransmitEngine(service); row=TxEntry(cyclic=True,period_ms=10);engine.add(row)
    try:
        with patch.object(service._bus,'send',side_effect=can.CanTimeoutError('outcome unknown')) as send:
            engine.start([row.uid],True)
            assert wait_for(lambda:bool(engine.state(row.uid)[3]))
            assert not engine.state(row.uid)[1] and send.call_count==1
    finally:engine.shutdown();service.disconnect_bus()


def test_script_send_and_disconnect_do_not_block_qt_on_stuck_driver():
    service=BusService();service.connect_bus(VIRTUAL,'stuck-script',500000,False)
    controller=ScriptController(CanScriptTransport(service))
    entered=Event();release=Event();actual=service._bus.send
    def slow(msg,timeout):entered.set();release.wait(2);return actual(msg,timeout)
    try:
        with patch.object(service._bus,'send',side_effect=slow):
            controller.start('se.can.send(0x321, bytes(8))')
            assert wait_for(entered.is_set)
            pulses=[];timer=QTimer();timer.setInterval(5);timer.timeout.connect(lambda:pulses.append(1));timer.start()
            assert wait_for(lambda:len(pulses)>=3)
            before=time.monotonic();service.disconnect_async()
            assert time.monotonic()-before<.1 and service.busy
            assert wait_for(lambda:len(pulses)>=6)
            release.set();assert wait_for(lambda:not service.busy)
            assert wait_for(lambda:not controller.active)
            timer.stop()
    finally:release.set();controller.stop();service.disconnect_bus()
