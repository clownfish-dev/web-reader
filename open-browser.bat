@echo off
set PLAYWRIGHT_BROWSERS_PATH=%~dp0browsers
start "" "%~dp0.venv\Scripts\pythonw.exe" -I -m scrapling.webreader.open_window %*
