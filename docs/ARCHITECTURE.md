# Architektur

## Komponenten

```text
x2bsky.timer
  -> x2bsky.service
       -> app/x2bsky.py
            -> app/x2bsky_common.py
                 -> X-Profil-HTML
                 -> Syndication-Fallback
                 -> fxtwitter/vxtwitter
            -> Bluesky AT Protocol
```

## Konfigurationsgrenze

Das Repository enthält ausschließlich neutrale Logik. Der Installer trennt drei Bereiche:

- unveränderlicher Code unter `/opt/x2bsky`
- nicht geheime Einstellungen unter `/etc/x2bsky`
- Credentials, Logs und State unter `/var/lib/x2bsky`

Accountnamen, Domains und Passwörter sind keine Codekonstanten. Fehlende oder ungültige Werte brechen vor Netzwerk- oder Veröffentlichungsaktionen mit Exitcode ungleich null ab.

Bei Installationen und Updates entstehen neue venv, App-Kopie und Prüflaufzeit zunächst als Staging-Bestand. Bluesky-Anmeldung und X-Dry-Run müssen dort erfolgreich sein, bevor die aktive Anwendung ersetzt wird.

## Verarbeitung

1. exklusiven Lauf-Lock setzen, Konfiguration validieren und Bluesky-Credentials laden.
2. X-Profil-HTML abrufen; bei fehlenden Treffern Syndication nutzen.
3. Volltext und Media-Metadaten optional anreichern.
4. Videos, GIFs, Zitate und unerwünschte Posttypen sperren.
5. bereits verarbeitete IDs und Bilder aus dem State entfernen.
6. Originaltext und Link auf 300 Zeichen begrenzen.
7. höchstens vier Bilder laden und auf höchstens 1 MB bringen.
8. Bluesky veröffentlichen und erst danach State atomar ersetzen.

## Sicherheit

- systemd läuft als unprivilegierter Benutzer
- `NoNewPrivileges`, `ProtectSystem=strict`, `ProtectHome=true`
- Schreibzugriff nur auf das eigene Laufzeitverzeichnis
- App-Passwort in separater Datei mit Modus `600`
- keine Credentials in Unit, EnvironmentFile, Git oder Kommandozeile
- gesonderte Loginprüfung ohne Lesen oder Veröffentlichen von Posts
- Dry-Run ohne Login, Posting oder Änderung von `state.json`
- exklusiver Dateilock gegen parallele Installer-, Timer- und manuelle Läufe
- keine Endlos-Retries; der nächste Timerlauf übernimmt temporäre Fehler
