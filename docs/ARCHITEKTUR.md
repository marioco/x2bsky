# Architektur und Betrieb

## Ablauf

```text
systemd timer
  -> x2bsky.py
       -> xso_common.py
            -> X-Profil-HTML
            -> Syndication-Fallback
            -> fxtwitter/vxtwitter-Anreicherung
       -> Bluesky AT Protocol
```

## Verarbeitung

1. Öffentliches X-Profil-HTML abrufen und parsen.
2. Ohne Treffer Syndication-HTML als Fallback lesen.
3. Volltext und Media-Metadaten über fxtwitter/vxtwitter ergänzen.
4. Zitat-Posts, Videos/GIFs und unerwünschte Posttypen sperren.
5. Bereits gesehene IDs und Bilder aus dem Bluesky-State entfernen.
6. Originaltext und Link auf maximal 300 Zeichen kürzen.
7. Höchstens vier Bilder herunterladen und auf maximal 1 MB verkleinern.
8. Auf Bluesky veröffentlichen und erst danach den State atomar ersetzen.

Die X-HTML-Struktur ist eine externe, nicht versionierte Schnittstelle. Parserfehler werden protokolliert; es werden keine Posts erfunden.

## State

`$XSO_HOME_DIR/tmp/xtosocialmedia/state_bluesky.json` enthält gesehene Post-IDs, `last_id` und Bildschlüssel. Geschrieben wird zuerst in eine `.tmp`-Datei und anschließend per atomarem Replace. Der Dry-Run schreibt keinen State.

## Fehlerstrategie

- HTTP-Aufrufe besitzen Timeouts.
- Fehlende Credentials oder Bluesky-Anmeldefehler liefern Exitcode ungleich null.
- Ein Sendefehler markiert den Post nicht als erfolgreich.
- Der nächste Stundenlauf ist der natürliche Retry; keine Endlosschleife.
- Bekannte Credential-Werte werden aus Logmeldungen redigiert.

## Grenzen

- Bluesky-Text: maximal 300 Zeichen
- Bilder: maximal vier
- Bildgröße: maximal 1 MB je Bild
- Videos, GIFs und Zitat-Posts: immer gesperrt

## Betriebschecks

```bash
systemctl is-enabled x2bsky.timer
systemctl is-active x2bsky.timer
systemctl list-timers x2bsky.timer --no-pager
journalctl -u x2bsky.service --since today --no-pager
```
