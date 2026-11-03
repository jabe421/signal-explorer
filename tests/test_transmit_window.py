import os
os.environ['QT_QPA_PLATFORM']='offscreen'
from copy import deepcopy
from dataclasses import replace
import json
import time
from uuid import uuid4
from threading import Event
from unittest.mock import patch

import can
import cantools
import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QMessageBox, QPushButton, QComboBox, QLineEdit
from PySide6.QtTest import QTest
from signal_explorer.bus.backends import BACKENDS
from signal_explorer.bus.service import BusService
from signal_explorer.bus.transmit import TxEntry, Waveform, TransmitEngine, dbc_entry, encode_entries, decode_entries
from signal_explorer.recording.recorder import MF4Recorder
from signal_explorer.ui.main_window import MainWindow
from signal_explorer.ui.transmit_window import TransmitWindow
from test_multichannel import LoopBus, configs

app=QApplication.instance() or QApplication([])
VIRTUAL=next(b for b in BACKENDS if b.interface=='virtual')


def wait_for(predicate, timeout=3):
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        app.processEvents()
        if predicate():return True
        time.sleep(.005)
    return False


@pytest.fixture
def wire():
    name='multi-tx-'+uuid4().hex
    service=BusService();service.connect_bus(VIRTUAL,name,500000,False,fd=True)
    peer=can.Bus(interface='virtual',channel=name,ignore_config=True)
    yield service,peer
    service.disconnect_bus();peer.shutdown()


def test_independent_rates_once_stop_and_no_restart_after_reconnect(wire):
    service,peer=wire;engine=TransmitEngine(service)
    try:
        fast=TxEntry(identifier=0x100,period_ms=20,cyclic=True,data=b'12345678')
        slow=TxEntry(identifier=0x200,period_ms=80,cyclic=True,data=b'abcdefgh')
        engine.add(fast);engine.add(slow)
        engine.start([fast.uid,slow.uid],True)
        assert wait_for(lambda:engine.state(slow.uid)[0]>=3)
        assert engine.state(fast.uid)[0]>engine.state(slow.uid)[0]
        engine.update(replace(fast,data=bytes(8)))
        assert engine.get(fast.uid).data==bytes(8) and engine.state(fast.uid)[1]
        engine.stop([fast.uid])
        assert wait_for(lambda:not engine.state(fast.uid)[2])
        count=engine.state(fast.uid)[0]
        time.sleep(.1)
        assert engine.state(fast.uid)[0]==count and engine.state(slow.uid)[1]
        service.disconnect_bus()
        assert wait_for(lambda:not engine.state(slow.uid)[2])
        stopped=engine.state(slow.uid)[0]
        service.connect_bus(VIRTUAL,'reconnected-'+uuid4().hex,500000,False,fd=True)
        time.sleep(.1)
        assert engine.state(slow.uid)[0]==stopped and not engine.state(slow.uid)[1]
        engine.start([fast.uid])
        assert wait_for(lambda:engine.state(fast.uid)[0]==count+1)
        assert not engine.state(fast.uid)[1]
    finally:engine.shutdown()


def test_classic_batch_preflight_and_playback_reject_before_any_send(wire):
    service,peer=wire;service.fd_enabled=False
    busy=[False];engine=TransmitEngine(service,lambda:busy[0])
    try:
        classic=TxEntry();fd=TxEntry(fd=True,brs=True,data=bytes(32))
        engine.add(classic);engine.add(fd)
        with pytest.raises(ValueError,match='CAN FD'):
            engine.start([classic.uid,fd.uid])
        assert peer.recv(.05) is None
        busy[0]=True
        with pytest.raises(ValueError,match='playback'):
            engine.start([classic.uid])
        assert peer.recv(.05) is None
        with pytest.raises(ValueError):
            engine.add(TxEntry(data=bytes(9)))
        with pytest.raises(ValueError):
            engine.add(TxEntry(fd=True,data=bytes(10)))
    finally:engine.shutdown()


