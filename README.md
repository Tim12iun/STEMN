# stemn

Zerlegt Drum-Loops lokal und offline in **Kick, Snare, Hi-Hat, Toms, Ride und Crash**.
Die Stems sind sample-genau so lang wie das Original und passen damit direkt aufs Grid in Ableton.

Status: App mit Oberflaeche (Mac und Windows). Das Max-for-Live-Device folgt.

## Benutzen

1. Unter *Releases* das Paket fuer dein System laden: `stemn-macos-arm64.zip` (Mac mit M-Chip) oder `stemn-windows-x64.zip`.
2. Entpacken. Das Modell ist schon enthalten.
   - **Mac:** `stemn.app` in den Programme-Ordner ziehen. Beim ersten Start blockiert macOS die App, weil sie nicht signiert ist:
     Rechtsklick auf die App → *Oeffnen* → *Oeffnen*, oder im Terminal `xattr -dr com.apple.quarantine /Applications/stemn.app`.
   - **Windows:** Ordner `stemn` irgendwohin legen, `stemn.exe` starten. Bei der SmartScreen-Warnung *Weitere Informationen* → *Trotzdem ausfuehren*.
3. Drum-Loop ins Fenster ziehen. Er wird sofort angezeigt und laesst sich mit der Leertaste abspielen.
4. **TRENNEN** druecken. Ein Balken zeigt den Fortschritt, getrennt wird immer in bester Qualitaet. Danach:
   - **Häkchen** schaltet eine Spur an oder aus. Klick auf den Namen, Rechtsklick oder Alt-Klick aufs Häkchen: nur diese Spur (noch mal: alle). Leertaste spielt ab der Klickposition.
   - **Griff rechts** an jeder Spur (Feld mit Punkten) greifen und ziehen: die Spur direkt in Ableton ins Arrangement oder auf eine Spur ziehen.
   - **⠿ Mix** ziehen: alle angehakten Spuren als eine Datei. **⠿ Einzeln** ziehen: jede angehakte Spur als eigene Datei (in Ableton beim Loslassen ⌘ bzw. Strg halten, dann landet jede auf einer eigenen Spur).

Die Dateien liegen im Ordner `<Loop> stems` neben dem Original und sind sample-genau gleich lang, also direkt auf dem Grid.
Am besten funktioniert es mit einer **reinen Drum-Spur** (z.B. vorher mit der Stem-Separation in Live die Drums trennen).
Eingangsformate: WAV, AIFF, FLAC.

### Kommandozeile (optional)

```bash
stemn separate "Loop.wav"                 # alle Stems
stemn separate "Loop.wav" -s kick         # nur die Kick
stemn separate "Loop.wav" -q fast         # schneller (fast / normal / best, Standard: best)
stemn models list                         # Modelle anzeigen
stemn serve                               # Dienst fuer Max for Live (Port 47800)
```

Auf dem Mac liegt das Programm in `stemn.app/Contents/MacOS/stemn`.
Stem-Namen: `kick`, `snare`, `hihat`, `toms`, `ride`, `crash` und `cymbals` (= Ride + Crash zusammen).

## Updates

stemn sucht beim Start des Dienstes (`stemn serve`) selbst nach einer neuen Version im Repo
und installiert sie, sobald stemn beendet ist. Das Modell wird nur neu geladen, wenn es sich geaendert hat.

```bash
stemn update            # jetzt pruefen und installieren
stemn update --check    # nur pruefen
stemn update --auto off # automatische Updates aus
```

Neue Version veroeffentlichen: auf GitHub unter *Actions → Build → Run workflow* eine hoehere
Versionsnummer eintragen (z.B. `0.2.0`) oder einen Tag `v0.2.0` pushen. Der Build erstellt dann das
Release mit den Paketen, und alle installierten stemn holen es sich beim naechsten Start.

Weil das Repo privat ist, braucht stemn einen GitHub-Token mit **nur Lesezugriff auf dieses Repo**.
Er wird beim Build aus dem Repo-Secret `STEMN_UPDATE_TOKEN` ins Paket eingebaut.
Alternativ pro Rechner: `stemn update --token <TOKEN>`.

## Weitere Modelle hinzufuegen

Ohne Code zu aendern:

```bash
stemn models import modell.ckpt config.yaml --name "Mein Drum-Modell"
```

Unterstuetzt werden MDX23C- und Roformer-Modelle (`--arch mdxc`, Standard) sowie Demucs (`--arch demucs`).
Die Stems liest stemn aus der Config (`training.instruments`). Importierte Modelle liegen in:

- Mac: `~/Library/Application Support/stemn/models`
- Windows: `%APPDATA%\stemn\models`

Danach: `stemn separate Loop.wav -m mein-drum-modell`.

## Lokaler Dienst (fuer Max for Live / Ableton)

`stemn serve` lauscht nur auf `127.0.0.1:47800` und haelt das Modell im Speicher.

```
GET  /health
GET  /models
POST /separate  {"input": "/pfad/Loop.wav", "stems": ["kick"], "model": null, "output_dir": null}
             -> {"stems": {"kick": "/pfad/Loop stems/Loop - kick.wav"}}
```

## Eingebautes Modell

`drumsep-mdx23c`: MDX23C DrumSep von aufr33 und jarredou, 6 Stems.
Die Lizenz der Modellgewichte ist nicht offiziell dokumentiert; nur fuer private, nicht-kommerzielle Nutzung verwenden.
Die Gewichte (437 MB) liegen nicht im Git, `scripts/fetch_models.py` laedt sie und prueft die Pruefsumme.

## Entwickeln

```bash
pip install -e ".[dev]"
python scripts/fetch_models.py   # Modell laden
pytest
```

Builds laufen in GitHub Actions (`.github/workflows/build.yml`), manuell oder per Tag `v*`.

## Plattformen und Releases

- **Mac hat Vorrang** (Apple Silicon). Dort wird getestet.
- Jede Version wird gleichzeitig auch fuer **Windows** gebaut und veroeffentlicht, dort aber nicht von Hand getestet (nur die automatischen Tests und der Start-Test im Build).

## Design

Die Oberflaeche folgt der loomn-Design-Language: Name klein mit orangem Punkt, warme Grautoene,
Orange `#FF5A1F` nur fuer Aktives und die eine Hauptaktion (Exportieren), Spurfarben nur gedaempft als Kennfarben,
Schrift Barlow Condensed (SIL Open Font License, mitgeliefert in `src/stemn/fonts`).
