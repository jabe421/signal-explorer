param(
    [string]$Version = '0.1.0-beta.1',
    [string]$Python = 'python',
    [switch]$SkipInstaller
)
$ErrorActionPreference = 'Stop'
if ($Version -notmatch '^\d+\.\d+\.\d+(?:-[A-Za-z0-9]+(?:\.[A-Za-z0-9]+)*)?$') {
    throw 'Version must look like 0.1.0 or 0.1.0-beta.1.'
}
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
Push-Location -LiteralPath $projectRoot
try {
    & $Python -m pip check
    if ($LASTEXITCODE -ne 0) { throw 'Dependency verification failed.' }
    & $Python -m PyInstaller --noconfirm --clean --distpath dist --workpath build/pyinstaller packaging/windows/signal-explorer.spec
    if ($LASTEXITCODE -ne 0) { throw 'PyInstaller build failed.' }
    & $Python packaging/windows/stage.py --version $Version
    if ($LASTEXITCODE -ne 0) { throw 'Release staging failed.' }
    $bundleRoot = Join-Path $projectRoot 'dist/SignalExplorer'
    $smokeReport = Join-Path $projectRoot 'build/packaging-smoke.json'
    # Run from a non-project directory: no accidental imports from the checkout.
    Push-Location -LiteralPath $env:TEMP
    try {
        & (Join-Path $bundleRoot 'SignalExplorer-ScriptWorker.exe') --packaging-smoke-test $smokeReport
        if ($LASTEXITCODE -ne 0) { throw "Packaged simulation test failed. See $smokeReport" }
    } finally { Pop-Location }
    if (-not (Test-Path -LiteralPath $smokeReport)) { throw 'Packaged smoke test did not write its report.' }
    $smokeResult = Get-Content -LiteralPath $smokeReport -Raw | ConvertFrom-Json
    if (-not $smokeResult.ok -or -not $smokeResult.frozen) { throw 'Frozen application verification did not pass.' }
    if (-not $SkipInstaller) {
        $compiler = Get-Command ISCC.exe -ErrorAction SilentlyContinue
        $compilerPath = if ($compiler) { $compiler.Source } else { Join-Path ${env:ProgramFiles(x86)} 'Inno Setup 6/ISCC.exe' }
        if (-not (Test-Path -LiteralPath $compilerPath)) { throw 'Install Inno Setup 6 or put ISCC.exe on PATH.' }
        & $compilerPath "/DBuildVersion=$Version" "/DSourceRoot=$projectRoot" packaging/windows/installer.iss
        if ($LASTEXITCODE -ne 0) { throw 'Installer compilation failed.' }
        $installerPath = Join-Path $projectRoot "dist/installer/SignalExplorer-Setup-$Version-windows-x64.exe"
        $digest = (Get-FileHash -LiteralPath $installerPath -Algorithm SHA256).Hash.ToLowerInvariant()
        "$digest  $([IO.Path]::GetFileName($installerPath))" | Set-Content -LiteralPath (Join-Path $projectRoot 'dist/installer/SHA256SUMS.txt') -Encoding ascii
        Copy-Item -LiteralPath (Join-Path $bundleRoot 'build-info.json') -Destination (Join-Path $projectRoot 'dist/installer/build-info.json')
        Copy-Item -LiteralPath $smokeReport -Destination (Join-Path $projectRoot 'dist/installer/packaging-smoke.json')
        Write-Host "Installer ready: $installerPath"
    }
} finally { Pop-Location }
