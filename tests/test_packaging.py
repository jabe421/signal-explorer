"""Installed builds keep user files writable and fail clearly without a worker."""
import os
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
from pathlib import Path
import sys
from unittest.mock import patch

import pytest
from PySide6.QtWidgets import QApplication
from signal_explorer.paths import save_path
from signal_explorer.bus.backends import BACKENDS
from signal_explorer.bus.service import BusService
from signal_explorer.scripting.controller import ScriptController
from signal_explorer.scripting.transport import CanScriptTransport

app = QApplication.instance() or QApplication([])


def test_installed_save_defaults_and_bundled_script_copy(tmp_path):
    install = tmp_path/'Program Files/Signal Explorer'
    documents = tmp_path/'Documents'
    original = install/'examples/scripts/test.py'
    original.parent.mkdir(parents=True)
    original.write_text('print("original")', encoding='utf-8')
    with patch.object(sys, 'frozen', True, create=True), patch.object(sys, 'executable', str(install/'SignalExplorer.exe')):
        with patch('signal_explorer.paths.QStandardPaths.writableLocation', return_value=str(documents)):
            assert Path(save_path('log.mf4')) == documents/'Signal Explorer/log.mf4'
            assert Path(save_path('test.py', original)) == documents/'Signal Explorer/test.py'
            user_file = tmp_path/'my tests/custom.py'
            assert Path(save_path('test.py', user_file)) == user_file
    assert original.read_text(encoding='utf-8') == 'print("original")'
    assert not (original.parent/'log.mf4').exists()


def test_missing_packaged_worker_does_not_start_or_disconnect_bus(tmp_path):
    service = BusService()
    controller = ScriptController(CanScriptTransport(service))
    service.connect_bus(next(b for b in BACKENDS if b.interface == 'virtual'), 'packaged-worker-test', 500000, False)
    try:
        with patch.object(sys, 'frozen', True, create=True), patch.object(sys, 'executable', str(tmp_path/'SignalExplorer.exe')):
            with pytest.raises(RuntimeError, match='Script worker is missing'):
                controller.start('se.can.send(0x123, bytes(8))')
        assert not controller.active
        assert service.is_connected
        assert not controller._accepting
    finally:
        controller.stop()
        service.disconnect_bus()
