"""Run guide listings through the actual script worker and private CAN peers."""
import os
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
from pathlib import Path
import time
import unittest
import uuid

import can
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
from signal_explorer.bus.backends import BACKENDS
from signal_explorer.bus.service import BusService
from signal_explorer.scripting.controller import ScriptController
from signal_explorer.scripting.transport import CanScriptTransport

app = QApplication.instance() or QApplication([])
EXAMPLES = Path('examples/scripts/mcu')


def wait_for(predicate, timeout=8):
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        app.processEvents()
        if predicate():return True
        time.sleep(.005)
    return False


class GuideExamples(unittest.TestCase):
    def setUp(self):
        channel='guide-'+uuid.uuid4().hex
        self.service=BusService()
        self.service.connect_bus(next(b for b in BACKENDS if b.interface=='virtual'),channel,500000,True,fd=True)
        self.controller=ScriptController(CanScriptTransport(self.service))
        self.output=[];self.sent=[]
        self.controller.output.connect(self.output.append)
        self.service.traffic.connect(lambda m:self.sent.append(m) if not m.is_rx else None)
        self.peer=can.Bus(interface='virtual',channel=channel,ignore_config=True)
        self.adc=2048;self.boots=7
        self.timer=QTimer();self.timer.setInterval(5);self.timer.timeout.connect(self.poll);self.timer.start()

    def tearDown(self):
        self.controller.stop();self.assertTrue(wait_for(lambda:not self.controller.active))
        self.timer.stop();self.peer.shutdown();self.service.disconnect_bus();app.processEvents()

    def send(self,identifier,data):
        self.peer.send(can.Message(arbitration_id=identifier,is_extended_id=False,data=data))

    def poll(self):
        for _ in range(100):
            frame=self.peer.recv(0)
            if frame is None:return
            data=frame.data
            if frame.arbitration_id==0x500 and len(data)==8:
                opcode,transaction=data[0],data[7]
                if opcode==1:
                    self.send(0x501,[0x81,data[1],self.adc&255,self.adc>>8,0,0,0,transaction])
                elif opcode in (2,3):
                    if opcode==2:self.boots=(self.boots+1)&65535
                    self.send(0x502,[0xB0,self.boots&255,self.boots>>8,0,0,0,0,transaction])
                else:self.send(0x501,[0xFF,0x11,0,0,0,0,0,transaction])
            elif frame.arbitration_id==0x700 and bytes(data[:4])==bytes([3,0x22,0x12,0x34]):
                self.send(0x708,[3,0x7F,0x22,0x78,0,0,0,0])
                self.send(0x708,[5,0x62,0x12,0x34,0x12,0x34,0,0])

    def run_example(self,path,confirmed=False):
        source=path.read_text(encoding='utf-8')
        if confirmed:
            source=source.replace('FIRMWARE_CONTRACT_CONFIRMED = False','FIRMWARE_CONTRACT_CONFIRMED = True')
        self.output.clear();self.controller.start(source,str(path.resolve()))
        self.assertTrue(wait_for(lambda:not self.controller.active),''.join(self.output))
        return self.controller.outcome

    def test_all_guide_sources_compile_and_fit_the_pdf_listing_width(self):
        files=list(EXAMPLES.glob('*.py'));self.assertEqual(len(files),9)
        for path in files:
            with self.subTest(path=path.name):
                source=path.read_text(encoding='utf-8');compile(source,str(path),'exec')
                self.assertLessEqual(max(map(len,source.splitlines())),88)

    def test_builtin_simulation_examples(self):
        for prefix in ('01','02','03','04','09'):
            path=next(EXAMPLES.glob(prefix+'*.py'))
            with self.subTest(path=path.name):
                self.assertEqual(self.run_example(path),'passed',''.join(self.output))

    def test_mcu_templates_reject_unconfirmed_contract_before_tx(self):
        for prefix in ('05','06','07','08'):
            with self.subTest(prefix=prefix):
                self.sent.clear()
                self.assertEqual(self.run_example(next(EXAMPLES.glob(prefix+'*.py'))),'failed')
                self.assertFalse(self.sent)

    def test_mcu_templates_with_mock_firmware_and_real_script_runner(self):
        for prefix in ('05','06','07','08'):
            with self.subTest(prefix=prefix):
                self.assertEqual(self.run_example(next(EXAMPLES.glob(prefix+'*.py')),True),'passed',''.join(self.output))

    def test_adc_acceptance_failure_is_reported(self):
        self.adc=5000
        self.assertEqual(self.run_example(next(EXAMPLES.glob('05*.py')),True),'failed')
        self.assertIn('FAIL: ADC fits a 12-bit reading',''.join(self.output))


if __name__=='__main__':unittest.main()
