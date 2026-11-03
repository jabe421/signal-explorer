"""Writable defaults for installed applications; package assets stay read-only."""
from pathlib import Path
import sys

from PySide6.QtCore import QStandardPaths


def save_path(name: str, current=None) -> str:
    """Keep ordinary source behavior; installed defaults go to Documents."""
    if not getattr(sys, 'frozen', False):
        return str(current or name)
    if current is not None:
        candidate = Path(current).resolve()
        install_root = Path(sys.executable).resolve().parent
        if not candidate.is_relative_to(install_root):
            return str(candidate)
        name = candidate.name
    documents = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DocumentsLocation)
    directory = Path(documents or Path.home()) / 'Signal Explorer'
    directory.mkdir(parents=True, exist_ok=True)
    return str(directory / name)
