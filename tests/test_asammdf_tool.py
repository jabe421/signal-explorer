from unittest.mock import patch

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from signal_explorer.ui.asammdf_tool import (
    AsammdfLocationDialog, DOWNLOAD_URL, LOCATION_KEY, open_in_asammdf,
)


def test_location_validation_download_and_launch(tmp_path):
    app = QApplication.instance() or QApplication([])
    settings = QSettings(str(tmp_path / 'settings.ini'), QSettings.Format.IniFormat)
    executable = tmp_path / 'asammdf tool' / 'asammdfgui.exe'
    executable.parent.mkdir()
    executable.touch()
    recording = tmp_path / 'test log.mf4'
    recording.touch()
    module = 'signal_explorer.ui.asammdf_tool'
    with patch(f'{module}.QSettings', return_value=settings):
        dialog = AsammdfLocationDialog()
        dialog.path.setText(str(tmp_path / 'missing.exe'))
        with patch(f'{module}.QMessageBox.warning') as warning:
            dialog.accept()
            warning.assert_called_once()
            assert not settings.contains(LOCATION_KEY)
        with patch(f'{module}.QDesktopServices.openUrl', return_value=True) as download:
            dialog.download()
            assert download.call_args.args[0].toString() == DOWNLOAD_URL
        dialog.path.setText(str(executable))
        dialog.accept()
        with patch(f'{module}.subprocess.Popen') as launch:
            launch.return_value.poll.return_value = 0
            open_in_asammdf(None, recording)
            assert launch.call_args.args == (
                [str(executable.resolve()), '--measurements', str(recording.resolve())],
            )
            assert launch.call_args.kwargs['cwd'] == executable.parent
        executable.unlink()
        with (patch(f'{module}.configure_asammdf', return_value=False) as configure,
              patch(f'{module}.subprocess.Popen') as launch):
            open_in_asammdf(None, recording)
            configure.assert_called_once()
            launch.assert_not_called()
    dialog.close()


def test_first_launch_asks_for_location(tmp_path):
    app = QApplication.instance() or QApplication([])
    settings = QSettings(str(tmp_path / 'first.ini'), QSettings.Format.IniFormat)
    module = 'signal_explorer.ui.asammdf_tool'
    with (patch(f'{module}.QSettings', return_value=settings),
          patch(f'{module}.configure_asammdf', return_value=False) as configure):
        open_in_asammdf(None)
        configure.assert_called_once_with(None)
