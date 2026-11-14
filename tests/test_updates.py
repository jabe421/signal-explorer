import hashlib
import io
import json
from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QApplication, QLabel, QPushButton

from signal_explorer import updates
from signal_explorer import DEVELOPMENT_VERSION
from signal_explorer.ui.main_window import MainWindow


def release(version, *, prerelease=False, draft=False, asset=True):
    name = f"SignalExplorer-Setup-{version}-windows-x64.exe"
    return {
        "tag_name": f"v{version}",
        "prerelease": prerelease,
        "draft": draft,
        "body": "Release notes",
        "assets": [{
            "name": name,
            "browser_download_url": f"https://github.com/{updates.REPOSITORY}/releases/download/v{version}/{name}",
            "digest": "sha256:" + "a" * 64,
            "size": 120,
        }] if asset else [],
    }


def test_selects_newer_published_installers_for_selected_channel(monkeypatch):
    releases = [
        release("0.4.0", draft=True),
        release("0.3.2", asset=False),
        release("0.3.0-beta.10", prerelease=True),
        release("0.3.0-beta.2", prerelease=True),
        release("0.2.0"),
    ]
    monkeypatch.setattr(updates, "urlopen", lambda *a, **kw: io.BytesIO(json.dumps(releases).encode()))
    stable = updates.find_update("0.3.0-beta.2", False)
    assert stable.latest_version == "0.3.2"
    assert stable.latest_notes == "Release notes"
    assert stable.update is None
    assert updates.find_update("0.3.0-beta.2", True).update.version == "0.3.0-beta.10"
    releases.append(release("0.3.0"))
    assert updates.find_update("0.3.0-beta.10", True).update.version == "0.3.0"
    assert updates.find_update("0.3.0-beta.10", False).update.version == "0.3.0"
    assert updates.find_update("0.3.0", True).update is None
    releases[:] = [release("0.3.0")]
    assert updates.find_update("0.3.0", False) == updates.UpdateCheck("0.3.0", None)
    assert updates.find_update(None, False) == updates.UpdateCheck("0.3.0", None)


def test_development_help_menu_shows_versions_without_install(monkeypatch):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr("signal_explorer.diagnostics.installed_build_info",
                        lambda: {"source_checkout": True})
    monkeypatch.setattr("signal_explorer.ui.update_dialog.UpdateDialog.check", lambda self: None)
    window = MainWindow()
    try:
        help_menu = next(action.menu() for action in window.menuBar().actions()
                         if action.text() == "Help")
        next(action for action in help_menu.actions()
             if action.text() == "Check for Updates...").trigger()
        dialog = window._update_dialog
        dialog._results.put((False, updates.UpdateCheck("0.5.0", None, "New release notes"), None))
        dialog._receive_result()
        labels = [label.text() for label in dialog.findChildren(QLabel)]
        assert f"Current version: {DEVELOPMENT_VERSION} (development build)" in labels
        assert "Newest available version: 0.5.0" in labels
        assert "New release notes" in dialog._notes.toPlainText()
        assert dialog._download.isVisible()
        assert not dialog._download.isEnabled()
        assert "installed Windows app" in dialog._status.text()
    finally:
        window.close()
        app.processEvents()


def test_update_button_downloads_and_hands_off_verified_installer(monkeypatch, tmp_path):
    from signal_explorer.ui.update_dialog import UpdateDialog

    app = QApplication.instance() or QApplication([])
    installer = tmp_path / "Setup.exe"
    candidate = updates.Update("0.5.0", "Installer release notes", "Setup.exe", "", "", 120)
    handoff = []
    attempts = []

    def download(update, *, progress):
        attempts.append(update)
        progress(60, 120)
        dialog._receive_result()
        assert dialog._progress.value() == 50
        return installer

    class ImmediateThread:
        def __init__(self, *, target, **kwargs):
            self.target = target

        def start(self):
            self.target()

    monkeypatch.setattr("signal_explorer.ui.update_dialog.download_update", download)
    monkeypatch.setattr("signal_explorer.ui.update_dialog.Thread", ImmediateThread)
    dialog = UpdateDialog("0.4.0", handoff.append, None)
    try:
        buttons = dialog.layout().itemAt(dialog.layout().count() - 1).layout()
        assert [buttons.itemAt(i).widget().text() for i in range(buttons.count())] == [
            "Check Again", "Update", "Later",
        ]
        update_button = next(button for button in dialog.findChildren(QPushButton)
                             if button.text() == "Update")
        assert not update_button.isEnabled()
        dialog._results.put((False, updates.UpdateCheck("0.6.0", None, "Notes only"), None))
        dialog._receive_result()
        assert not update_button.isEnabled()
        assert "Notes only" in dialog._notes.toPlainText()
        dialog._results.put((False, updates.UpdateCheck("0.6.0", candidate, "Notes only"), None))
        dialog._receive_result()
        assert "Release notes for 0.5.0" in dialog._notes.toPlainText()
        update_button.click()
        assert attempts == [candidate]
        assert handoff == []
        dialog._receive_result()
        assert dialog._progress.value() == 100
        assert "Closing Signal Explorer" in dialog._status.text()
        assert not update_button.isEnabled()
        assert handoff == []
        app.processEvents()
        assert handoff == [installer]
        dialog.finish_install_shutdown(cancelled=True)
        assert "Update canceled" in dialog._status.text()
        assert dialog._progress.isHidden()
        assert update_button.isEnabled()
    finally:
        dialog.close()
        app.processEvents()


@pytest.mark.parametrize("payload", [b"installer", b"corrupted", b"short", b"oversized installer"])
def test_download_requires_matching_digest_and_size(monkeypatch, tmp_path, payload):
    monkeypatch.setattr(updates.tempfile, "tempdir", str(tmp_path))
    monkeypatch.setattr(updates, "build_opener", lambda *a: SimpleNamespace(
        open=lambda *a, **kw: io.BytesIO(payload),
    ))
    expected = b"installer"
    update = updates.Update("0.3.0", "", "Setup.exe", "https://github.com/installer",
                            hashlib.sha256(expected).hexdigest(), len(expected))
    progress = []
    if payload == expected:
        assert updates.download_update(update, progress=lambda *args: progress.append(args)).read_bytes() == expected
        assert progress == [(len(expected), len(expected))]
    else:
        with pytest.raises(ValueError):
            updates.download_update(update)
        assert not list(tmp_path.iterdir())


def test_shutdown_veto_discards_update_and_success_hands_it_off(monkeypatch, tmp_path):
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    directory = tmp_path / "update"
    directory.mkdir()
    path = directory / "Setup.exe"
    path.touch()
    with monkeypatch.context() as patch:
        patch.setattr(window._transmit_window, "request_close_for_main", lambda: False)
        window._install_update(path)
        assert window.installer_on_exit is None
        assert window._pending_update is None
        assert not path.exists()
    directory.mkdir()
    path.touch()
    window._install_update(path)
    assert window.installer_on_exit == path
    assert path.exists()
    app.processEvents()
