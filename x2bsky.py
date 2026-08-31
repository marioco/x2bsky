#!/usr/bin/env python3
"""
5030_bluesky.py

Ziel ist ein konfigurierbares Bluesky-Konto.
Keine Videos. Höchstens 4 Bilder. Text max. 300 Zeichen (Bluesky).

Eigenständiger Einstieg: x2bsky.py
"""

from __future__ import annotations

import argparse
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
from atproto import Client

SCRIPT_PATH = Path(__file__).resolve()
HOME_DIR = Path(os.environ.get("XSO_HOME_DIR") or Path.home()).resolve()
SCRIPTS_DIR = Path(os.environ.get("XSO_SCRIPTS_DIR") or SCRIPT_PATH.parent).resolve()
CREDS_DIR = HOME_DIR / "creds"
LOG_DIR = HOME_DIR / "logs"
TMP_DIR = HOME_DIR / "tmp"

from xso_common import (
    collect_posts,
    compose_caption,
    enrich_post,
    fit_text,
    hard_trim,
    load_state,
    mark_seen,
    media_key,
    redact,
    remember_images,
    save_state,
    select_link,
    select_new_posts,
    skip_reason,
    unused_photos,
)

MAX_LOG_BYTES = 25_000
try:
    LOG_FILE = (LOG_DIR / SCRIPT_PATH.relative_to(SCRIPTS_DIR)).with_suffix(".log")
except ValueError:
    LOG_FILE = LOG_DIR / "projekte" / "xtosocialmedia" / f"{SCRIPT_PATH.stem}.log"
LOG_OLD = LOG_FILE.with_suffix(".old")
for _d in (LOG_FILE.parent, TMP_DIR):
    try:
        _d.mkdir(parents=True, exist_ok=True)
    except OSError:
        logging.getLogger(__name__).debug(
            "Nichtkritischer Fehler wird bewusst ignoriert",
            exc_info=True,
        )

PLATFORM = "bluesky"
BSKY_TEXT_MAX = 300
BSKY_MAX_IMAGES = 4
BSKY_MAX_IMAGE_BYTES = 1_000_000

session = requests.Session()
session.headers.update({"User-Agent": "LauraXsoBluesky/1"})
_HANDLE = ""
_PASSWORD = ""
_CLIENT: Client | None = None


def log(msg: str) -> None:
    if LOG_FILE.is_file() and LOG_FILE.stat().st_size > MAX_LOG_BYTES:
        try:
            if LOG_OLD.exists():
                LOG_OLD.unlink()
            LOG_FILE.rename(LOG_OLD)
        except OSError:
            logging.getLogger(__name__).debug(
                "Nichtkritischer Fehler wird bewusst ignoriert",
                exc_info=True,
            )
    line = f"[{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')}] {redact(msg, _PASSWORD)}"
    try:
        with LOG_FILE.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        logging.getLogger(__name__).debug(
            "Nichtkritischer Fehler wird bewusst ignoriert",
            exc_info=True,
        )
    print(line)


def load_bluesky_creds() -> tuple[str, str]:
    path = CREDS_DIR / "_bluesky_gfrei.txt"
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise FileNotFoundError(f"_bluesky_gfrei.txt fehlt: {path}") from None
    except OSError as exc:
        raise RuntimeError(f"_bluesky_gfrei.txt unlesbar: {exc}") from exc
    lines = [
        ln.strip()
        for ln in raw.splitlines()
        if ln.strip() and not ln.lstrip().startswith("#")
    ]
    if len(lines) < 2 or not lines[0] or not lines[1]:
        raise RuntimeError(
            "_bluesky_gfrei.txt braucht 2 nichtleere Zeilen: Handle, App-Passwort"
        )
    return lines[0], lines[1]


