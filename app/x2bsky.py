#!/usr/bin/env python3
# ruff: noqa: BLE001
"""
x2bsky.py

Ziel ist ein konfigurierbares Bluesky-Konto.
Keine Videos. Höchstens 4 Bilder. Text max. 300 Zeichen (Bluesky).

Eigenständiger Einstieg: x2bsky.py
"""

from __future__ import annotations

import argparse
import fcntl
import logging
import os
import sys
import time
import traceback
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path

import requests
from atproto import Client
from PIL import Image
from x2bsky_common import (
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
    validate_configuration,
)

RUNTIME_DIR = Path(
    os.environ.get("X2BSKY_RUNTIME_DIR")
    or (Path.home() / ".local" / "share" / "x2bsky")
).resolve()
CREDS_DIR = RUNTIME_DIR / "credentials"
LOG_DIR = RUNTIME_DIR / "logs"
TMP_DIR = RUNTIME_DIR / "tmp"

MAX_LOG_BYTES = 25_000
LOG_FILE = LOG_DIR / "x2bsky.log"
LOG_OLD = LOG_FILE.with_suffix(".old")
LOCK_FILE = RUNTIME_DIR / "state" / "run.lock"

BSKY_TEXT_MAX = 300
BSKY_MAX_IMAGES = 4
BSKY_MAX_IMAGE_BYTES = 1_000_000

session = requests.Session()
session.headers.update({"User-Agent": "x2bsky/2"})
_HANDLE = ""
_REDACTION_SECRET: str | None = None
_CLIENT: Client | None = None


def log(msg: str) -> None:
    try:
        rotate = LOG_FILE.is_file() and LOG_FILE.stat().st_size > MAX_LOG_BYTES
    except OSError:
        rotate = False
    if rotate:
        try:
            if LOG_OLD.exists():
                LOG_OLD.unlink()
            LOG_FILE.rename(LOG_OLD)
        except OSError:
            logging.getLogger(__name__).debug(
                "Nichtkritischer Fehler wird bewusst ignoriert",
                exc_info=True,
            )
    line = (
        f"[{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')}] "
        f"{redact(msg, _REDACTION_SECRET)}"
    )
    try:
        with LOG_FILE.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError as exc:
        print(f"WARNUNG: Logdatei nicht schreibbar: {exc}", file=sys.stderr)
    print(line)


def prepare_runtime() -> None:
    """Create writable runtime directories before logging or state access."""
    for directory in (LOG_DIR, TMP_DIR, LOCK_FILE.parent):
        try:
            directory.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise RuntimeError(
                f"Laufzeitverzeichnis nicht anlegbar: {directory}: {exc}"
            ) from exc


def acquire_run_lock():
    """Prevent concurrent manual, timer and installer runs."""
    try:
        handle = LOCK_FILE.open("a", encoding="utf-8")
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        raise RuntimeError("x2bsky läuft bereits") from None
    except OSError as exc:
        raise RuntimeError(f"Lauf-Lock nicht verfügbar: {LOCK_FILE}: {exc}") from exc
    return handle


def load_bluesky_creds() -> tuple[str, str]:
    path = CREDS_DIR / "bluesky.credentials"
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise FileNotFoundError(f"bluesky.credentials fehlt: {path}") from None
    except OSError as exc:
        raise RuntimeError(f"bluesky.credentials unlesbar: {exc}") from exc
    lines = [
        ln.strip()
        for ln in raw.splitlines()
        if ln.strip() and not ln.lstrip().startswith("#")
    ]
    if len(lines) != 2 or not lines[0] or not lines[1]:
        raise RuntimeError(
            "bluesky.credentials braucht genau 2 nichtleere Zeilen: Handle, App-Passwort"
        )
    return lines[0], lines[1]


def bluesky_caption(text: str, link: str, log_fn=None) -> str:
    link = (link or "").strip()
    if len(link) >= BSKY_TEXT_MAX - 8:
        link = ""
    room = BSKY_TEXT_MAX - (len(link) + 1 if link else 0)
    room = max(room, 8)
    raw = compose_caption(text, "", log_fn=log_fn)
    body = fit_text(raw, room, log_fn=log_fn)
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
    except (OSError, ValueError) as exc:
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
    except requests.RequestException as exc:
        log(f"Bild holen: {exc}")
        return None


def _is_timeout(exc: BaseException) -> bool:
    if isinstance(exc, (TimeoutError, requests.Timeout)):
        return True
    name = type(exc).__name__.lower()
    if "timeout" in name:
        return True
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


def send_post(caption: str, photos: list[str]) -> str:
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
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument(
        "--check-login",
        action="store_true",
        help="Bluesky-Anmeldung prüfen, ohne Posts zu lesen oder zu senden",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    global _HANDLE, _REDACTION_SECRET, _CLIENT
    args = parse_args(argv)
    dry = args.dry_run or (os.environ.get("X2BSKY_DRY_RUN") or "").strip().lower() in (
        "1",
        "true",
        "yes",
    )
    lock_handle = None
    try:
        prepare_runtime()
        lock_handle = acquire_run_lock()
        log("=== x2bsky gestartet ===")
        validate_configuration()
        _HANDLE, _REDACTION_SECRET = load_bluesky_creds()
        if args.check_login:
            _CLIENT = Client()
            _CLIENT.login(_HANDLE, _REDACTION_SECRET)
            log("Bluesky-Anmeldung erfolgreich geprüft")
            return 0
        state = load_state(log_fn=log)
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
                _CLIENT.login(_HANDLE, _REDACTION_SECRET)
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
                    save_state(state)
                continue
            try:
                enrich_post(post, log_fn=log)
            except Exception as exc:
                log(f"status-html {post.id}: {exc}")
            link = select_link(post.urls)
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
                    save_state(state)
                continue
            caption = bluesky_caption(post.text, link, log_fn=log)
            if dry:
                log(
                    f"DRY {post.id} @{post.author} photos={len(photos)} "
                    f"keys={[media_key(p) for p in photos]} len={len(caption)}"
                )
                log("DRY caption => " + caption.replace("\n", " ⏎ "))
                sent += 1
                continue
            try:
                kind = send_post(caption, photos)
                if photos and kind.startswith("photos"):
                    remember_images(state, photos)
                mark_seen(state, post.id)
                save_state(state)
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
        if LOG_DIR.is_dir():
            log(f"FEHLER: {exc}")
        else:
            print(f"FEHLER: {exc}", file=sys.stderr)
        traceback.print_exc()
        return 1
    finally:
        if lock_handle is not None:
            log("=== x2bsky beendet ===")
            lock_handle.close()


if __name__ == "__main__":
    raise SystemExit(main())
