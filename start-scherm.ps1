# Start het 3,5"-schermpje (Turing Smart Screen, revisie A) met de instellingen uit config.yaml.
# Eerst: het programma van de fabrikant afsluiten (ook in het systeemvak), anders is de COM-poort bezet.
# Stoppen: rechtsklik op het pictogram in het systeemvak > Quit, of Ctrl+C in dit venster.
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = Join-Path $here '.venv\Scripts\python.exe'
if (-not (Test-Path $python)) {
    Write-Host 'Nog niet geinstalleerd. Zie LOKAAL.md (stap 1).'
    exit 1
}
& $python (Join-Path $here 'main.py')
