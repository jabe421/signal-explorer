# Build a Windows tester installer with GitHub Actions

The workflow **Build Windows installer** runs only when started manually. It
tests and packages Windows x64 using Python 3.12, PyInstaller and Inno Setup.
It builds and verifies a separate x86 helper for the NI-CAN USB-8473 family.
It uploads a downloadable build artifact; it does not publish a GitHub Release,
create a tag, or upload anything on an ordinary source push.

## In-app updates

Starting with `0.3.0-beta.2`, open **Help → Check for Updates** in the installed
Windows app. Select **Include beta releases** to receive prerelease offers.
The dialog shows the newer release's published notes when available.
**Update** downloads the Windows installer, verifies its published SHA-256
digest, then uses the normal app close flow before opening Windows Setup.
The dialog shows download percentage and stays open with a closing status until
Signal Explorer is ready to launch Setup.
The source checkout shows release notes and keeps **Update** disabled.
Choose **Later** to keep working, or cancel a save prompt to stop the update.
Windows asks for administrator approval to run Setup.
On the final page, **Start Signal Explorer** is checked by default. Leave it
checked to open the updated app when you click **Finish**.

For each release, use the same version in the workflow input and GitHub release
tag, such as `0.3.0-beta.2` and `v0.3.0-beta.2`. Attach
`SignalExplorer-Setup-0.3.0-beta.2-windows-x64.exe` directly to the published
GitHub Release. Keep the versioned filename and publish beta builds as prereleases.
Users on beta.1 install beta.2 manually. To validate the updater end to end,
install an updater-enabled build and check against a newer published installer.

## First-time setup: put the workflow on GitHub

These project changes must reach the repository's default branch, currently
`main`, before GitHub displays **Run workflow**. Having them only on your PC is
not enough.

If you use GitHub Desktop:

1. Open the existing `signal-explorer` repository. Confirm the branch is `main`.
2. Review the changes. Include `.github/workflows/windows-build.yml`, the
   `packaging/windows` folder, the application packaging changes, tests, LICENSE
   and documentation. `.venv`, `build` and `dist` remain excluded.
3. Commit with a description such as `Prepare manual Windows installer build`.
4. Click **Push origin**. If your repository uses protected branches, use its
   normal pull-request process to merge these changes into the default branch.

You can ask for help with this synchronization step. No personal access token
or code-signing secret is required for this build-only workflow. It uses the
normal GitHub Actions token with read access to repository contents.

## Start your first build

1. Open <https://github.com/jabe421/signal-explorer/actions> in your browser.
2. If GitHub asks to enable Actions, enable them if permitted by your repository
   or organization. You need repository write access to start a manual workflow.
3. Select **Build Windows installer** in the left sidebar.
4. Click **Run workflow**. Select branch **main**.
5. Leave the installer version as `0.5.0-beta.1`, or enter the intended release
   version. This labels the installer; changing the package's application version
   remains a separate source edit in `pyproject.toml`.
6. Click the green **Run workflow** button. Refresh after a few seconds, then
   select the new run named **Build Windows installer**.
7. Expand **Test and package Windows x64** to see progress. It installs pinned
   dependencies, runs the software tests, freezes the app, checks the frozen
   simulation paths and compiles the installer. The workflow has a 45-minute
   timeout; the first build may take several minutes.
8. A green check means those build checks passed. Scroll to **Artifacts** on the
   run's summary page. Download **SignalExplorer-windows-x64-0.5.0-beta.1**.
   You must be signed into GitHub with access to the repository to download it.
9. Extract the ZIP. It contains the setup EXE, `SHA256SUMS.txt`, `build-info.json`
   and `packaging-smoke.json`. Artifacts are retained for 30 days. Keep a local
   copy if needed; a later GitHub Release asset is separate and longer lived.

If **Run workflow** is absent, confirm the workflow is on the default branch,
Actions is enabled and you have write permission. A red run shows the failing
step; open it and copy the error text. **Windows-build-diagnostics-...** contains
available test results, smoke results and PyInstaller import warnings, including
on failed runs. A failed dependency/test/smoke step prevents a success artifact.

GitHub's guide: <https://docs.github.com/en/actions/how-tos/manage-workflow-runs/manually-run-a-workflow>.

## What the installer does

- Installs the complete app into `C:\Program Files\Signal Explorer` by default;
  the destination can be changed. Administrator approval is requested.
- Creates a Start menu shortcut and an optional desktop shortcut with the SE icon.
- Includes Python, Qt, application libraries, the separate script worker, demo
  DBC/LDF/scripts, three PDF guides, dependency notices and driver instructions.
  The installed `examples` folder includes `demo_board.dbc` and `demo_board.mf4`
  for decoding and replaying demo board traffic.
- Reuses the previous installation directory for upgrades with the same AppId.
  Close the running app and save pending files before upgrading. Use distinct
  beta versions for identifiable builds; do not replace an already shared
  release's installer silently. Downgrade/version-policy enforcement is not added.
