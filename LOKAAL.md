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

## Arcoloo ops op het schermpje (zonder browser)

`smartmonitor.py ops` toont de winkels van het Arcoloo ops-dashboard om beurten, met de gateway erbij: een groot
verkeerslicht, de naam en acht kleine lampjes (Bereikbaar, Certificaat, Backup, ...). Standaard elke 5 seconden een
andere. Staat er iets in het **rood**, dan toont het enkel nog de rode; met één rode blijft het daarop staan tot het
opgelost is. Hetzelfde beeld als de pagina `/minischerm.html` in ops, maar zonder browser.

**Instellen (één keer)**

1. In ops: **Monitor → 📟 Minischerm → Sleutel voor een pc-programma** → **Kopieer**. De sleutel kan enkel de
   samenvatting lezen (geen details, geen knoppen) en is maar één keer zichtbaar.
2. In deze map:

   ```powershell
   .\start-scherm.cmd ops --url https://arcoloomonitoring.up.railway.app --key
   ```

   Plak de sleutel als erom gevraagd wordt (je ziet ze niet verschijnen) en druk Enter. Adres en sleutel komen in
   `smartmonitor-ops.json` (staat niet in git). Het thema gaat op het schermpje (ca. 20 s) en de winkels verschijnen.
3. Vanzelf starten na het aanmelden, zonder venster:

   ```powershell
   powershell -NoProfile -ExecutionPolicy Bypass -File .\ops-autostart.ps1
   ```

   Kijken of het loopt: hetzelfde met `-Controleer`; uitzetten met `-Uit`. Wat het doet, staat in
   `smartmonitor-ops.log`. Dit zet ook de knoppen in ops aan (hieronder); wil je die zonder vanzelf starten, gebruik
   dan `-Knop`. Alles weg (vanzelf starten én knoppen): `-Verwijder`.

**Starten en stoppen vanuit ops** (Monitor → 📟 Minischerm → *Op deze pc*)

Een webpagina kan zelf geen programma op de pc starten. `ops-autostart.ps1` zet daarom voor jouw Windows-gebruiker het
linktype `arcoloo-minischerm:` klaar (in HKCU, geen beheerder nodig). De knoppen **▶ Starten**, **↻ Opnieuw** en
**■ Stoppen** in ops openen zo'n link, en Windows start dan `smartmonitor.py link` zonder venster:

- Werkt op de pc waarop je klikt. De eerste keer vraagt Chrome "Arcoloo minischerm openen?": vink *altijd toestaan* aan.
- Stand, omgedraaid en seconden kies je ernaast ("zoals ingesteld" = niet veranderen). Loopt het al en kies je iets
  anders, dan start het opnieuw met de nieuwe keuze (bewaard in `smartmonitor-ops.json`).
- Stoppen stopt enkel `smartmonitor.py ops` uit déze map; het eigen dashboard en andere programma's niet.
- Elke webpagina kan zo'n link openen. Daarom aanvaardt `smartmonitor.py link` enkel start / opnieuw / stop met die
  drie instellingen; het adres van ops en de sleutel gaan nooit mee in een link. Een geweigerde link staat in het logboek.
- Of het loopt, zie je in ops bij de sleutel: *laatst gebruikt minder dan een minuut geleden*.
- Verplaats je deze map, voer `ops-autostart.ps1` dan opnieuw uit (de link wijst naar de map; `-Controleer` zegt het).

**Liggend, staand, omgedraaid** (wordt bewaard; het thema gaat daarna vanzelf opnieuw op het schermpje):

| Opdracht | Wat |
|---|---|
| `start-scherm.cmd ops --orientation staand` | staand (320 × 480): de vakjes onder elkaar |
| `start-scherm.cmd ops --orientation liggend` | liggend (480 × 320), standaard |
| `start-scherm.cmd ops --upside-down ja` | het beeld 180° gedraaid (kabel aan de andere kant); `nee` = terug |
| `start-scherm.cmd ops --seconds 10` | elke 10 s een andere winkel (2–120) |
| `start-scherm.cmd ops-preview` | het beeld van nu als `ops-preview.png` — geen schermpje nodig |
| `start-scherm.cmd ops-preview --test --orientation staand` | testbeeld, zonder ops |

Staat het staand beeld op zijn kop, gebruik dan `--upside-down ja`. Loopt het programma al vanzelf (autostart), stop
het dan eerst (`ops-autostart.ps1 -Uit`), wijzig de instelling en zet het weer aan. Of kies het in ops bij *Op deze pc*
en klik **▶ Starten**.

**Goed om te weten**

- Eén programma tegelijk: `smartmonitor.py` weigert te starten als er al een `smartmonitor.py` loopt (dashboard of
  ops). Sluit ook de kassa- of ops-pagina die het schermpje via de browser aanstuurt: twee schrijvers verknoeien het
  opladen van het thema.
- Het gewone dashboard (`start-scherm.cmd` zonder `ops`) zet daarna zijn eigen thema terug; omgekeerd ook.
- Een nieuwe winkel of een andere naam: het thema gaat vanzelf opnieuw op het schermpje. Tot 10 namen; de 10de heet
  dan "Andere winkels" en de teller (11/12) zegt welke het is.
- Onderaan: "Laatste controle 3 min geleden", "Verouderd!" (ops controleert niet meer), "Geen verbinding met ops"
  (3 min geen antwoord) of "Sleutel ongeldig" (ingetrokken in ops: maak een nieuwe en stel ze opnieuw in met `--key`).
- Heeft ops een IP-allowlist, dan moet het internetadres van dit netwerk erin staan.

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
.venv\Scripts\python.exe -m unittest tests.library.smartmonitor.test_smartmonitor tests.library.smartmonitor.test_ops tests.library.smartmonitor.test_link -v
```

29 tests met een nagebootst schermpje (YMODEM-ontvanger die elk blok controleert), het themaformaat, de voorvertoning,
foutmeldingen en de waarden; 27 voor `ops` (beurtrol en rood, het thema in alle vier de standen, de verbinding met ops
met een lokale testserver, opnieuw opladen bij een nieuwe winkel, één programma tegelijk).
