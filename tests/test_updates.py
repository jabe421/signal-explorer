import hashlib
import io
import json
from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QApplication

from signal_explorer import updates
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
    assert updates.find_update("0.3.0-beta.2", False) is None
    assert updates.find_update("0.3.0-beta.2", True).version == "0.3.0-beta.10"
    releases.append(release("0.3.0"))
    assert updates.find_update("0.3.0-beta.10", True).version == "0.3.0"
    assert updates.find_update("0.3.0-beta.10", False).version == "0.3.0"
    assert updates.find_update("0.3.0", True) is None


@pytest.mark.parametrize("payload", [b"installer", b"corrupted", b"short", b"oversized installer"])
def test_download_requires_matching_digest_and_size(monkeypatch, tmp_path, payload):
    monkeypatch.setattr(updates.tempfile, "tempdir", str(tmp_path))
    monkeypatch.setattr(updates, "build_opener", lambda *a: SimpleNamespace(
        open=lambda *a, **kw: io.BytesIO(payload),
    ))
    expected = b"installer"
    update = updates.Update("0.3.0", "", "Setup.exe", "https://github.com/installer",
                            hashlib.sha256(expected).hexdigest(), len(expected))
    if payload == expected:
        assert updates.download_update(update).read_bytes() == expected
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
