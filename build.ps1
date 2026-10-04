$ErrorActionPreference = "Stop"
$projectRoot = $PSScriptRoot
$releaseDir = Join-Path $projectRoot "release"
$workDir = Join-Path $projectRoot "build"

Push-Location $projectRoot
try {
    New-Item -ItemType Directory -Force -Path $releaseDir | Out-Null
    python -m PyInstaller --clean --noconfirm --distpath $releaseDir --workpath $workDir "NEXDROP.spec"
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller failed with exit code $LASTEXITCODE"
    }

    $portableExe = Join-Path $releaseDir "NEXDROP-Portable.exe"
    if (-not (Test-Path $portableExe)) {
        throw "Portable executable was not produced: $portableExe"
    }

    $compiler = Get-Command "ISCC.exe" -ErrorAction SilentlyContinue
    if (-not $compiler) {
        $candidates = @(
            "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
            "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
            "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe"
        )
        $compilerPath = $candidates | Where-Object { Test-Path $_ } | Select-Object -First 1
    } else {
        $compilerPath = $compiler.Source
    }

    if (-not $compilerPath) {
        throw "Portable build succeeded. Install Inno Setup 6, then rerun this script to create NEXDROP-Setup.exe."
    }

    & $compilerPath (Join-Path $projectRoot "installer.iss")
    if ($LASTEXITCODE -ne 0) {
        throw "Inno Setup failed with exit code $LASTEXITCODE"
    }

    Write-Host "Created $portableExe and $(Join-Path $releaseDir 'NEXDROP-Setup.exe')"
} finally {
    Pop-Location
}
