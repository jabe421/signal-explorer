# Local workspace notes

On October 29, 2026, the project moved to:

`C:\Users\Jabe\Documents\Signal Explorer\signal-explorer`

This nested folder is the Git repository and application root. The parent
`Signal Explorer` folder is only its container. Run project commands from the
repository root. `SignalExplorer.bat` resolves its own folder so future moves do
not require editing a hard-coded launcher path.

The local `.venv` contains installed Python dependencies. Virtual environments,
temporary captures, caches, generated package metadata and MF4 recordings are
excluded from Git. Keep source, tests, example scripts, documentation, application
assets and the three PDF guides in the repository.

On a different PC, create a fresh `.venv` and install from `pyproject.toml`; do
not transfer the development PC's environment. The local environment was repaired
after this move to point its editable package and launchers at the new root.
