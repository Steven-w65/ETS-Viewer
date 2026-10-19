"""Build a self-contained Windows x64 folder, ZIP, and SHA-256 checksum."""

import hashlib
from importlib.metadata import version
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys


def main():
    if sys.platform != "win32" or struct.calcsize("P") != 8:
        raise SystemExit("Build with 64-bit Python on Windows.")
    root = Path(__file__).resolve().parents[1]
    output = root / "dist"
    work = root / "build"
    # PyInstaller owns these generated directories; keep its cache inside
    # the checkout too, without depending on a writable roaming profile.
    for directory in (output, work):
        if not directory.resolve().is_relative_to(root):
            raise SystemExit(f"Build directory leaves the checkout: {directory}")
        directory.mkdir(exist_ok=True)
    environment = dict(os.environ, PYINSTALLER_CONFIG_DIR=str(work / "pyinstaller-cache"))
    windows = Path(environment["SYSTEMROOT"])
    # DLL discovery must not collect incompatible ICU/OpenSSL/runtime DLLs
    # from unrelated tools on the developer's PATH.
    environment["PATH"] = os.pathsep.join((str(Path(sys.executable).parent), sys.base_prefix,
                                           str(windows / "System32"), str(windows)))
    subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
                    "--distpath", str(output), "--workpath", str(work / "pyinstaller"),
                    str(root / "packaging" / "windows.spec")],
                   cwd=root, env=environment, check=True)
    bundle = output / "ETS-Viewer"
    if not (bundle / "ETS-Viewer.exe").is_file():
        raise SystemExit("PyInstaller did not produce ETS-Viewer.exe.")
    try:
        commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root,
                                         text=True, stderr=subprocess.DEVNULL).strip()
        if subprocess.check_output(["git", "status", "--porcelain"], cwd=root, text=True):
            commit += " (working tree changes)"
    except (OSError, subprocess.CalledProcessError):
        commit = "Source archive (Git revision unavailable)"
    (bundle / "BUILD-INFO.txt").write_text(
        f"Commit: {commit}\nPython: {sys.version}\n"
        f"PySide6: {version('PySide6')}\nPyInstaller: {version('PyInstaller')}\n",
        encoding="utf-8")
    # Only the explicit bundle is archived, never the checkout or bank files.
    archive = Path(shutil.make_archive(str(output / "ETS-Viewer-windows-x64"), "zip",
                                       root_dir=output, base_dir=bundle.name))
    with archive.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    checksum = archive.with_suffix(".zip.sha256")
    checksum.write_text(f"{digest}  {archive.name}\n", encoding="ascii")
    print(f"Portable ZIP: {archive}")
    print(f"SHA-256: {checksum}")


if __name__ == "__main__":
    main()