def test_rows_route_explicit_channels_fd_and_failures_do_not_stop_other_rows():
    service=BusService();wire=LoopBus();selection=configs()
    with patch('signal_explorer.bus.service.can.Bus',return_value=wire):
        service.connect_bus(next(b for b in BACKENDS if b.interface=='vector'),'0',500000,False,dict(selection[0],channels=selection),fd=True)
    engine=TransmitEngine(service)
    try:
        first=TxEntry(channel='CAN1',identifier=0x111)
        second=TxEntry(channel='CAN2',identifier=0x222,extended=True,fd=True,brs=True,data=bytes(range(64)))
        engine.add(first);engine.add(second);engine.start([first.uid,second.uid])
        assert wait_for(lambda:engine.state(second.uid)[0]==1)
        assert [m.channel for m in wire.sent]==[0,1]
        assert wire.sent[-1].is_extended_id and wire.sent[-1].bitrate_switch and len(wire.sent[-1].data)==64
        bad=TxEntry(channel='CAN3');engine.add(bad)
        with pytest.raises(ValueError,match='not enabled'):engine.start([bad.uid])
        original=service.send_message
        with patch.object(service,'send_message',side_effect=lambda msg,**kw:False if msg.arbitration_id==0x111 else original(msg,**kw)):
            engine.start([first.uid,second.uid],True)
            assert wait_for(lambda:bool(engine.state(first.uid)[3]) and engine.state(second.uid)[0]>=3)
            assert not engine.state(first.uid)[1] and engine.state(second.uid)[1]
    finally:engine.shutdown();service.disconnect_bus()


def test_multi_tx_uses_shared_capture_and_mf4_rx_tx_logging(wire,tmp_path):
    service,peer=wire;engine=TransmitEngine(service);recorder=MF4Recorder(temp_directory=tmp_path)
    service.traffic.connect(recorder.capture,Qt.ConnectionType.DirectConnection)
    try:
        recorder.start(service.session_info)
        entry=TxEntry(identifier=0x321,fd=True,brs=True,data=bytes(range(32)))
        engine.add(entry);engine.start([entry.uid])
        sent=peer.recv(1)
        assert sent and sent.arbitration_id==0x321 and sent.data==bytes(range(32))
        peer.send(can.Message(arbitration_id=0x221,is_extended_id=False,data=bytes(range(32)),is_fd=True,bitrate_switch=True))
        assert wait_for(lambda:recorder.count>=2)
        recorder.stop();recorder._thread.join(10)
        assert not recorder.problem
        with can.MF4Reader(recorder.pending_path) as reader:frames=list(reader)
        assert len(frames)==2 and {m.is_rx for m in frames}=={False,True}
        assert all(m.is_fd and m.bitrate_switch and len(m.data)==32 for m in frames)
    finally:
        engine.shutdown();recorder.stop()
        if recorder._thread:recorder._thread.join(10)


def test_dbc_waveforms_encoding_choices_mux_and_transmit_list_roundtrip():
    database=cantools.database.load_file('examples/demo.dbc')
    rows=[dbc_entry(m) for m in database.messages]
    rpm=next(e for e in rows if e.identifier==0x200)
    rpm=replace(rpm,waves={'EngineRPM':Waveform('Ramp',1000,1002,1,1000)})
    assert [rpm.definition.decode(rpm.frame(tick=i).data)['EngineRPM'] for i in range(4)]==[1000,1001,1002,1000]
    pulse=replace(rpm,waves={'EngineRPM':Waveform('Pulse',1000,2000,1,1000)})
    assert pulse.definition.decode(pulse.frame(elapsed=.75).data)['EngineRPM']==2000
    sine=replace(rpm,waves={'EngineRPM':Waveform('Sine',1000,2000,1,1000)})
    assert sine.definition.decode(sine.frame(elapsed=.5).data)['EngineRPM']==2000
    rows.append(rpm)
    restored=decode_entries(encode_entries(rows))
    assert len(restored)==4 and restored[2].fd and len(restored[2].data)==32
    assert restored[-1].waves==rpm.waves and restored[-1].frame(tick=2).data==rpm.frame(tick=2).data
    assert restored[0].uid!=rows[0].uid
    mux=cantools.database.load_string('''VERSION ""
NS_ :
BS_:
BU_: ECU
BO_ 100 Mux: 2 ECU
 SG_ Selector M : 0|4@1+ (1,0) [0|15] "" ECU
 SG_ Branch m3 : 8|8@1+ (1,0) [0|255] "" ECU
VAL_ 100 Branch 0 "Off" 1 "On";
''',database_format='dbc')
    muxrow=dbc_entry(mux.messages[0])
    assert muxrow.definition.decode(muxrow.data)['Selector']==3
    restored=decode_entries(encode_entries([muxrow]))[0]
    assert str(restored.definition.decode(restored.data)['Branch'])=='Off'


