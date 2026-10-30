# PyInstaller spec for the portable Windows build: a one-folder bundle (dist/LayerForge/)
# with a windowed LayerForge.exe.  Build with scripts/build_windows.ps1.
#
# One folder, not one file: the CAD stack (OpenCascade/OCP, VTK) is several hundred MB and
# a one-file exe would unpack it to a temporary folder on every start.

from pathlib import Path

ROOT = Path(SPECPATH).resolve().parent

datas = [
    (str(ROOT / "src" / "layerforge_l1" / "app" / "static"), "layerforge_l1/app/static"),
    (str(ROOT / "fixtures" / "print_processes"), "fixtures/print_processes"),
    (str(ROOT / "packaging" / "README_FIRST.txt"), "."),
]

a = Analysis(
    [str(ROOT / "packaging" / "layerforge_launcher.py")],
    pathex=[str(ROOT / "src")],
    datas=datas,
    # cadquery and trimesh are imported lazily inside the export functions; name them so the
    # analysis follows them even if the lazy import moves.
    hiddenimports=["cadquery", "trimesh", "layerforge_l1.app.launcher"],
    # Not used by the app: the CP-SAT planner is an optional, CLI-only experiment
    # (global-planner extra); test and notebook tooling.
    excludes=["ortools", "pytest", "_pytest", "IPython", "jupyter_client", "notebook", "trame", "trame_vtk"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="LayerForge", console=False,
          upx=False, version=None)
coll = COLLECT(exe, a.binaries, a.datas, name="LayerForge", upx=False)
