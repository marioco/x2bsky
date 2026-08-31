# Installation von x2bsky

Die Anleitung richtet ausschließlich den X→Bluesky-Transport unter einem unprivilegierten Linux-Benutzer ein. Beispiele verwenden `/opt/x2bsky` für Code und `/var/lib/x2bsky` für Credentials, Logs und State.

## 1. Voraussetzungen

- Linux mit systemd
- Python 3.10 oder neuer
- Git
- Bluesky-Konto mit App-Passwort
- öffentlicher X-Account

```bash
sudo apt update
sudo apt install -y git python3 python3-venv
```

## 2. Benutzer und Verzeichnisse

```bash
sudo useradd --system --create-home --home-dir /var/lib/x2bsky x2bsky
sudo git clone https://github.com/marioco/x2bsky.git /opt/x2bsky
sudo chown -R x2bsky:x2bsky /opt/x2bsky /var/lib/x2bsky
sudo -u x2bsky mkdir -p /var/lib/x2bsky/{creds,logs,tmp/locks}
sudo chmod 700 /var/lib/x2bsky/creds
```

## 3. Python-Umgebung

```bash
sudo -u x2bsky python3 -m venv /opt/x2bsky/.venv
sudo -u x2bsky /opt/x2bsky/.venv/bin/pip install --upgrade pip
sudo -u x2bsky /opt/x2bsky/.venv/bin/pip install -r /opt/x2bsky/requirements.txt
```

## 4. Bluesky-Credentials

`/var/lib/x2bsky/creds/_bluesky_gfrei.txt` enthält genau zwei nichtleere Zeilen:

```text
HANDLE
APP_PASSWORD
```

```bash
sudo chown x2bsky:x2bsky /var/lib/x2bsky/creds/_bluesky_gfrei.txt
sudo chmod 600 /var/lib/x2bsky/creds/_bluesky_gfrei.txt
```

Das App-Passwort wird in Bluesky unter Einstellungen → Datenschutz und Sicherheit → App-Passwörter erzeugt. Niemals das Hauptpasswort verwenden oder Credentials committen.

## 5. Konfiguration

Account und Domains vor Installation in `systemd/x2bsky.service` anpassen.

| Variable | Standard | Bedeutung |
|---|---:|---|
| `XSO_HOME_DIR` | Benutzer-Home | Credentials, Logs und State |
| `XSO_SCRIPTS_DIR` | Repository-Root | Installationspfad |
| `XSO_SCREEN_NAME` | `GFreiNews` | X-Account ohne `@` |
| `XSO_MY_DOMAINS` | `gfrei.news` | bevorzugte Domains, kommasepariert |
| `XSO_FALLBACK_URL` | `https://GFrei.News` | Ersatzlink |
| `XSO_MAX_PER_RUN` | `6` | Höchstzahl neuer Posts pro Lauf |
| `XSO_INCLUDE_RETWEETS` | `1` | Retweets transportieren |
| `XSO_INCLUDE_REPLIES` | `0` | Antworten transportieren |
| `XSO_INCLUDE_POLLS` | `0` | Umfragen transportieren |
| `XSO_DRY` | `0` | bei `1` nicht veröffentlichen |

Videos, GIFs und Zitat-Posts bleiben immer gesperrt.

## 6. Dry-Run

```bash
sudo -u x2bsky env \
  XSO_HOME_DIR=/var/lib/x2bsky \
  XSO_SCRIPTS_DIR=/opt/x2bsky \
  XSO_SCREEN_NAME=GFreiNews \
  XSO_DRY=1 \
  /opt/x2bsky/.venv/bin/python /opt/x2bsky/x2bsky.py --dry-run
```

Der Dry-Run sendet nichts und verändert keinen State.

## 7. systemd

```bash
sudo install -o root -g root -m 0644 /opt/x2bsky/systemd/x2bsky.service /etc/systemd/system/x2bsky.service
sudo install -o root -g root -m 0644 /opt/x2bsky/systemd/x2bsky.timer /etc/systemd/system/x2bsky.timer
sudo systemd-analyze verify /etc/systemd/system/x2bsky.service /etc/systemd/system/x2bsky.timer
sudo systemctl daemon-reload
sudo systemctl enable --now x2bsky.timer
sudo systemctl start x2bsky.service
```

```bash
systemctl status x2bsky.timer --no-pager
systemctl list-timers x2bsky.timer --no-pager
journalctl -u x2bsky.service -n 100 --no-pager
```

Ein erfolgreicher `oneshot`-Dienst ist danach wieder `inactive (dead)`; der Timer muss `active (waiting)` anzeigen.

## 8. Aktualisierung

```bash
sudo -u x2bsky git -C /opt/x2bsky pull --ff-only
sudo -u x2bsky /opt/x2bsky/.venv/bin/pip install -r /opt/x2bsky/requirements.txt
sudo systemctl daemon-reload
sudo systemctl restart x2bsky.timer
sudo systemctl start x2bsky.service
```
