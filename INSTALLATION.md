# Präzise Installationsanleitung

## Unterstützte Systeme

- Debian 12, Ubuntu 22.04/24.04 oder Linux Mint 21/22
- aktives systemd
- sudo/root-Zugriff
- Netzwerkzugriff auf X, fxtwitter/vxtwitter, PyPI und Bluesky
- öffentlicher X-Account
- Bluesky-Konto und eigens erzeugtes App-Passwort

Fehlende Standardpakete wie Python 3, venv, `flock` und Benutzerverwaltungswerkzeuge installiert der Installer auf diesen Systemen automatisch über `apt`. Andere Distributionen werden sicher abgelehnt, statt ungetestete Paketbefehle auszuführen.

Für X wird kein Passwort oder API-Token benötigt, weil ausschließlich das öffentliche Profil gelesen wird. Im Bluesky-Konto muss vor der Installation ein separates App-Passwort erzeugt werden; das normale Kontopasswort soll nicht verwendet werden.

Die Fallback-URL muss eine vollständige HTTPS-URL sein. Sie wird verwendet, wenn ein X-Post keinen geeigneten externen Link enthält. Die optionale Domainliste legt fest, welche externen Links bevorzugt werden.

Der Installer nimmt keine Account- oder Domainwerte aus dem Repository. Alle installationsspezifischen Werte werden abgefragt oder ausdrücklich über Umgebungsvariablen bereitgestellt.

## Interaktive Installation

```bash
sudo apt-get update
sudo apt-get install -y git
git clone https://github.com/marioco/x2bsky.git
cd x2bsky
sudo ./install.sh
```

Der Installer erklärt jede Eingabe, zeigt anschließend alle nicht geheimen Angaben noch einmal an und beginnt erst nach Bestätigung. Das App-Passwort bleibt immer verdeckt.

Die Eingaben werden validiert. Das App-Passwort erscheint nicht im Terminal. Es wird ausschließlich in `/var/lib/x2bsky/credentials/bluesky.credentials` mit Modus `600` gespeichert. Die nicht geheime Konfiguration liegt in `/etc/x2bsky/x2bsky.env` mit Modus `640`.

Vor dem ersten Anwendungs-Update prüft der Installer Python-Version, benötigte Systemprogramme und ein aktives systemd. Fehlt eine Voraussetzung oder wird eine Eingabe abgebrochen, endet er mit einer eindeutigen Fehlermeldung und Exitcode ungleich null.

Der Installer aktiviert den Timer erst, wenn:

1. alle Quelldateien vorhanden sind,
2. Python-Syntax stimmt,
3. venv und Requirements installiert und mit `pip check` konsistent sind,
4. die systemd-Units valide sind und
5. die Bluesky-Anmeldung ohne Posting erfolgreich war und
6. ein echter X-Dry-Run ohne Posting erfolgreich war.

## Unbeaufsichtigte Installation

Für Provisionierung können die Antworten als Umgebungsvariablen übergeben werden. Secrets gehören dabei ausschließlich in eine geschützte Provisionierungsumgebung, niemals in Shellskripte oder Git:

```bash
sudo env \
  X2BSKY_X_USERNAME='<X_USERNAME>' \
  X2BSKY_BLUESKY_HANDLE='<BLUESKY_HANDLE>' \
  X2BSKY_BLUESKY_APP_PASSWORD='<BLUESKY_APP_PASSWORD>' \
  X2BSKY_MY_DOMAINS='<DOMAIN1,DOMAIN2>' \
  X2BSKY_FALLBACK_URL='<HTTPS_URL>' \
  X2BSKY_CONFIRM=yes \
  ./install.sh
```

`X2BSKY_CONFIRM=yes` ersetzt ausschließlich die sichtbare Abschlussbestätigung. Alle Werte werden weiterhin validiert sowie Bluesky-Login und X-Lesepfad praktisch geprüft.

## Ergebnis

| Bestandteil | Pfad |
|---|---|
| Anwendung | `/opt/x2bsky/app/` |
| venv | `/opt/x2bsky/.venv/` |
| nicht geheime Konfiguration | `/etc/x2bsky/x2bsky.env` |
| Bluesky-Credentials | `/var/lib/x2bsky/credentials/bluesky.credentials` |
| State | `/var/lib/x2bsky/state/state.json` |
| Lauf-Lock | `/var/lib/x2bsky/state/run.lock` |
| Logs | `/var/lib/x2bsky/logs/x2bsky.log` und `.old` |
| Units | `/etc/systemd/system/x2bsky.service` und `.timer` |

## Prüfung

```bash
systemctl is-enabled x2bsky.timer
systemctl is-active x2bsky.timer
systemctl list-timers x2bsky.timer --no-pager
journalctl -u x2bsky.service -n 100 --no-pager
```

Der Service ist `Type=oneshot` und nach einem erfolgreichen Lauf wieder `inactive (dead)`. Der Timer muss `active (waiting)` sein.

Standardmäßig werden Retweets transportiert; Antworten und Umfragen werden ausgelassen. Videos, GIFs und Zitat-Posts sind fest gesperrt. Der Timer läuft stündlich mit einer zufälligen Verzögerung von höchstens fünf Minuten.

## Isolierter Installertest

`--test-root` prüft und rendert dieselbe Verzeichnis-, Credential-, Konfigurations- und Unit-Struktur unter einem beliebigen absoluten Testpfad. Benutzerverwaltung, venv/pip, systemd und externe Zugriffe werden dabei bewusst nicht ausgeführt. Dieser Modus ist ein Strukturtest; die vollständige Installation wird nur ohne `--test-root` durchgeführt.

```bash
./install.sh --test-root /tmp/x2bsky-test
```

## Aktualisierung

```bash
cd x2bsky
git pull --ff-only
sudo ./install.sh
```

Der Git-Abruf wird bewusst nicht vom Installer ausgeführt. Eine neue Quellversion wird mit `sudo ./install.sh` installiert. Neue venv, Anwendung, Bluesky-Zugangsdaten und X-Lesepfad werden zunächst separat geprüft. Erst danach aktualisiert der Installer Anwendung, Konfiguration und Units. Ein exklusiver Lauf-Lock verhindert parallele Verarbeitung. Bestehender State bleibt erhalten. Das Bluesky-App-Passwort wird erneut sicher abgefragt und gezielt ersetzt.

## Typische Fehler

- **„X2BSKY_X_USERNAME ungültig“:** nur den X-Namen ohne führendes `@` eingeben.
- **„Bluesky-Anmeldung fehlgeschlagen“:** Bluesky-Handle und ein neu erzeugtes App-Passwort prüfen; nicht das normale Kontopasswort verwenden.
- **„keine Posts aus HTML gelesen“:** Das X-Profil muss öffentlich und vom Server erreichbar sein.
- **„systemd ist … nicht aktiv“:** Das System gehört nicht zur unterstützten Installationsumgebung.
- **Installer lehnt ein vorhandenes Ziel ab:** Dort liegen fremde Dateien. Der Installer überschreibt sie absichtlich nicht.

Bei einem Fehler wird der Timer nicht neu aktiviert. Bei einem fehlgeschlagenen Update bleiben die zuvor geprüfte venv, Anwendung und Credentials erhalten, sofern die Staging-Prüfung noch nicht erfolgreich abgeschlossen war.
