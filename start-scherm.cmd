@echo off
rem Dubbelklikken om het 3,5"-schermpje te starten (zie LOKAAL.md).
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0start-scherm.ps1"
if errorlevel 1 pause
