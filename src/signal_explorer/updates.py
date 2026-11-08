"""Discover and verify Windows installers published in GitHub Releases."""

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import shutil
import tempfile
from urllib.parse import quote, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener, urlopen


REPOSITORY = "jabe421/signal-explorer"
RELEASES_URL = f"https://api.github.com/repos/{REPOSITORY}/releases"
MAX_INSTALLER_BYTES = 512 * 1024 * 1024


@dataclass(frozen=True)
class Update:
    version: str
    notes: str
    name: str
    url: str
    digest: str
    size: int


class ReleaseAssetRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target = urlsplit(newurl)
        if target.scheme != "https" or target.hostname not in {
            "github.com", "release-assets.githubusercontent.com", "objects.githubusercontent.com",
        }:
            raise ValueError("GitHub redirected the installer to an unexpected download host.")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def version_key(version):
    match = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)(?:-([A-Za-z0-9]+(?:\.[A-Za-z0-9]+)*))?", version)
    if match is None:
        raise ValueError("Expected a release version such as 0.3.0-beta.2.")
    suffix = match[4]
    identifiers = tuple((0, int(part)) if part.isdigit() else (1, part)
                        for part in suffix.split(".")) if suffix else ()
    return tuple(map(int, match.groups()[:3])), suffix is None, identifiers


def find_update(installed, include_beta):
    current = version_key(installed)
    best = None
    best_key = current
    request = Request(f"{RELEASES_URL}?per_page=100", headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": "SignalExplorer-UpdateCheck",
    })
    with urlopen(request, timeout=15) as response:
        releases = json.load(response)
    if not isinstance(releases, list):
        raise ValueError("GitHub returned an unexpected release list.")
    for release in releases:
        tag = release.get("tag_name", "")
        try:
            key = version_key(tag)
        except ValueError:
            continue
        if release.get("draft") or key <= best_key:
            continue
        if not include_beta and (release.get("prerelease") or not key[1]):
            continue
        version = tag.removeprefix("v")
        name = f"SignalExplorer-Setup-{version}-windows-x64.exe"
        url = f"https://github.com/{REPOSITORY}/releases/download/{quote(tag)}/{name}"
        for asset in release.get("assets", []):
            digest = asset.get("digest") or ""
            size = asset.get("size", 0)
            if (asset.get("name") == name and asset.get("browser_download_url") == url
                    and re.fullmatch(r"sha256:[a-f0-9]{64}", digest)
                    and 0 < size <= MAX_INSTALLER_BYTES):
                best = Update(version, release.get("body") or "", name, url, digest[7:], size)
                best_key = key
                break
    return best


def download_update(update):
    directory = Path(tempfile.mkdtemp(prefix="signal-explorer-update-"))
    path = directory / update.name
    digest = hashlib.sha256()
    received = 0
    try:
        request = Request(update.url, headers={"User-Agent": "SignalExplorer-Updater"})
        opener = build_opener(ReleaseAssetRedirect())
        with opener.open(request, timeout=15) as response, path.open("xb") as file:
            while block := response.read(256 * 1024):
                received += len(block)
                if received > update.size:
                    raise ValueError("The installer exceeds the published download size.")
                digest.update(block)
                file.write(block)
        if received != update.size or digest.hexdigest() != update.digest:
            raise ValueError("The installer failed SHA-256 verification.")
        return path
    except Exception:
        discard_installer(path)
        raise


def discard_installer(path):
    shutil.rmtree(path.parent, ignore_errors=True)


def launch_installer(path):
    import ctypes
    from ctypes import wintypes

    execute = ctypes.windll.shell32.ShellExecuteW
    execute.argtypes = [wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR,
                        wintypes.LPCWSTR, wintypes.LPCWSTR, ctypes.c_int]
    execute.restype = wintypes.HINSTANCE
    result = execute(None, "runas", str(path), None, str(path.parent), 1)
    if (result or 0) <= 32:
        raise OSError(f"Windows did not start setup (code {result or 0}).")
