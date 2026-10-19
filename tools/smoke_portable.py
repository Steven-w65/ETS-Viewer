"""Launch the extracted Windows build without a system Python on PATH."""

import argparse
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import zipfile


def main_window(process_id):
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows.argtypes = (callback_type, wintypes.LPARAM)
    user32.EnumChildWindows.argtypes = (wintypes.HWND, callback_type, wintypes.LPARAM)
    user32.GetWindowThreadProcessId.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
    user32.GetWindowTextW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
    found = []
    errors = []

    @callback_type
    def error_text(handle, _):
        text = ctypes.create_unicode_buffer(16000)
        user32.GetWindowTextW(handle, text, len(text))
        if text.value:
            errors.append(text.value)
        return True

    @callback_type
    def inspect(handle, _):
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(handle, ctypes.byref(pid))
        if pid.value == process_id:
            title = ctypes.create_unicode_buffer(256)
            user32.GetWindowTextW(handle, title, len(title))
            if title.value == "ETS Viewer":
                found.append(handle)
                return False
            if title.value == "Unhandled exception in script":
                user32.EnumChildWindows(handle, error_text, 0)
        return True

    user32.EnumWindows(inspect, 0)
    if errors:
        raise RuntimeError("Portable startup failed:\n" + "\n".join(errors))
    return found[0] if found else None


def smoke(archive):
    from PySide6.QtGui import QImage

    root = Path(__file__).resolve().parents[1]
    work = root / "build"
    work.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="Portable smoke 测试 ", dir=work) as folder:
        temporary = Path(folder).resolve()
        with zipfile.ZipFile(archive) as package:
            for member in package.infolist():
                if not (temporary / member.filename).resolve().is_relative_to(temporary):
                    raise RuntimeError("Archive member leaves the extraction directory.")
            package.extractall(temporary)
        bundle = temporary / "ETS-Viewer"
        exe = bundle / "ETS-Viewer.exe"
        if not exe.is_file() or not (bundle / "_internal" / "ui" / "style.qss").is_file():
            raise RuntimeError("Portable executable or theme template is missing.")
        for plugin in ("qwindows.dll", "qjpeg.dll"):
            if not any(bundle.rglob(plugin)):
                raise RuntimeError(f"Required Qt plugin is missing: {plugin}")
        bank = temporary / "Sample bank 题库"
        question = bank / "paper" / "content_smoke"
        material = question / "material"
        material.mkdir(parents=True)
        parts = []
        for index, extension in enumerate(("png", "jpg"), 1):
            name = f"图片 with spaces.{extension}"
            image = QImage(160, 80, QImage.Format.Format_RGB32)
            image.fill(0xffaabbcc)
            if not image.save(str(material / name)):
                raise RuntimeError(f"Could not create synthetic {extension} fixture.")
            parts.append({"xh": str(index), "ask": f"Original prompt {index}",
                          "askimg": name, "answer": "Original stored answer"})
        (question / "content.json").write_text(json.dumps({
            "structure_type": "collector.dialogue", "info": {"stid": "smoke", "question": parts}},
            ensure_ascii=False), encoding="utf-8")
        originals = {path: path.read_bytes() for path in question.rglob("*") if path.is_file()}
        environment = os.environ.copy()
        for name in ("PYTHONHOME", "PYTHONPATH", "QT_PLUGIN_PATH", "QT_QPA_PLATFORM_PLUGIN_PATH"):
            environment.pop(name, None)
        windows = Path(environment["SYSTEMROOT"])
        environment["PATH"] = os.pathsep.join((str(windows / "System32"), str(windows)))
        environment["QT_QPA_PLATFORM"] = "windows"
        startup = subprocess.STARTUPINFO()
        startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startup.wShowWindow = 0  # Keep the automated test window hidden.
        process = subprocess.Popen([str(exe), str(bank)], cwd=temporary, env=environment,
                                   startupinfo=startup)
        print(f"Launched portable process {process.pid}; waiting for its native window.", flush=True)
        try:
            deadline = time.monotonic() + 45
            handle = None
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError(f"Portable app exited during startup: {process.returncode}")
                handle = main_window(process.pid)
                if handle:
                    break
                time.sleep(0.1)
            if not handle:
                raise RuntimeError("Portable app did not create its main window.")
            time.sleep(2)  # Allow the small synthetic bank to finish loading.
            user32 = ctypes.WinDLL("user32", use_last_error=True)
            user32.SendMessageTimeoutW.argtypes = (wintypes.HWND, wintypes.UINT, wintypes.WPARAM,
                wintypes.LPARAM, wintypes.UINT, wintypes.UINT, ctypes.POINTER(ctypes.c_size_t))
            result = ctypes.c_size_t()
            if not user32.SendMessageTimeoutW(handle, 0, 0, 0, 2, 5000, ctypes.byref(result)):
                raise RuntimeError("Portable app's window is unresponsive.")
            user32.PostMessageW.argtypes = (wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
            user32.PostMessageW(handle, 0x0010, 0, 0)  # WM_CLOSE requests a clean worker shutdown.
            if process.wait(timeout=20) != 0:
                raise RuntimeError(f"Portable app exited with code {process.returncode}")
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
        if originals != {path: path.read_bytes() for path in originals}:
            raise RuntimeError("Portable app modified the synthetic question bank.")
    print("Portable smoke test passed: Unicode/space paths, bundled Qt/QSS, isolated PATH, clean shutdown.")


if __name__ == "__main__":
    if sys.platform != "win32":
        raise SystemExit("The portable smoke test requires Windows.")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    smoke(parser.parse_args().archive.resolve())
