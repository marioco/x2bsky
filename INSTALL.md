# Installationsanleitung – x2bsky

Diese Anleitung führt dich Schritt für Schritt durch die Einrichtung von **x2bsky** – einem automatischen Crossposter von X (Twitter) nach Bluesky.

## Projektbeschreibung

x2bsky überträgt neue Posts von einem X-Account vollautomatisch auf Bluesky. Es filtert Quotes und Retweets heraus, lädt Bilder korrekt hoch und läuft stabil im Hintergrund.

## Voraussetzungen

- Linux-Server (Ubuntu 24.04/26.04, Debian 12 oder Raspberry Pi OS)
- SSH-Zugriff
- Docker und Docker Compose
- Python 3.10 oder neuer
- Ein Bluesky-Account

## Schritt 1: System vorbereiten

```bash
sudo apt update && sudo apt full-upgrade -y
sudo apt install -y curl wget git nano htop ufw ca-certificates
Optional: Firewall einrichten
Bashsudo ufw allow OpenSSH
sudo ufw allow 8080/tcp
sudo ufw --force enable
Schritt 2: Nitter per Docker installieren
Bashmkdir -p ~/nitter
cd ~/nitter
docker-compose.yml anlegen (mit nano docker-compose.yml):
YAMLversion: "3"

services:
  nitter:
    image: zedeus/nitter:latest
    container_name: nitter
    ports:
      - "8080:8080"
    volumes:
      - ./nitter.conf:/src/nitter.conf:ro
    restart: unless-stopped
    depends_on:
      - redis

  redis:
    image: redis:alpine
    container_name: nitter-redis
    restart: unless-stopped
    volumes:
      - redis-data:/data

volumes:
  redis-data:
nitter.conf anlegen:
ini[Server]
address = "0.0.0.0"
port = 8080
https = false
enableRSS = true

[Cache]
rssMinutes = 10
redisHost = "redis"

[Config]
hmacKey = "changeme123"
enableDebug = false
Nitter starten:
Bashdocker compose up -d
Testen im Browser:
texthttp://DEINE-SERVER-IP:8080/dein_x_username/rss
Schritt 3: x2bsky Projekt einrichten
Bashcd ~
git clone https://github.com/marioco/x2bsky.git
cd x2bsky

python3 -m venv venv
source venv/bin/activate

pip install -r requirements.txt
Schritt 4: .env anlegen
Bashcp .env.example .env
nano .env
Beispiel:
envBSKY_HANDLE=deinname.bsky.social
BSKY_APP_PASSWORD=dein-app-passwort-hier

FEED_URL=http://127.0.0.1:8080/dein_x_username/rss
Schritt 5: Testen
Bashpython x2bsky.py
Schritt 6: Automatischer Start (systemd)
Bashsudo cp systemd/x2bsky.service /etc/systemd/system/
sudo cp systemd/x2bsky.timer /etc/systemd/system/

sudo systemctl daemon-reload
sudo systemctl enable --now x2bsky.timer
Unterstützung
Dieses Projekt ist komplett kostenlos und quelloffen.
Falls es dir geholfen hat, freue ich mich über ein kleines Dankeschön:

PayPal: https://www.paypal.com/donate/?hosted_button_id=MUACSGW4UGADU
Bitcoin: bc1qsxsftstx2pjk8p32ad8j87fu06708nuhyenjej

Vielen Dank!
Lizenz: MIT
