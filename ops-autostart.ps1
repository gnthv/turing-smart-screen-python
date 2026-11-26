# Arcoloo ops op het 3,5"-schermpje vanzelf laten starten na het aanmelden op Windows: een snelkoppeling in de
# opstartmap die "smartmonitor.py ops" start zonder venster (pythonw). Zie LOKAAL.md, "Arcoloo ops".
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File ops-autostart.ps1              aanzetten (en nu starten)
#   powershell -NoProfile -ExecutionPolicy Bypass -File ops-autostart.ps1 -Controleer  kijken of het loopt
#   powershell -NoProfile -ExecutionPolicy Bypass -File ops-autostart.ps1 -Uit         uitzetten (en stoppen)
param([switch]$Uit, [switch]$Controleer)

$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$pythonw = Join-Path $here '.venv\Scripts\pythonw.exe'
$program = Join-Path $here 'smartmonitor.py'
$settings = Join-Path $here 'smartmonitor-ops.json'
$log = Join-Path $here 'smartmonitor-ops.log'
$link = Join-Path ([Environment]::GetFolderPath('Startup')) 'Arcoloo minischerm.lnk'

function Get-OpsProcess {
    Get-CimInstance Win32_Process -Filter "Name like 'python%'" |
        Where-Object { $_.CommandLine -like '*smartmonitor.py*' -and $_.CommandLine -match '\sops(\s|$)' }
}

if ($Controleer) {
    if (Test-Path $link) { 'Vanzelf starten: AAN' } else { 'Vanzelf starten: uit' }
    $running = @(Get-OpsProcess)
    if ($running.Count) { "Loopt nu: ja (proces $($running.ProcessId -join ', '))" } else { 'Loopt nu: nee' }
    if (Test-Path $log) { ''; 'Laatste regels van het logboek:'; Get-Content $log -Tail 8 -Encoding UTF8 }
    exit 0
}

if ($Uit) {
    if (Test-Path $link) { Remove-Item $link; 'Vanzelf starten: uit.' } else { 'Vanzelf starten stond al uit.' }
    $running = @(Get-OpsProcess)
    foreach ($p in $running) { Stop-Process -Id $p.ProcessId -ErrorAction SilentlyContinue }
    if ($running.Count) { 'Het programma is gestopt (de klok op het schermpje blijft nu stilstaan).' }
    exit 0
}

if (-not (Test-Path $pythonw)) { 'Nog niet geinstalleerd: zie LOKAAL.md, stap 1.'; exit 1 }
if (-not (Test-Path $settings)) {
    'Eerst instellen (eenmalig), in deze map:'
    '  start-scherm.cmd ops --url https://arcoloomonitoring.up.railway.app --key'
    'en plak de sleutel uit ops (Monitor -> Minischerm -> Sleutel voor een pc-programma).'
    exit 1
}

$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($link)
$shortcut.TargetPath = $pythonw
$shortcut.Arguments = '"' + $program + '" ops'
$shortcut.WorkingDirectory = $here
$shortcut.Description = 'Arcoloo ops op het 3,5-inch schermpje (smartmonitor.py ops)'
$shortcut.Save()
'Vanzelf starten: AAN (na het aanmelden op Windows).'

if (@(Get-OpsProcess).Count) {
    'Het programma loopt al.'
} else {
    Start-Process -FilePath $pythonw -ArgumentList ('"' + $program + '" ops') -WorkingDirectory $here
    'Gestart zonder venster. De eerste keer zet het zijn thema op het schermpje (ca. 20 s, het herstart twee keer).'
}
"Controleren: powershell -NoProfile -ExecutionPolicy Bypass -File `"$($MyInvocation.MyCommand.Path)`" -Controleer"
