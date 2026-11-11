# stemn als Max-for-Live-Device

`stemn.amxd` ist ein Audio-Effekt für Ableton Live 12 (Suite oder mit Max for Live), nur Mac.
Er rechnet nicht selbst, sondern startet die installierte **stemn.app** im Hintergrund,
genau wie das App-Fenster es tut. Die App muss also im Programme-Ordner liegen.

## Benutzen

1. Device auf irgendeine Spur ziehen (zu finden unter *Audio Effects → Max Audio Effect → stemn*).
2. Einen Audio-Clip anklicken (Session oder Arrangement). Das Device zeigt ihn sofort an.
   Oder eine Datei aus dem Finder oder Browser auf das Feld ziehen.
3. Oben die Spuren abwählen, die man nicht braucht (oranger Rand = wird angelegt).
4. **TRENNEN**. Der Knopf zeigt den Fortschritt, ein Klick bricht ab.

Danach liegt jede Spur als neue Audiospur direkt unter dem Clip, an derselben Stelle
(Arrangement: gleiche Startzeit, Session: gleicher Slot), mit Warp, Markern und Gain des Originals.
Der Original-Clip wird stummgeschaltet (Clip-Aktivator), ⌘Z macht alles rückgängig.
Eine hereingezogene Datei landet in der Session im gewählten Scene-Slot, im Arrangement an der Abspielposition.

Die Dateien liegen wie bei der App im Ordner `<Loop> stems` neben dem Original
(bei schreibgeschützten Orten wie der Core Library in `~/Music/stemn`).

## Updates

Die App bringt das Device mit. Bei jedem Start legt sie es in die User Library, wenn sich etwas geändert hat.
Ein App-Update aktualisiert also auch das Device (neu laden: Device einmal neu auf die Spur ziehen).

## Bauen

```bash
python m4l/build_device.py             # -> m4l/dist/stemn/
python m4l/build_device.py --install   # zusätzlich in die User Library
```

Das Device ist nicht eingefroren: `stemn.amxd` und die drei Skripte müssen im selben Ordner liegen.

- `stemn-ui.js` (jsui): Oberfläche im loomn-Stil
- `stemn-live.js` (js): Zustand, Live-API (Clip folgen, Spuren und Clips anlegen)
- `stemn-runner.js` (node.script): startet `stemn.app/Contents/MacOS/stemn separate` und liest den Fortschritt

`create_audio_clip` gibt es in der Live-API ab Live 12.0.5.
