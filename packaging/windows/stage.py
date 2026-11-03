"""Stage documented release files and dependency license texts, not the checkout."""
import argparse
from datetime import datetime, timezone
import importlib.metadata as metadata
import json
from pathlib import Path
import platform
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--version', required=True)
    args = parser.parse_args()
    bundle = ROOT/'dist/SignalExplorer'
    if not (bundle/'SignalExplorer.exe').is_file():
        raise FileNotFoundError('Build the application before staging release files.')
    for name in ('Install Guide.pdf', 'Users Guide.pdf', "Scripting User's Guide.pdf", 'README.md', 'LICENSE'):
        shutil.copy2(ROOT/name, bundle/name)
    shutil.copy2(ROOT/'packaging/windows/Driver Setup.txt', bundle/'Driver Setup.txt')
    shutil.copytree(ROOT/'examples', bundle/'examples', dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    notices = bundle/'THIRD_PARTY_LICENSES'
    notices.mkdir(exist_ok=True)
    shutil.copytree(ROOT/'packaging/windows/licenses', notices/'license-texts', dirs_exist_ok=True)
    python_license = Path(sys.base_prefix)/'LICENSE.txt'
    if python_license.is_file():
        shutil.copy2(python_license, notices/'Python-LICENSE.txt')
    packages = []
    for distribution in sorted(metadata.distributions(), key=lambda d: d.metadata['Name'].lower()):
        name = distribution.metadata['Name']
        packages.append(dict(name=name, version=distribution.version,
                             license=distribution.metadata.get('License-Expression') or distribution.metadata.get('License'),
                             home=distribution.metadata.get('Home-page'),
                             project_urls=distribution.metadata.get_all('Project-URL') or []))
        for relative in distribution.files or []:
            path = Path(relative)
            if '..' in path.parts or not any(p.lower().startswith(('license', 'copying', 'notice')) for p in path.parts):
                continue
            source = Path(distribution.locate_file(relative))
            if source.is_file():
                destination = notices/name/path
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)
    result = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=ROOT, capture_output=True, text=True, check=True)
    status = subprocess.run(['git', 'status', '--porcelain'], cwd=ROOT, capture_output=True, text=True, check=True)
    info = dict(version=args.version, application_version=metadata.version('signal-explorer'),
                commit=result.stdout.strip(), built_utc=datetime.now(timezone.utc).isoformat(),
                dirty=bool(status.stdout.strip()),
                python=platform.python_version(), platform=platform.platform(),
                dependencies=packages, dependency_scope='Complete build environment, may include unused build tools')
    (bundle/'build-info.json').write_text(json.dumps(info, indent=2), encoding='utf-8')
    (bundle/'THIRD_PARTY_NOTICES.txt').write_text(
        'Signal Explorer uses third-party components under their respective licenses.\n'
        'See THIRD_PARTY_LICENSES for available license/copyright texts and build-info.json\n'
        'for versions in the build environment. Python and Qt shared libraries remain\n'
        'separate files in _internal. Dependency source projects are identified in their\n'
        'distribution metadata; source distribution: https://github.com/jabe421/signal-explorer\n'
        'Vendor hardware drivers and Npcap are installed separately and are not included.\n', encoding='utf-8')
    print('Staged manuals, examples, dependency notices and build provenance.')


if __name__ == '__main__':
    main()
