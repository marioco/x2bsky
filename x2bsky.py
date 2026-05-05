#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json, os, re, time
from pathlib import Path
from typing import List, Optional

import feedparser, requests
from dotenv import load_dotenv

from sanitize import clean_text_fields, final_sanitize_caption, init_sanitize_logger, clean_extracted_urls
from quotes_filter import clean_main_post

from atproto import Client, models

VERSION = "x2bsky 2026-05-05 FIXED"
def report(msg: str): print(msg, flush=True)
report(VERSION)

# ====================== Config ======================
load_dotenv()

BSKY_HANDLE     = os.getenv("BSKY_HANDLE")
BSKY_PASSWORD   = os.getenv("BSKY_APP_PASSWORD")
FEED_URL        = os.getenv("FEED_URL")
MY_DOMAINS      = [d.strip().lower() for d in os.getenv("MY_DOMAINS","").split(",") if d.strip()]
FALLBACK        = os.getenv("FALLBACK_URL", "https://GFrei.News")
DEBUG           = os.getenv("DEBUG") == "1"

STATE_FILE = Path("state.json")
SEEN_LIMIT = 500

# ====================== Init ======================
sanitize_logger = init_sanitize_logger(log_path="logs/sanitize.log")

client = Client()
try:
    if not BSKY_HANDLE or not BSKY_PASSWORD:
        raise ValueError("BSKY credentials fehlen")
    client.login(BSKY_HANDLE, BSKY_PASSWORD)
    report(f"✅ Angemeldet als @{BSKY_HANDLE}")
except Exception as e:
    report(f"❌ Login fehlgeschlagen: {e}")
    raise SystemExit(1)

# ====================== Helpers ======================
def entry_eid(entry) -> str:
    cand = getattr(entry, "id", "") or getattr(entry, "link", "")
    m = re.search(r'/status/(\d+)', cand)
    return m.group(1) if m else cand

def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception as e:
            report(f"State defekt, wird neu erstellt: {e}")
    return {"seen_ids": [], "last_id": ""}

def save_state(st: dict):
    try:
        STATE_FILE.write_text(json.dumps(st, indent=2))
    except Exception as e:
        report(f"State speichern fehlgeschlagen: {e}")

def already_seen(st: dict, eid: str) -> bool:
    return eid in (st.get("seen_ids") or [])

def mark_seen(st: dict, eid: str):
    seen = st.get("seen_ids") or []
    if eid not in seen:
        seen.append(eid)
    st["seen_ids"] = seen[-SEEN_LIMIT:]
    st["last_id"] = eid

def smart_truncate(text: str, max_len: int = 280) -> str:
    if not text:
        return ""
    if len(text) <= max_len:
        return text

    for end in ['. ', '! ', '? ', '.\n']:
        pos = text[:max_len-40].rfind(end)
        if pos > 80:
            return text[:pos+1].strip() + " …"

    pos = text[:max_len-40].rfind(' ')
    return (text[:pos].strip() + " …") if pos > 50 else text[:max_len-3] + " …"

def clean_nitter_and_image_text(text: str) -> str:
    if not text:
        return ""

    # Entfernt typische Nitter-/Twitter-Artefakte
    text = re.sub(r'\bimage\b:? ?', '', text, flags=re.IGNORECASE)
    text = re.sub(r'Image\s+https?://[^\s]+', '', text, flags=re.IGNORECASE)
    text = re.sub(r'https?://nitter\.net[^\s]*', '', text, flags=re.IGNORECASE)

    # Mehrfache Leerzeichen entfernen
    text = re.sub(r'\s+', ' ', text).strip()
    return text

def safe_request(url: str) -> Optional[bytes]:
    try:
        resp = requests.get(
            url,
            timeout=20,
            headers={"User-Agent": "Mozilla/5.0"}
        )
        resp.raise_for_status()
        return resp.content
    except Exception as e:
        report(f"Download fehlgeschlagen: {url} → {e}")
        return None

# ====================== Bluesky Poster ======================
def bsky_post_text(text: str, link: Optional[str] = None):
    text = clean_nitter_and_image_text(text)

    if link and "nitter.net" in link:
        link = None

    content = f"{text}\n\n{link}" if link else text
    content = smart_truncate(content)

    if content.strip():
        try:
            client.send_post(text=content.strip())
            report("→ Text-Post gesendet")
        except Exception as e:
            report(f"❌ Text-Post fehlgeschlagen: {e}")

def bsky_post_with_images(text: str, image_urls: List[str]):
    images = []

    for url in image_urls[:4]:
        data = safe_request(url)
        if not data:
            continue

        try:
            upload = client.upload_blob(data)
            images.append(models.AppBskyEmbedImages.Image(
                alt="Bild",
                image=upload.blob
            ))
        except Exception as e:
            report(f"Upload fehlgeschlagen: {e}")

    clean_text = clean_nitter_and_image_text(text)
    clean_text = smart_truncate(clean_text)

    try:
        if images:
            embed = models.AppBskyEmbedImages.Main(images=images)

            # 🔥 FIX: Kein Dummy-Text mehr → verhindert "Image"
            if clean_text:
                client.send_post(text=clean_text, embed=embed)
            else:
                client.send_post(text="", embed=embed)

            report(f"→ Bild-Post mit {len(images)} Bild(er) gesendet")

        elif clean_text:
            bsky_post_text(clean_text)

    except Exception as e:
        report(f"❌ Bild-Post fehlgeschlagen: {e}")

# ====================== Main ======================
def main():
    if not FEED_URL:
        report("❌ FEED_URL fehlt")
        return

    state = load_state()
    last_id = state.get("last_id", "")

    try:
        feed_url = f"{FEED_URL}{'&' if '?' in FEED_URL else '?'}t={int(time.time())}"
        feed = feedparser.parse(feed_url)
    except Exception as e:
        report(f"Feed konnte nicht geladen werden: {e}")
        return

    if not getattr(feed, "entries", None):
        report("Keine Feed-Einträge gefunden")
        return

    entries = sorted(
        feed.entries,
        key=lambda e: getattr(e, "published_parsed", (0,0,0))
    )

    candidates = [e for e in entries if entry_eid(e) > last_id] if last_id else entries[-5:]

    report(f"Neue Kandidaten: {len(candidates)}")

    for e in candidates:
        eid = entry_eid(e)

        if not eid or already_seen(state, eid):
            continue

        report(f"Verarbeite {eid}")

        try:
            raw_html = f"{getattr(e,'title','')} {getattr(e,'summary','')}"
            cleaned_main = clean_main_post(html=raw_html)

            if not cleaned_main.strip():
                report(f"SKIPPED id={eid} → Quote / Retweet")
                mark_seen(state, eid)
                save_state(state)
                continue

            urls = clean_extracted_urls(
                re.findall(r'https?://\S+', raw_html) + [getattr(e, "link", "")]
            )

            link = next(
                (u for u in urls if any(d in u.lower() for d in MY_DOMAINS)),
                None
            )

            caption = final_sanitize_caption(
                caption_text=cleaned_main,
                link=link,
                my_domains=MY_DOMAINS,
                debug=DEBUG,
                logger=sanitize_logger
            )

            imgs = [
                u for u in urls
                if u.lower().endswith(('.jpg','.jpeg','.png','.webp'))
            ]

            if imgs:
                bsky_post_with_images(caption, imgs)
            else:
                bsky_post_text(caption, link)

            mark_seen(state, eid)
            save_state(state)
            time.sleep(3)

        except Exception as ex:
            report(f"❌ Fehler {eid}: {ex}")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        report(f"Fatal Error: {e}")
