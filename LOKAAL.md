# Lokaal gebruik — 3,5"-schermpje (Hsthe Sea / Lanzouv = Turing Smart Screen)

Fork van [mathoudebine/turing-smart-screen-python](https://github.com/mathoudebine/turing-smart-screen-python) voor eigen dashboards op het kleine USB-C-schermpje. Los project, niet gekoppeld aan andere apps.

Het schermpje (Amazon ASIN B0CW2LSDD4, model CP032074, 320×480) is een **Turing Smart Screen 3,5"**, protocol **revisie A** (seriële COM-poort). De software van de fabrikant is niet nodig.

## 1. Installeren (één keer)

In PowerShell, in deze map:

```powershell
py -3.12 -m venv .venv
```

```powershell
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Python 3.12 (niet 3.14: nog niet alle pakketten ondersteunen dat).

## 2. Starten

1. Sluit het programma van de fabrikant af (ook in het systeemvak en bij Opstarten).
2. Steek het schermpje in.
3. Dubbelklik **`start-scherm.cmd`**, of in PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -File .\start-scherm.ps1
```

(Windows blokkeert `.ps1`-scripts standaard; `-ExecutionPolicy Bypass` geldt enkel voor deze ene start en verandert geen instellingen.)

Stoppen: rechtsklik op het pictogram in het systeemvak › Quit.

## Instellingen (`config.yaml`)

| Sleutel | Waarde hier | Waarom |
|---|---|---|
| `display.REVISION` | `A` | Turing / UsbPCMonitor 3,5" |
| `config.COM_PORT` | `AUTO` | herkent het scherm zelf; anders bv. `"COM5"` |
| `config.HW_SENSORS` | `PYTHON` | werkt zonder beheerdersrechten. `AUTO`/`LHM` geeft meer temperaturen (CPU), maar dan moet je als administrator starten, anders stopt het programma meteen. |
| `config.THEME` | `3.5inchTheme2` | een ander thema: map uit `res/themes` |
| `display.BRIGHTNESS` | `20` | niet boven 50 % zetten: het scherm wordt heet |

Grafisch instellen kan ook met `.venv\Scripts\python.exe configure.py`.

## Zonder scherm testen (gesimuleerd)

Zet `display.REVISION` op `SIMU`, start `start-scherm.cmd` en open <http://localhost:5678>. Daarna terug op `A`.

## Eigen dashboard maken

- **Thema-editor:** `.venv\Scripts\python.exe theme-editor.py <themanaam>` toont het thema live terwijl je `res/themes/<naam>/theme.yaml` aanpast. Kopieer een bestaand 3,5"-thema als vertrekpunt.
- **Eigen gegevens** (bv. iets van een website of API): zie `library/sensors/sensors_custom.py` en het thema `CustomDataExample`.
- **Eigen programma in Python:** `simple-program.py` toont tekst, afbeeldingen en balken rechtstreeks, zonder thema.
