# x2bsky

`x2bsky` liest neue öffentliche Posts eines X-Accounts und veröffentlicht sie auf Bluesky. Diese Repository-Version enthält ausschließlich den Bluesky-Transport.

## Funktionen

- X-Profil-HTML als primäre Quelle, Syndication-HTML als Fallback
- fxtwitter/vxtwitter zur Volltext- und Media-Anreicherung
- eigener atomarer Bluesky-State gegen Doppelposts und doppelte Bilder
- harte Sperre für Videos, GIFs und Zitat-Posts
- konfigurierbare Behandlung von Antworten, Retweets und Umfragen
- Kürzung auf das Bluesky-Limit von 300 Zeichen
- bis zu vier Bilder, automatische Verkleinerung auf maximal 1 MB je Bild
- Dry-Run ohne Veröffentlichung oder State-Änderung
- stündlicher systemd-Timer

## Dateien

| Datei | Aufgabe |
|---|---|
| `x2bsky.py` | Bluesky-Einstiegspunkt |
| `xso_common.py` | X-Quelle, Parser, Filter, Text, Media und State |
| `requirements.txt` | direkte Python-Abhängigkeiten |
| `systemd/x2bsky.*` | stündlicher Dienst und Timer |
| `INSTALLATION.md` | vollständige Installation |
| `docs/ARCHITEKTUR.md` | Technik, State und Fehlerstrategie |
| `docs/Installationsanleitung_x2bsky.docx` | druckbare Anleitung |

## Schnelltest

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
XSO_DRY=1 .venv/bin/python x2bsky.py --dry-run
```

Der Dry-Run benötigt gültige Bluesky-Credentials und liest die X-Quelle, sendet aber nichts und verändert keinen State.

## Laufzeitdaten

- Credentials: `$XSO_HOME_DIR/creds/_bluesky_gfrei.txt`
- Logs: `$XSO_HOME_DIR/logs/`
- State: `$XSO_HOME_DIR/tmp/xtosocialmedia/state_bluesky.json`
- Standard für `XSO_HOME_DIR`: Home-Verzeichnis des Dienstbenutzers
- Standard für `XSO_SCRIPTS_DIR`: automatisch erkannter Repository-Root

Secrets, Logs, State und temporäre Dateien gehören nicht ins Git.

## Sicherheit

- Keine Zugangsdaten im Repository oder in Kommandozeilenargumenten
- Kein Posten von Videos, GIFs oder Zitat-Posts
- State wird erst nach erfolgreicher Veröffentlichung fortgeschrieben
- HTTP-Zugriffe verwenden explizite Timeouts
- Dry-Run schreibt keinen State

## Lizenz

MIT