- Registers an uninstall entry in Windows Installed apps. User-created data,
  local settings and shared adapter drivers are preserved on uninstall.
- Defaults installed-build Save dialogs to `Documents\Signal Explorer`.
  Installed example scripts/databases are templates: Save/Save As uses a writable
  copy instead of replacing files under the installation directory.

Vendor hardware drivers and Npcap are not bundled or silently updated. Python
bindings are included; the corresponding driver/API must be installed separately
for physical hardware. Simulation does not need those drivers. The same
[Driver Setup.txt](../src/signal_explorer/assets/Driver%20Setup.txt) is shown at
the end of Setup, installed with a Start menu shortcut, and available with
clickable links under **Help → About Signal Explorer**. For the USB-8473/8473s,
install NI-CAN 18.5 separately and confirm the CAN interface in NI MAX.
The USB-8502 uses NI-XNET.

The installer is unsigned until signing is configured; Windows/company policy
may show an unknown publisher or block execution. Use a controlled tester PC and
your organization's approval process. Checksums identify the downloaded file;
they do not replace a trusted signature or hardware validation.

## First installer acceptance test

Use a Windows x64 test PC/VM without Python or the development checkout.
The installer minimum is Windows 10 version 1809 (build 17763), x64. This includes
Windows 10 1909 (build 18363). Beta.1 mistakenly required build 19041; beta.2
corrects that installer gate. Application operation on the older VM still needs
verification. Qt 6.12 lists Windows 10 1809 or later in its
[supported platforms](https://doc.qt.io/qt-6/supported-platforms.html).
32-bit Windows is not supported by this x64 package. Use a supported
Windows version under your organization's policy. ARM64 is not qualified.

1. Run Setup and check its destination, Start menu and optional desktop shortcut.
2. Start Signal Explorer from the shortcut. Verify its icon/splash and default
   Virtual selection. No adapter should connect or transmit automatically.
   Open **Help → About Signal Explorer** and check that the version is correct
   and the NI-CAN and NI-XNET links open their respective vendor pages.
3. Connect Virtual CAN, enable demo traffic, load the installed demo DBC and run
   File Viewer > Open CAN Simulation Example. Try Pause/Resume/Stop.
4. Try raw/DBC multi-message once/cyclic sending, live editing and saving/reopening
   a TX list. Create a log, stop/save it, then open/replay it to Virtual CAN.
5. Check LIN and Ethernet simulation, their test traffic and logging/replay.
6. Save an edited example script/DBC and confirm its copy is under Documents,
   while the installed template stays unchanged.
7. Close the app. Install a newer beta and confirm settings/user files survive.
8. Uninstall and confirm shortcuts/app files disappear while user files remain.
9. Test physical adapters separately after installing their vendor drivers.

Build smoke checks cover UI construction, asset loading, separate-process CAN FD
echo, cyclic/live TX editing, MF4 CAN replay, and CAN/LIN/Ethernet recording
round trips. They do not run the interactive installer, verify a clean PC's
runtime, or qualify physical hardware. Complete the acceptance list before
distributing the build as a release.

## Local build (optional)

Use Windows x64 Python 3.12 and Inno Setup 6. To include USB-8473 support,
also install Python 3.12 x86 on the **build PC** and create the helper first:

```powershell
py -3.12-32 -m venv .venv-build-nican
.\.venv-build-nican\Scripts\python.exe -m pip install -c packaging/windows/constraints.txt pyinstaller pyinstaller-hooks-contrib python-can
.\.venv-build-nican\Scripts\python.exe -m PyInstaller --onedir --name SignalExplorer-NICAN --distpath build/nican --workpath build/nican-work --specpath build/nican-work src/signal_explorer/bus/nican_worker.py
```

Then build the x64 app and pass the helper folder to the packaging script:

```powershell
py -3.12-64 -m venv .venv-build
.\.venv-build\Scripts\python.exe -m pip install -c packaging/windows/constraints.txt -r packaging/windows/requirements-build.txt ".[intrepid,nixnet]" "gs-usb==0.3.1" "pyusb==1.3.1"
.\.venv-build\Scripts\python.exe -m pytest tests -q
.\packaging\windows\build.ps1 -Python .\.venv-build\Scripts\python.exe -Version 0.5.0-beta.1 -NiCanHelper build/nican/SignalExplorer-NICAN
```

`-SkipInstaller` builds and smoke-tests the application folder when Inno Setup
is unavailable. Generated files stay under ignored `build/` and `dist/` folders.
Omit `-NiCanHelper` only for builds that can skip USB-8473/8473s operation.
Share the setup EXE, not one EXE extracted from the application dependency folder.
The packaged script editor can use bundled modules and adjacent helper `.py`
files; it does not provide pip installation of arbitrary third-party modules.

## Later: GitHub Releases

After clean-PC/bench checks, attach the tested setup EXE and checksum to a draft
`v0.5.0-beta.1` release, then publish as a pre-release when ready. Publishing is
deliberately separate from this manual build workflow. A later workflow can add
tag-triggered builds, draft releases, signing and update checks.