def test_waveform_bounds_fail_preflight_and_bad_lists_are_rejected(wire):
    service,peer=wire;engine=TransmitEngine(service)
    try:
        entry=dbc_entry(cantools.database.load_file('examples/demo.dbc').get_message_by_frame_id(0x200))
        entry=replace(entry,waves={'EngineRPM':Waveform('Ramp',1000,100000,1,1000)})
        engine.add(entry)
        with pytest.raises(Exception):engine.start([entry.uid],True)
        assert peer.recv(.05) is None
        text=encode_entries([TxEntry()]);document=json.loads(text)
        for key,value in [('identifier',0x800),('period_ms',0),('data','00'*9),('uid','injected'),('enabled','true')]:
            invalid=deepcopy(document);invalid['messages'][0][key]=value
            with pytest.raises(Exception):decode_entries(json.dumps(invalid))
    finally:engine.shutdown()


def test_window_byte_signal_editing_clone_and_save_reload_offline(tmp_path):
    service=BusService();database=cantools.database.load_file('examples/demo.dbc')
    window=TransmitWindow(service,lambda:database)
    try:
        entry=dbc_entry(database.get_message_by_frame_id(0x200));window.engine.add(entry)
        assert window.table.rowCount()==1 and window.signals.rowCount()>0
        signal_row=next(i for i in range(window.signals.rowCount()) if window.signals.item(i,0).text()=='EngineRPM')
        window.signals.item(signal_row,4).setText('2000')
        entry=window.engine.get(entry.uid)
        assert entry.definition.decode(entry.data)['EngineRPM']==2000
        window.table.selectRow(0);window.clone()
        assert window.table.rowCount()==2
        window.table.selectRow(1);window.clone()
        assert window.table.rowCount()==3
        window.table.selectRow(2);window.delete()
        assert window.table.rowCount()==2
        path=tmp_path/'messages.json'
        with patch('signal_explorer.ui.transmit_window.QFileDialog.getSaveFileName',return_value=(str(path),'')):
            assert window.save()
        window.add_raw()
        with patch('signal_explorer.ui.transmit_window.QMessageBox.question',return_value=QMessageBox.StandardButton.Discard), \
             patch('signal_explorer.ui.transmit_window.QFileDialog.getOpenFileName',return_value=(str(path),'')):
            window.load()
        assert window.table.rowCount()==2 and not window._dirty
        assert all(not window.engine.state(e.uid)[1] for e in window.engine.entries())
    finally:window.shutdown();window.close()


def test_main_single_sender_remains_and_secondary_window_reuses_and_stops(wire):
    window=MainWindow();service=window._service
    try:
        service.connect_bus(VIRTUAL,'main-multi-'+uuid4().hex,500000,False)
        sender=window._open_transmit_window()
        assert sender is window._open_transmit_window()
        assert window._send.isEnabled()
        entry=TxEntry(cyclic=True,period_ms=20);sender.engine.add(entry);sender._send_row(entry.uid)
        assert wait_for(lambda:sender.engine.state(entry.uid)[0]>=2)
        sender.playback_changed(True)
        assert not sender.engine.state(entry.uid)[1]
        sender._send_row(entry.uid)
        assert wait_for(lambda:sender.engine.state(entry.uid)[0]>=3)
        sender.close()
        assert not sender.engine.state(entry.uid)[1]
        assert window._open_transmit_window() is sender and sender.table.rowCount()==1
        sender._dirty=False
    finally:
        if window._transmit_window:window._transmit_window._dirty=False
        window.close()


def test_signal_edits_and_waveforms_preserve_unassigned_payload_bits():
    from signal_explorer.bus.transmit import encode_signals
    definition=cantools.database.load_string('''VERSION ""
NS_ :
BS_:
BU_: ECU
BO_ 256 Partial: 8 ECU
 SG_ Value : 0|8@1+ (1,0) [0|255] "" ECU
''',database_format='dbc').messages[0]
    seed=bytes.fromhex('01 AA BB CC DD EE FF 55')
    assert encode_signals(definition,seed,{'Value':42})==bytes.fromhex('2A AA BB CC DD EE FF 55')
    row=replace(dbc_entry(definition),data=seed,waves={'Value':Waveform('Ramp',10,20,1,1000)})
    assert row.frame(tick=3).data==bytes.fromhex('0D AA BB CC DD EE FF 55')


