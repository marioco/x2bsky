# x2bsky

Automatischer Crossposter von X (Twitter) nach Bluesky.

Das Projekt überträgt neue Posts von einem X-Account vollautomatisch auf Bluesky – inklusive Text, Bilder und intelligenter Filterung von Quotes und Retweets.

## Funktionen

- Holt Posts über selbst gehosteten Nitter (RSS)
- Filtert Quotes, Retweets und leere Posts automatisch heraus
- Kürzt Texte intelligent auf das Bluesky-Limit (300 Graphemes)
- Lädt Bilder korrekt hoch
- Läuft stabil als systemd-Timer im Hintergrund

## Voraussetzungen

- Linux-Server (Ubuntu, Debian oder Raspberry Pi OS)
- Docker + Docker Compose (für Nitter)
- Python 3.10+
- Bluesky Account + App Passwort

## Schnellstart

```bash
git clone https://github.com/marioco/x2bsky.git
cd x2bsky
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
nano .env          # ← deine Daten eintragen

python x2bsky.py   # Testlauf

Vollständige Installationsanleitung

Die detaillierte Schritt-für-Schritt-Anleitung findest du in der Datei:
INSTALL.md (im Repository)

Unterstützung
Dieses Projekt ist komplett kostenlos und quelloffen.
Falls es dir geholfen hat, freue ich mich über ein kleines Dankeschön:

PayPal: https://www.paypal.com/donate/?hosted_button_id=MUACSGW4UGADU
Bitcoin: bc1qsxsftstx2pjk8p32ad8j87fu06708nuhyenjej

Vielen Dank!
Lizenz
MIT License
