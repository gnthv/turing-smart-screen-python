# Arcoloo ops op het 3,5"-schermpje vanzelf laten starten na het aanmelden op Windows: een snelkoppeling in de
# opstartmap die "smartmonitor.py ops" start zonder venster (pythonw). Het zet ook de knoppen Starten / Opnieuw /
# Stoppen in ops (Monitor -> Minischerm -> Op deze pc) aan: het linktype arcoloo-minischerm: voor deze Windows-
# gebruiker (HKCU, geen beheerder nodig), dat "smartmonitor.py link" start. Zie LOKAAL.md, "Arcoloo ops".
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File ops-autostart.ps1              aanzetten (en nu starten)
#   powershell -NoProfile -ExecutionPolicy Bypass -File ops-autostart.ps1 -Knop        enkel de knoppen in ops
#   powershell -NoProfile -ExecutionPolicy Bypass -File ops-autostart.ps1 -Controleer  kijken of het loopt
#   powershell -NoProfile -ExecutionPolicy Bypass -File ops-autostart.ps1 -Uit         niet meer vanzelf (en stoppen)
#   powershell -NoProfile -ExecutionPolicy Bypass -File ops-autostart.ps1 -Verwijder   ook de knoppen in ops weg
param([switch]$Uit, [switch]$Controleer, [switch]$Knop, [switch]$Verwijder)

$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$pythonw = Join-Path $here '.venv\Scripts\pythonw.exe'
$program = Join-Path $here 'smartmonitor.py'
$settings = Join-Path $here 'smartmonitor-ops.json'
$log = Join-Path $here 'smartmonitor-ops.log'
$link = Join-Path ([Environment]::GetFolderPath('Startup')) 'Arcoloo minischerm.lnk'
$scheme = 'HKCU:\Software\Classes\arcoloo-minischerm'
$handler = '"' + $pythonw + '" "' + $program + '" link "%1"'

# Only "smartmonitor.py ops" of THIS folder, with ops right after the script (how the shortcut, start-scherm.cmd ops
# and the buttons start it): not another folder, not "run --theme ops".
function Get-OpsProcess {
    Get-CimInstance Win32_Process -Filter "Name like 'python%'" | Where-Object {
        $c = $_.CommandLine
        if (-not $c) { return $false }
        $i = $c.IndexOf($program, [StringComparison]::OrdinalIgnoreCase)
        ($i -ge 0) -and ($c.Substring($i + $program.Length) -match '^"?\s+ops(\s|$)')
    }
}

function Test-Settings {
    if (-not (Test-Path -LiteralPath $settings)) { return $false }
    try { $s = Get-Content -LiteralPath $settings -Raw | ConvertFrom-Json } catch { return $false }
    return [bool]($s -and $s.url -and $s.key)
}

function Get-LinkCommand {
    $key = Join-Path $scheme 'shell\open\command'
    if (Test-Path $key) { (Get-Item $key).GetValue('') } else { $null }
}

function Set-OpsLink {
    $command = Join-Path $scheme 'shell\open\command'
    New-Item -Path $command -Force | Out-Null
    Set-Item -Path $scheme -Value 'URL:Arcoloo minischerm'
    New-ItemProperty -Path $scheme -Name 'URL Protocol' -Value '' -PropertyType String -Force | Out-Null
    New-ItemProperty -Path (Join-Path $scheme 'shell\open') -Name 'FriendlyAppName' -Value 'Arcoloo minischerm' `
        -PropertyType String -Force | Out-Null
    Set-Item -Path $command -Value $handler
    'Knoppen in ops (Op deze pc): AAN. Chrome vraagt de eerste keer "Arcoloo minischerm openen?": vink altijd toestaan aan.'
}

function Stop-Ops {
    $running = @(Get-OpsProcess)
    foreach ($p in $running) { Stop-Process -Id $p.ProcessId -ErrorAction SilentlyContinue }
    if ($running.Count) { 'Het programma is gestopt (de klok op het schermpje blijft nu stilstaan).' }
}

if ($Controleer) {
    if (Test-Path -LiteralPath $link) { 'Vanzelf starten: AAN' } else { 'Vanzelf starten: uit' }
    $current = Get-LinkCommand
    if (-not $current) { 'Knoppen in ops: uit' }
    elseif ($current -eq $handler) { 'Knoppen in ops: AAN' }
    else { "Knoppen in ops: AAN, maar voor een andere map: $current" }
    $running = @(Get-OpsProcess)
    if ($running.Count) { "Loopt nu: ja (proces $($running.ProcessId -join ', '))" } else { 'Loopt nu: nee' }
    if (Test-Path -LiteralPath $log) { ''; 'Laatste regels van het logboek:'; Get-Content -LiteralPath $log -Tail 8 -Encoding UTF8 }
    exit 0
}

if ($Uit -or $Verwijder) {
    if (Test-Path -LiteralPath $link) { Remove-Item -LiteralPath $link; 'Vanzelf starten: uit.' } else { 'Vanzelf starten stond al uit.' }
    if ($Verwijder) {
        if (Test-Path $scheme) { Remove-Item -Path $scheme -Recurse; 'Knoppen in ops: uit.' }
    } elseif (Get-LinkCommand) {
        'De knoppen in ops werken nog (Starten start het weer). Ook die uit: -Verwijder'
    }
    Stop-Ops
    exit 0
}

if (-not (Test-Path -LiteralPath $pythonw)) { 'Nog niet geinstalleerd: zie LOKAAL.md, stap 1.'; exit 1 }

Set-OpsLink
if ($Knop) { exit 0 }

if (-not (Test-Settings)) {
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
    # -WorkingDirectory reads [ ] as wildcards: escape them (a folder like "scherm [2]")
    Start-Process -FilePath $pythonw -ArgumentList ('"' + $program + '" ops') `
        -WorkingDirectory ([Management.Automation.WildcardPattern]::Escape($here))
    'Gestart zonder venster. De eerste keer zet het zijn thema op het schermpje (ca. 20 s, het herstart twee keer).'
}
"Controleren: powershell -NoProfile -ExecutionPolicy Bypass -File `"$($MyInvocation.MyCommand.Path)`" -Controleer"
