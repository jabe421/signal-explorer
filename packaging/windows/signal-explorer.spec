"""Two entry points share one analysis and one onedir dependency folder."""
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata

root = Path(SPECPATH).resolve().parents[1]
datas = collect_data_files('signal_explorer', includes=['assets/*', 'lin/*.ldf', 'scripting/demo_can.py'])
datas += [(str(root/'src/signal_explorer/scripting/demo_can.py'), 'signal_explorer/scripting')]
datas += collect_data_files('asammdf')
for distribution in ('signal-explorer', 'python-can', 'asammdf', 'cantools', 'ldfparser', 'python-ics', 'nixnet'):
    datas += copy_metadata(distribution)

hidden = [
    'can.interfaces.virtual', 'can.interfaces.slcan', 'can.interfaces.gs_usb',
    'can.interfaces.vector', 'can.interfaces.kvaser', 'can.interfaces.pcan',
    'can.interfaces.ics_neovi', 'signal_explorer.scripting.worker',
    'signal_explorer.packaging_smoke', 'nixnet', 'ics', 'gs_usb', 'usb.backend.libusb1',
    # Standard helpers commonly used by local test scripts are dynamic imports.
    'struct', 'csv', 'json', 'math', 'statistics', 'random', 'decimal', 'fractions',
    'datetime', 'collections', 'itertools', 'functools', 're', 'pathlib',
    'socket', 'ipaddress', 'hashlib', 'logging', 'threading', 'queue', 'enum',
]
hidden += collect_submodules('nixnet')

a = Analysis([str(root/'packaging/windows/launcher.py')], pathex=[str(root/'src')],
             binaries=[], datas=datas, hiddenimports=hidden,
             excludes=['PyQt5', 'PyQt6', 'PySide2', 'tkinter', 'matplotlib',
                       'PySide6.QtWebEngineCore', 'PySide6.QtWebEngineWidgets',
                       'PySide6.QtPdf', 'PySide6.QtPdfWidgets'],
             hookspath=[], runtime_hooks=[], noarchive=False)
# Never collect a developer PC's separately installed proprietary driver/API.
# Optional Python bindings are packaged; vendor driver installers remain separate.
vendor_dlls = {'vxlapi64.dll', 'vxlapi.dll', 'canlib32.dll', 'pcanbasic.dll',
               'plinapi.dll', 'icsneo40.dll', 'nixnet.dll', 'wpcap.dll', 'packet.dll'}
a.binaries = [item for item in a.binaries if Path(item[0]).name.lower() not in vendor_dlls]
# Qt uses Windows' unversioned ICU API. A developer's PATH can expose an
# incompatible third-party icuuc.dll (with version-suffixed exports) instead.
# Never shadow the operating-system ICU implementation in the app bundle.
a.binaries = [item for item in a.binaries if Path(item[0]).name.lower() != 'icuuc.dll']
pyz = PYZ(a.pure)
gui = EXE(pyz, a.scripts, [], exclude_binaries=True, name='SignalExplorer',
          debug=False, strip=False, upx=False, console=False,
          icon=str(root/'src/signal_explorer/assets/signal-explorer.ico'))
worker = EXE(pyz, a.scripts, [], exclude_binaries=True, name='SignalExplorer-ScriptWorker',
             debug=False, strip=False, upx=False, console=True,
             icon=str(root/'src/signal_explorer/assets/signal-explorer.ico'))
bundle = COLLECT(gui, worker, a.binaries, a.datas, strip=False, upx=False, name='SignalExplorer')
