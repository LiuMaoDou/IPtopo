# Run on Windows: python -m PyInstaller --clean --noconfirm packaging/windows.spec
from pathlib import Path
from PyInstaller.utils.hooks import collect_all, copy_metadata

root = Path(SPECPATH).parent
datas = [
    (str(root / "frontend/dist"), "frontend/dist"),
    (str(root / "samples/demo"), "samples/demo"),
    (str(root / "LICENSE"), "."),
]
binaries = []
hiddenimports = ["uvicorn.logging", "uvicorn.loops.asyncio", "uvicorn.protocols.http.h11_impl", "uvicorn.lifespan.on", "sqlalchemy.dialects.sqlite"]
# TTP dynamically loads Python plugins from its package directories.
for package in ("ttp", "ttp_templates", "netutils", "N2G"):
    package_data, package_binaries, package_imports = collect_all(package)
    datas += package_data
    binaries += package_binaries
    hiddenimports += package_imports
for package in ("ttp", "ttp-templates", "netutils", "N2G", "fastapi"):
    datas += copy_metadata(package)

a = Analysis([str(root / "desktop.py")], pathex=[str(root)], binaries=binaries,
             datas=datas, hiddenimports=hiddenimports,
             excludes=["pytest", "IPython", "matplotlib", "numpy", "pandas"])
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], name="IPtopo", console=False,
          debug=False, strip=False, upx=False)