def test_stop_all_cancels_queued_once_sends_behind_an_inflight_frame(wire):
    service,peer=wire;engine=TransmitEngine(service)
    entered,release=Event(),Event()
    actual=service.send_message
    def delayed(msg,**kw):
        entered.set();release.wait(1)
        return actual(msg,**kw)
    try:
        entries=[TxEntry(identifier=0x100+i) for i in range(3)]
        for entry in entries:engine.add(entry)
        with patch.object(service,'send_message',side_effect=delayed):
            engine.start([e.uid for e in entries])
            assert entered.wait(1)
            engine.stop_all();release.set()
            assert wait_for(lambda:not engine.state(entries[0].uid)[2])
        assert engine.state(entries[0].uid)[0]==1
        assert all(engine.state(e.uid)[0]==0 for e in entries[1:])
    finally:release.set();engine.shutdown()


def test_rebuilding_table_does_not_leave_old_cell_controls_visible():
    service=BusService();window=TransmitWindow(service)
    try:
        window.show();app.processEvents()
        for i in range(3):window.engine.add(TxEntry(name=f'Row {i}'))
        window.engine.update(replace(window.engine.entries()[0],name='Updated'))
        app.processEvents()
        assert len([w for w in window.table.findChildren(QPushButton) if w.isVisible()])==3
        assert len([w for w in window.table.findChildren(QComboBox) if w.isVisible()])==3
    finally:window.shutdown();window.close()


def test_live_update_is_atomic_during_inflight_send_and_preserves_counts_and_running_state():
    service=BusService();wire=LoopBus();selection=configs()
    with patch('signal_explorer.bus.service.can.Bus',return_value=wire):
        service.connect_bus(next(b for b in BACKENDS if b.interface=='vector'),'0',500000,False,dict(selection[0],channels=selection),fd=True)
    engine=TransmitEngine(service);entered,release=Event(),Event();actual=service.send_message
    def delayed(msg,**kw):
        if not entered.is_set():entered.set();release.wait(2)
        return actual(msg,**kw)
    try:
        old=TxEntry(channel='CAN1',identifier=0x111,data=b'12345678',cyclic=True,period_ms=20)
        engine.add(old)
        with patch.object(service,'send_message',side_effect=delayed):
            engine.start([old.uid],True);assert entered.wait(1)
            new=replace(old,channel='CAN2',identifier=0x1ABCDE,extended=True,fd=True,brs=True,data=bytes(range(32)),period_ms=70)
            engine.update(new)
            assert engine.state(old.uid)[1]
            release.set()
            assert wait_for(lambda:engine.state(old.uid)[0]>=3)
            assert (wire.sent[0].channel,wire.sent[0].arbitration_id,wire.sent[0].data,wire.sent[0].is_fd)==(0,0x111,b'12345678',False)
            assert all(m.channel==1 and m.arbitration_id==0x1ABCDE and m.is_extended_id and m.is_fd and m.bitrate_switch
                       and m.data==bytes(range(32)) for m in wire.sent[1:])
            with pytest.raises(ValueError,match='not enabled'):
                engine.update(replace(new,channel='CAN3'))
            assert engine.get(old.uid).channel=='CAN2' and engine.state(old.uid)[1]
            engine.update(replace(new,enabled=False))
            assert wait_for(lambda:not engine.state(old.uid)[2])
            count=engine.state(old.uid)[0];time.sleep(.1)
            assert engine.state(old.uid)[0]==count and not engine.state(old.uid)[1]
            engine.update(new)
            assert not engine.state(old.uid)[1]
    finally:release.set();engine.shutdown();service.disconnect_bus()


def test_queued_once_send_uses_updated_frame_without_duplicate_transmission(wire):
    service,peer=wire;engine=TransmitEngine(service);entered,release=Event(),Event();actual=service.send_message
    def delayed(msg,**kw):
        if not entered.is_set():entered.set();release.wait(2)
        return actual(msg,**kw)
    try:
        first=TxEntry(identifier=0x100);second=TxEntry(identifier=0x200)
        engine.add(first);engine.add(second)
        with patch.object(service,'send_message',side_effect=delayed):
            engine.start([first.uid,second.uid]);assert entered.wait(1)
            engine.update(replace(second,identifier=0x321,data=b'updated!'))
            release.set();assert wait_for(lambda:engine.state(second.uid)[0]==1)
        frames=[peer.recv(.2),peer.recv(.2)]
        assert [m.arbitration_id for m in frames]==[0x100,0x321]
        assert frames[-1].data==b'updated!' and peer.recv(.05) is None
    finally:release.set();engine.shutdown()


