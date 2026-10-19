ETS-Viewer - portable Windows x64 build

Extract the complete ZIP, then open ETS-Viewer.exe.
Keep the _internal folder beside the executable; it contains Python, Qt,
the theme template, and the required runtime files. No Python installation
or administrator access is required.

The viewer opens your existing %APPDATA%\ETS bank when available.
Use Select Question Bank / Change Bank to choose another folder.
You can also launch: ETS-Viewer.exe "D:\path\to\ETS"
Question-bank files are not included and are never modified by the viewer.
Appearance preferences are stored in the current Windows user's settings.

Source, build instructions and releases:
https://github.com/Steven-w65/ETS-Viewer

This build includes PySide6/Qt and shiboken6. Their distribution metadata
and supplied license files are included under _internal/*.dist-info.
PyInstaller's bootloader uses its GPL license with the bootloader exception:
https://pyinstaller.org/en/stable/license.html

Verify the downloaded ZIP using the accompanying SHA-256 checksum file.