def bluesky_caption(text: str, link: str, log_fn=None) -> str:
    link = (link or "").strip()
    if len(link) >= BSKY_TEXT_MAX - 8:
        link = ""
    room = BSKY_TEXT_MAX - (len(link) + 1 if link else 0)
    room = max(room, 8)
    try:
        raw = compose_caption(text, "", has_photo=False, log_fn=log_fn)
    except Exception:
        raw = text or ""
    try:
        body = fit_text(raw, room, log_fn=log_fn)
    except Exception:
        body = hard_trim(raw, room)
    if link and link not in body:
        cap = f"{body}\n{link}".strip()
    else:
        cap = body.strip()
    if len(cap) > BSKY_TEXT_MAX:
        body2 = hard_trim(body, room)
        cap = f"{body2}\n{link}".strip() if link else body2
        if len(cap) > BSKY_TEXT_MAX:
            cap = cap[:BSKY_TEXT_MAX]
    return cap or (link[:BSKY_TEXT_MAX] if link else ".")


def shrink_image(data: bytes) -> bytes | None:
    """Bluesky nimmt höchstens 1 MB pro Bild."""
    if data and len(data) <= BSKY_MAX_IMAGE_BYTES:
        return data
    try:
        from io import BytesIO

        from PIL import Image

        img = Image.open(BytesIO(data))
        img.load()
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        for max_side in (1600, 1200, 1000, 800, 640):
            w, h = img.size
            scale = min(1.0, max_side / max(w, h, 1))
            out = img
            if scale < 1.0:
                out = img.resize(
                    (max(1, int(w * scale)), max(1, int(h * scale))),
                    Image.Resampling.LANCZOS,
                )
            buf = BytesIO()
            out.save(buf, format="JPEG", quality=82, optimize=True)
            cand = buf.getvalue()
            if cand and len(cand) <= BSKY_MAX_IMAGE_BYTES:
                return cand
    except Exception as exc:
        log(f"Bild verkleinern: {exc}")
        return None
    return None


def download_image(url: str) -> bytes | None:
    try:
        r = session.get(url, timeout=30)
        r.raise_for_status()
        data = r.content or b""
        if not data:
            return None
        ctype = (r.headers.get("Content-Type") or "").lower().split(";")[0].strip()
        if ctype and not (
            ctype.startswith("image/")
            or ctype in ("application/octet-stream", "binary/octet-stream")
        ):
            return None
        return shrink_image(data)
    except Exception as exc:
        log(f"Bild holen: {exc}")
        return None


def _is_timeout(exc: BaseException) -> bool:
    if isinstance(exc, (TimeoutError, requests.Timeout)):
        return True
    name = type(exc).__name__.lower()
    if "timeout" in name:
        return True
    try:
        import httpx

        if isinstance(exc, httpx.TimeoutException):
            return True
    except Exception:
        logging.getLogger(__name__).debug(
            "Nichtkritischer Fehler wird bewusst ignoriert",
            exc_info=True,
        )
    return "timeout" in str(exc).lower()


def _is_unsure_send(exc: BaseException) -> bool:
    """Timeout/5xx/Rate-Limit: Post kann schon draußen sein – kein Text-Fallback."""
    if _is_timeout(exc):
        return True
    msg = str(exc).lower()
    if any(s in msg for s in ("429", "502", "503", "504", "rate limit", "temporarily")):
        return True
    name = type(exc).__name__.lower()
    return "network" in name or "servererror" in name or "invoke" in name


def _is_too_long(exc: BaseException) -> bool:
    msg = str(exc).lower()
    return any(
        s in msg
        for s in (
            "grapheme",
            "too long",
            "maxlength",
            "maximum length",
            "max graphemes",
        )
    )


def bsky_send_text(text: str) -> None:
    if _CLIENT is None:
        raise RuntimeError("Bluesky-Client nicht angemeldet")
    try:
        _CLIENT.send_post(text=text)
        return
    except Exception as exc:
        if not _is_too_long(exc):
            raise
        short = hard_trim(text, 240)
        log(f"Text zu lang für Bluesky, nochmal mit {len(short)} Zeichen")
        _CLIENT.send_post(text=short)


def bsky_send_images(text: str, photo_urls: list[str]) -> str:
    if _CLIENT is None:
        raise RuntimeError("Bluesky-Client nicht angemeldet")
    blobs: list[bytes] = []
    for url in photo_urls[:BSKY_MAX_IMAGES]:
        data = download_image(url)
        if not data:
            continue
        blobs.append(data)
    if not blobs:
        raise RuntimeError("keine Bilder geladen")
    alts = ["Bild"] * len(blobs)
    try:
        _CLIENT.send_images(text=text or ".", images=blobs, image_alts=alts)
    except Exception as exc:
        if _is_too_long(exc):
            short = hard_trim(text, 240)
            log(f"Bildtext zu lang, nochmal mit {len(short)} Zeichen")
            _CLIENT.send_images(text=short or ".", images=blobs, image_alts=alts)
        else:
            raise
    return "photos"