def test_live_ui_editing_preserves_typed_text_and_manual_signal_edits_override_wave(wire):
    service,peer=wire;window=TransmitWindow(service)
    try:
        row=TxEntry(cyclic=True,period_ms=20);window.engine.add(row);window.show();app.processEvents()
        window.engine.start([row.uid],True)
        assert wait_for(lambda:window.engine.state(row.uid)[0]>=2)
        assert window.table.cellWidget(0,3).isEnabled() and not window.payload.isReadOnly()
        item=window.table.item(0,11);window.table.setCurrentCell(0,11);window.table.editItem(item)
        editor=window.table.findChild(QLineEdit)
        assert editor is not None
        editor.setText('AB');window._refresh_status()
        assert editor.text()=='AB'
        QTest.keyClick(editor,Qt.Key.Key_Return)
        assert wait_for(lambda:window.engine.last_data(row.uid)[0]==0xAB)
        window.payload.setFocus();app.processEvents()
        QTest.keyClick(window.payload,Qt.Key.Key_A,Qt.KeyboardModifier.ControlModifier)
        QTest.keyClicks(window.payload,'01 02 03 04 05 06 07 08')
        window._refresh_status()
        assert window.payload.text()=='01 02 03 04 05 06 07 08'
        window.payload.clearFocus();app.processEvents()
        assert wait_for(lambda:window.engine.get(row.uid).data==bytes(range(1,9)))
        dbc=dbc_entry(cantools.database.load_file('examples/demo.dbc').get_message_by_frame_id(0x200))
        dbc=replace(dbc,cyclic=True,period_ms=20,waves={'EngineRPM':Waveform('Ramp',800,1600,100,1000)})
        window.engine.add(dbc);window.table.setCurrentCell(1,1);window.engine.start([dbc.uid],True)
        assert wait_for(lambda:window.engine.state(dbc.uid)[0]>=2)
        window.payload.setFocus();app.processEvents();window.payload.clearFocus();app.processEvents()
        assert window.engine.get(dbc.uid).waves
        signal_row=next(i for i in range(window.signals.rowCount()) if window.signals.item(i,0).text()=='EngineRPM')
        assert window.signals.item(signal_row,4).flags() & Qt.ItemFlag.ItemIsEditable
        assert window.signals.cellWidget(signal_row,8).isEnabled()
        window.signals.item(signal_row,4).setText('2000')
        assert wait_for(lambda:dbc.definition.decode(window.engine.last_data(dbc.uid))['EngineRPM']==2000)
        assert not window.engine.get(dbc.uid).waves and window.engine.state(dbc.uid)[1]
        window.table.item(1,9).setCheckState(Qt.CheckState.Unchecked)
        assert not window.engine.state(dbc.uid)[1]
    finally:window.shutdown();window.close()


def test_open_save_buttons_replace_clipboard_actions_and_keep_file_menu(tmp_path):
    window=TransmitWindow(BusService())
    try:
        buttons={button.text():button for button in window.findChildren(QPushButton)}
        assert {'Open TX List','Save TX List','Clone'} <= set(buttons)
        assert not {'Cut','Copy','Paste'} & set(buttons)
        menu=window.menuBar().actions()[0].menu()
        assert [action.text() for action in menu.actions()]==['Open transmit list...','Save transmit list...']
        window.add_raw();path=tmp_path/'tx.json'
        with patch('signal_explorer.ui.transmit_window.QFileDialog.getSaveFileName',return_value=(str(path),'')):
            buttons['Save TX List'].click()
        assert path.exists()
        window.add_raw()
        with patch('signal_explorer.ui.transmit_window.QMessageBox.question',return_value=QMessageBox.StandardButton.Discard), \
             patch('signal_explorer.ui.transmit_window.QFileDialog.getOpenFileName',return_value=(str(path),'')):
            buttons['Open TX List'].click()
        assert len(window.engine.entries())==1
    finally:window.shutdown();window.close()
