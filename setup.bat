@echo off
rem Install web-reader here (venv, browser, Claude registration, self test).
rem Add -Headless on a PC without a desktop.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1" %*
pause
