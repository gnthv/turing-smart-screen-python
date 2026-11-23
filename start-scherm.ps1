# Start het SmartMonitor 3,5"-schermpje (USB 0483:0065, "llhmi.com") met smartmonitor.py en smartmonitor.yaml.
# De eerste keer (of na een wijziging van het thema) wordt het thema opgeladen: ca. 15 s, het schermpje herstart.
# Stoppen: Ctrl+C of dit venster sluiten. Zie LOKAAL.md.
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = Join-Path $here '.venv\Scripts\python.exe'
if (-not (Test-Path $python)) {
    Write-Host 'Nog niet geinstalleerd. Zie LOKAAL.md (stap 1).'
    exit 1
}
& $python (Join-Path $here 'smartmonitor.py') @args
exit $LASTEXITCODE