def send_post(post, caption: str, photos: list[str]) -> str:
    if photos:
        try:
            return bsky_send_images(caption, photos)
        except Exception as exc:
            if _is_unsure_send(exc):
                log(f"Bilder unsicher, kein Text-Fallback: {exc}")
                raise
            log(f"Bilder abgelehnt, sende Text: {exc}")
    bsky_send_text(caption)
    return "text"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="X nach Bluesky")
    p.add_argument("--dry-run", action="store_true")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    global _HANDLE, _PASSWORD, _CLIENT
    args = parse_args(argv)
    dry = args.dry_run or (os.environ.get("XSO_DRY") or "").strip().lower() in (
        "1",
        "true",
        "yes",
    )
    log("=== 5030_bluesky gestartet ===")
    try:
        _HANDLE, _PASSWORD = load_bluesky_creds()
        state = load_state(log_fn=log, platform=PLATFORM)
        posts = collect_posts(log_fn=log)
        batch = select_new_posts(posts, state)
        log(
            f"kandidaten={len(batch)} last_id={state.get('last_id')} "
            f"seen_img={len(state.get('seen_images') or [])}"
        )
        if not batch:
            log("nichts Neues")
            return 0
        if not dry:
            try:
                _CLIENT = Client()
                _CLIENT.login(_HANDLE, _PASSWORD)
                log("Bluesky angemeldet")
            except Exception as exc:
                raise RuntimeError(f"Bluesky-Anmeldung fehlgeschlagen: {exc}") from exc

        sent = 0
        for post in batch:
            reason = skip_reason(post)
            if reason:
                log(f"SKIP {post.id} {reason}")
                if not dry:
                    mark_seen(state, post.id)
                    save_state(state, platform=PLATFORM)
                continue
            try:
                enrich_post(post, log_fn=log)
            except Exception as exc:
                log(f"status-html {post.id}: {exc}")
            try:
                link = select_link(post.urls)
            except Exception as exc:
                log(f"link {post.id}: {exc}")
                link = "https://GFrei.News"
            photos = unused_photos(state, post.photos)[:BSKY_MAX_IMAGES]
            skipped = len(post.photos) - len(photos)
            if skipped:
                log(
                    f"{post.id}: {skipped} Bilder schon gesendet/über Limit, {len(photos)} neu"
                )
            if not (post.text or "").strip() and not photos:
                log(f"SKIP {post.id} leer")
                if not dry:
                    mark_seen(state, post.id)
                    save_state(state, platform=PLATFORM)
                continue
            try:
                caption = bluesky_caption(post.text, link, log_fn=log)
            except Exception as exc:
                log(f"Caption {post.id}: {exc}")
                caption = hard_trim(
                    (post.text or "") + ("\n" + link if link else ""), BSKY_TEXT_MAX
                )
            if dry:
                log(
                    f"DRY {post.id} @{post.author} photos={len(photos)} "
                    f"keys={[media_key(p) for p in photos]} len={len(caption)}"
                )
                log("DRY caption => " + caption.replace("\n", " ⏎ "))
                sent += 1
                continue
            try:
                kind = send_post(post, caption, photos)
                if photos and kind.startswith("photos"):
                    remember_images(state, photos)
                mark_seen(state, post.id)
                save_state(state, platform=PLATFORM)
                log(
                    f"SENT {post.id} type={kind} photos={len(photos)} len={len(caption)}"
                )
                sent += 1
                time.sleep(1.2)
            except Exception as exc:
                log(f"SEND FAIL {post.id}: {exc}")
                return 1
        log(f"fertig gesendet={sent}")
        return 0
    except Exception as exc:
        log(f"FEHLER: {exc}")
        import traceback

        traceback.print_exc()
        return 1
    finally:
        log("=== 5030_bluesky beendet ===")


if __name__ == "__main__":
    raise SystemExit(main())
