# x2bsky – öffentliche X-Posts nach Bluesky

Diese eigenständige Version transportiert öffentliche Posts eines frei konfigurierbaren X-Accounts ausschließlich nach Bluesky. Sie enthält keine X-/Bluesky-Accountnamen, persönlichen Domains, Credential-Werte oder produktiven Pfade.

Andere vorhandene Anwendungen oder Skripte werden weder gelesen noch überschrieben. x2bsky verwendet ausschließlich eigene Standardpfade:

- Code: `/opt/x2bsky`
- Konfiguration: `/etc/x2bsky/x2bsky.env`
- Credentials und State: `/var/lib/x2bsky`
- Dienst: `x2bsky.service` und `x2bsky.timer`

## Automatische Installation

```bash
sudo apt-get update
sudo apt-get install -y git
git clone https://github.com/marioco/x2bsky.git
cd x2bsky
sudo ./install.sh
```

Unterstützt werden Debian 12, Ubuntu 22.04/24.04 und Linux Mint 21/22 mit aktivem systemd. Fehlende Standardpakete einschließlich Python und venv installiert der Installer dort automatisch über `apt`. Andere Distributionen werden vor jeder Änderung mit einer klaren Meldung abgelehnt.

Der Installer fragt interaktiv ab:

1. X-Benutzername ohne `@`
2. Bluesky-Handle
3. Bluesky-App-Passwort mit verdeckter Eingabe
4. vollständige Fallback-URL
5. optionale bevorzugte Domains

Die Fallback-URL ist die HTTPS-Seite, die ein Bluesky-Post erhält, wenn im X-Post kein geeigneter externer Link vorhanden ist. Bevorzugte Domains priorisieren passende Links aus einem X-Post. Für das Lesen des öffentlichen X-Profils werden keine X-Zugangsdaten benötigt.

Danach erledigt er automatisch:

- Dienstbenutzer und Verzeichnisse anlegen
- Code mit sicheren Rechten installieren
- Credential- und Konfigurationsdateien getrennt schreiben
- Python-venv erzeugen und Requirements installieren
- installierte Python-Abhängigkeiten mit `pip check` prüfen
- systemd-Service und stündlichen Timer rendern und validieren
- Bluesky-Anmeldung prüfen, ohne einen Post zu senden
- produktionsnahen Dry-Run durchführen
- Timer nur nach erfolgreichem Dry-Run aktivieren
- nächsten geplanten Lauf anzeigen

Der Installer überschreibt keine bestehende Quellinstallation. Das feste Ziel `/opt/x2bsky` wird ausschließlich für diese eigenständige Installation verwendet. Bei bereits vorhandenen Dateien aktualisiert er nur die von x2bsky verwalteten Programm- und Unit-Dateien; Credentials werden durch die erneute sichere Abfrage bewusst ersetzt.

Vor einem Update werden neue Python-Abhängigkeiten, Bluesky-Zugangsdaten und das öffentliche X-Profil in einer getrennten Staging-Umgebung geprüft. Schlägt diese Prüfung fehl, werden die bisherige Anwendung, venv und Credentials nicht ersetzt.

## Sichere Vorprüfung

```bash
./install.sh --check
```

Vollständige isolierte Testinstallation ohne root, pip oder systemd:

```bash
X2BSKY_X_USERNAME=test_account \
X2BSKY_BLUESKY_HANDLE=test.example \
X2BSKY_BLUESKY_APP_PASSWORD=test-password \
X2BSKY_MY_DOMAINS=example.org \
X2BSKY_FALLBACK_URL=https://example.org \
X2BSKY_CONFIRM=yes \
./install.sh --test-root /tmp/x2bsky-install-test
```

## Verhalten

- X-Profil-HTML, Syndication-Fallback und fxtwitter/vxtwitter-Anreicherung
- maximal 300 Zeichen und vier Bilder je Bluesky-Post
- maximal 1 MB je Bild, bei Bedarf automatische Verkleinerung
- Videos, GIFs und Zitat-Posts sind immer gesperrt
- Retweets sind standardmäßig erlaubt; Antworten und Umfragen standardmäßig gesperrt
- Prozess-Lock gegen parallele Installer-, Timer- und manuelle Läufe
- atomarer State und ID-/Bildhistorie zur Vermeidung von Doppelposts
- Dry-Run sendet nichts und verändert `state.json` nicht
- HTTP-Aufrufe besitzen Timeouts

Siehe [INSTALLATION.md](INSTALLATION.md) und [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Wichtige Betriebsgrenze

X stellt für diesen Anwendungsfall keinen vertraglich stabilen, anmeldungsfreien Profil-Endpunkt bereit. x2bsky nutzt deshalb öffentliche Webseiten und öffentliche Fallback-Dienste. Änderungen oder Sperren dieser externen Angebote können den Import vorübergehend unterbrechen; sie führen nicht zu einem automatischen Ersatzpost oder zur Löschung lokaler Daten.
