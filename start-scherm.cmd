@echo off
rem Dubbelklikken om het SmartMonitor 3,5"-schermpje te starten (smartmonitor.py, zie LOKAAL.md).
rem Ook met een opdracht: start-scherm.cmd preview / upload / metrics / devices
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0start-scherm.ps1" %*
if errorlevel 1 pause
