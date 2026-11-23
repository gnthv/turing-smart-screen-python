# Lokaal gebruik — 3,5"-schermpje (SmartMonitor, USB 0483:0065)

Fork van [mathoudebine/turing-smart-screen-python](https://github.com/mathoudebine/turing-smart-screen-python) met
eigen dashboards voor het kleine USB-C-schermpje. Los project, niet gekoppeld aan andere apps.

## Welk schermpje is dit?

Amazon ASIN B0CW2LSDD4 ("Hsthe Sea", model CP032074). Op de pc gemeten (23-11-2026): **geen** Turing Smart Screen
maar een **SmartMonitor** — USB-HID `0483:0065`, fabrikant `llhmi.com`, product `llTechCo.,Ltd`, 480 x 320. Verkocht als
"3.5 inch PC CPU Data Monitor".

Dit schermpje is geen gewoon beeldscherm: het speelt een **thema** af dat in zijn eigen geheugen staat (achtergrond,
lettertekens en vakjes voor getallen). De pc laadt dat thema één keer op en stuurt daarna alleen **gehele getallen** en
de **klok**. Daarom werkt `main.py` (voor Turing/XuanFang) er niet mee; hiervoor is `smartmonitor.py`.

Het protocol en het themaformaat komen uit de openbare beschrijving van
[sensordeck](https://github.com/norareit/sensordeck) (`docs/protocol.md`, `docs/theme-format.md`).

## 1. Installeren (één keer)

In PowerShell, in deze map:

```powershell
py -3.12 -m venv .venv
```

```powershell
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## 2. Starten

1. Sluit andere schermpjes-software af (ook in het systeemvak).
2. Steek het schermpje in.
3. Dubbelklik **`start-scherm.cmd`**.

De eerste keer, en telkens als het thema gewijzigd is, wordt het thema opgeladen: ca. 15 seconden, het schermpje
herstart twee keer. Daarna komen de waarden en de klok elke seconde binnen. Stoppen: het venster sluiten.

Het schermpje houdt zelf de tijd niet bij: zonder `start-scherm.cmd` blijft de klok stilstaan op de laatste waarde.

Automatisch starten met Windows: `Win + R` → `shell:startup` → maak daar een snelkoppeling naar `start-scherm.cmd`
(eigenschappen: Uitvoeren = Geminimaliseerd).

## Opdrachten

In PowerShell in deze map (of `start-scherm.cmd <opdracht>`):

| Opdracht | Wat |
|---|---|
| `.venv\Scripts\python.exe smartmonitor.py` | starten (= `start-scherm.cmd`) |
| `... smartmonitor.py preview` | het thema tekenen als `preview.png` in de themamap — geen schermpje nodig |
| `... smartmonitor.py metrics` | alle waarden die deze pc kan leveren, met hun huidige waarde |
| `... smartmonitor.py upload` | het thema opnieuw opladen, ook als het niet gewijzigd is |
| `... smartmonitor.py devices` | toont of het schermpje gevonden wordt |
| `... smartmonitor.py send 2=42` | testwaarde 42 naar tag 2 sturen (tags: zie `compile`) |
| `... smartmonitor.py compile` | `img.dat` + de lijst met tags schrijven |

## Eigen dashboard

1. Kopieer `res\smartmonitor\themes\standaard` naar een nieuwe map, bv. `res\smartmonitor\themes\mijn`.
2. Pas `theme.yaml` aan en bekijk het resultaat met `smartmonitor.py preview --theme res/smartmonitor/themes/mijn`.
3. Zet `theme: res/smartmonitor/themes/mijn` in `smartmonitor.yaml` en start: het nieuwe thema wordt vanzelf opgeladen.

`theme.yaml`:

```yaml
name: Mijn dashboard
background: "#0b0f17"            # een kleur, of een PNG (wordt 480 x 320)
fonts:                           # TTF-bestanden (uit de themamap of res/fonts), grootte in pixels
  label: {file: res/fonts/roboto/Roboto-Medium.ttf, size: 15, color: "#8b98ad"}
  big:   {file: res/fonts/roboto/Roboto-Bold.ttf, size: 46, color: "#ffffff"}
widgets:                         # in deze volgorde getekend
  - {type: rect, x: 8, y: 8, width: 228, height: 124, color: "#151b26", radius: 12}
  - {type: text, font: label, text: "CPU", x: 22, y: 18}                 # align: left/center/right
  - {type: number, font: big, metric: cpu.percent, digits: 3, align: right, x: 22, y: 42}
  - {type: bar, metric: cpu.percent, x: 22, y: 108, width: 200, height: 12, color: "#4cc2ff", background: "#263042"}
  - {type: datetime, font: label, format: "DD/MM/YYYY HH:mm:ss", x: 16, y: 290}
  - {type: image, file: icoon.png, x: 400, y: 10}                         # PNG met transparantie
metrics:                         # eigen waarden: een commando dat een getal teruggeeft
  buiten.temp: {command: "powershell -NoProfile -Command \"(Invoke-RestMethod 'https://...').temp\"", every: 300}
```

- `rect` en `text` liggen vast in de achtergrond (kosten niets); `number`, `bar` en `datetime` krijgen live waarden.
- `number`: `digits` = breedte in cijfers (het minteken telt mee). `thousands: true` toont waarde/1000 met één
  decimaal: `ram.used.mb` 9830 → `9.8` (GB). Vanaf 10000 zonder decimaal.
- `bar`: waarde 0–100 vult de balk van links; zonder waarde toont het schermpje hem vol.
- `datetime`: `YYYY MM DD HH mm ss` en de tekens `. - / :` en spatie.
- Een eigen metric (`metrics:`) loopt op de achtergrond elke `every` seconden; het eerste getal van de uitvoer telt,
  maal `scale` (standaard 1).

## Grenzen van het schermpje

- Alleen gehele getallen van -32768 tot 32767 (wordt afgerond en begrensd).
- Tekst kan niet veranderen (staat in de achtergrond).
- Hoogstens 62 widgets (`rect`/`text` niet meegeteld) en een thema van hoogstens 503 808 bytes; de achtergrond neemt
  er al 307 200. Grote lettertypes en afbeeldingen kosten het meest — `smartmonitor.py preview` toont de grootte.
- Geen CPU-temperatuur op Windows (psutil kan die niet lezen). GPU-waarden alleen voor NVIDIA.
- Het originele thema van de fabrikant is overschreven; terugzetten kan alleen met de software van de fabrikant.

## Problemen

- **"Schermpje niet gevonden"**: kijk met `smartmonitor.py devices`; andere software afsluiten; ander USB-poortje.
- **Klok blijft op 12:00:00 / 2022-01-01 en er verandert niets**: het schermpje hangt (gebeurt soms na het
  opstarten van de pc). Trek het uit en steek het terug in.
- **Opladen mislukt**: uittrekken, terug insteken, `smartmonitor.py upload`.

## Tests

```powershell
.venv\Scripts\python.exe -m unittest tests.library.smartmonitor.test_smartmonitor -v
```

29 tests met een nagebootst schermpje (YMODEM-ontvanger die elk blok controleert), het themaformaat, de voorvertoning,
foutmeldingen en de waarden.
