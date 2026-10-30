# Builds the portable Windows app: dist\LayerForge\LayerForge.exe (+ _internal\) and
# dist\LayerForge-<version>-win64.zip.  Needs the project venv with the build extra:
#   .\.venv\Scripts\python.exe -m pip install -e ".[build]"
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"
Push-Location $root
try {
    & $python -m PyInstaller --noconfirm --clean --distpath dist --workpath build\pyinstaller packaging\LayerForge.spec
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed ($LASTEXITCODE)" }
    $version = & $python -c "import sys; sys.path.insert(0, 'src'); import layerforge_l1; print(layerforge_l1.__version__)"
    $zip = "dist\LayerForge-$version-win64.zip"
    & $python -c "import pathlib, sys, zipfile; src = pathlib.Path('dist/LayerForge'); z = zipfile.ZipFile(sys.argv[1], 'w', zipfile.ZIP_DEFLATED); [z.write(f, pathlib.Path('LayerForge') / f.relative_to(src)) for f in sorted(src.rglob('*')) if f.is_file()]; z.close()" $zip
    if ($LASTEXITCODE -ne 0) { throw "zip failed ($LASTEXITCODE)" }
    Write-Host "Built dist\LayerForge\LayerForge.exe and $zip"
} finally {
    Pop-Location
}
