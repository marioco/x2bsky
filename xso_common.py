"""
Gemeinsame Hilfen für x2bsky: X-HTML holen, filtern, State,
Bild-Fingerprints und Text auf Bluesky-Länge bringen.
"""

from __future__ import annotations

import html
import json
import logging
import os
import re
import time
import urllib.parse
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
from bs4 import BeautifulSoup

SCRIPT_PATH = Path(__file__).resolve()
HOME_DIR = Path(os.environ.get("XSO_HOME_DIR") or Path.home()).resolve()
SCRIPTS_DIR = Path(os.environ.get("XSO_SCRIPTS_DIR") or SCRIPT_PATH.parent).resolve()
TMP_DIR = HOME_DIR / "tmp"
STATE_DIR = TMP_DIR / "xtosocialmedia"

SCREEN_NAME = (os.environ.get("XSO_SCREEN_NAME") or "GFreiNews").strip().lstrip("@")
MY_DOMAINS = [
    d.strip().lower()
    for d in (os.environ.get("XSO_MY_DOMAINS") or "gfrei.news").split(",")
    if d.strip()
]
FALLBACK_URL = os.environ.get("XSO_FALLBACK_URL") or "https://GFrei.News"
INCLUDE_RETWEETS = (os.environ.get("XSO_INCLUDE_RETWEETS") or "1").strip().lower() in (
    "1",
    "true",
    "yes",
)
INCLUDE_REPLIES = (os.environ.get("XSO_INCLUDE_REPLIES") or "0").strip().lower() in (
    "1",
    "true",
    "yes",
)
# Quotes und Videos: hart verboten – kein Env-Override (nie transportieren)
INCLUDE_QUOTES = False
INCLUDE_VIDEOS = False
INCLUDE_POLLS = (os.environ.get("XSO_INCLUDE_POLLS") or "0").strip().lower() in (
    "1",
    "true",
    "yes",
)


def _env_int(name: str, default: int) -> int:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


# Stündlicher Lauf: ein paar Posts pro Stunde, kein Stau über den Tag.
MAX_PER_RUN = max(1, _env_int("XSO_MAX_PER_RUN", 6))
SEEN_LIMIT = 500
IMAGE_SEEN_LIMIT = 2000

# Arbeitsbudget vor der abschließenden Bluesky-Kürzung.
DEFAULT_CAPTION_MAX = 300

PROFILE_URL = f"https://x.com/{SCREEN_NAME}"
SYND_URL = (
    f"https://syndication.twitter.com/srv/timeline-profile/screen-name/{SCREEN_NAME}"
)
STATUS_URL = "https://x.com/i/status/{id}"

INTERNAL_DOMAINS = {
    "x.com",
    "twitter.com",
    "t.co",
    "mobile.twitter.com",
    "www.x.com",
    "www.twitter.com",
    "pic.twitter.com",
}
UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
URL_RE = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
MEDIA_KEY_RE = re.compile(r"/media/([A-Za-z0-9_-]+)")
CARD_KEY_RE = re.compile(r"/card_img/(\d+)")

session = requests.Session()
session.headers.update(
    {
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "de-DE,de;q=0.9,en;q=0.8",
        "Cache-Control": "no-cache",
    }
)

LogFn = Callable[[str], None] | None


def _log(fn: LogFn, msg: str) -> None:
    if not fn:
        return
    try:
        fn(msg)
    except Exception:
        logging.getLogger(__name__).debug(
            "Nichtkritischer Fehler wird bewusst ignoriert",
            exc_info=True,
        )


def redact(msg: str, secret: str = "") -> str:
    s = str(msg)
    if secret:
        s = s.replace(secret, "…TOKEN…")
    return s


@dataclass
class Post:
    id: str
    text: str
    created: str = ""
    author: str = ""
    permalink: str = ""
    urls: list[str] = field(default_factory=list)
    photos: list[str] = field(default_factory=list)
    is_reply: bool = False
    is_quote: bool = False
    is_retweet: bool = False
    is_poll: bool = False
    is_video: bool = False  # native Video / GIF / Amplify – nie transportieren

    @property
    def id_int(self) -> int:
        try:
            return int(self.id)
        except (TypeError, ValueError):
            return 0


def fetch_html(
    url: str, timeout: int = 20, tries: int = 3, log_fn: LogFn = None
) -> str | None:
    last_err: Exception | None = None
    for attempt in range(tries):
        try:
            r = session.get(url, timeout=timeout, allow_redirects=True)
            if r.status_code in (429, 500, 502, 503, 504):
                time.sleep(1.2 * (attempt + 1))
                last_err = RuntimeError(f"HTTP {r.status_code}")
                continue
            r.raise_for_status()
            if not r.text or len(r.text) < 200:
                last_err = RuntimeError(f"kurze Antwort ({len(r.text or '')})")
                time.sleep(0.6)
                continue
            return r.text
        except Exception as exc:
            last_err = exc
            time.sleep(0.6 * (attempt + 1))
    _log(log_fn, f"fetch fail {url}: {last_err}")
    return None


def _itemprop(tag) -> str:
    return (tag.get("itemprop") or tag.get("itemProp") or "").strip().lower()


def meta_content(root, prop: str) -> str:
    prop_l = prop.lower()
    for el in root.find_all("meta"):
        if _itemprop(el) == prop_l:
            return html.unescape(el.get("content") or "").strip()
    return ""


def normalize_pbs(url: str) -> str:
    u = html.unescape(url or "").strip()
    if not u:
        return ""
    u = u.replace("&amp;", "&")
    if "pbs.twimg.com/media/" in u:
        base = u.split("?")[0]
        if not re.search(r"\.(jpg|jpeg|png|webp)$", base, re.IGNORECASE):
            return base + "?format=jpg&name=orig"
        return base
    if "pbs.twimg.com/card_img/" in u:
        base = u.split("?")[0]
        return base + "?format=jpg&name=orig"
    return u.split("?")[0]


def media_key(url: str) -> str:
    u = html.unescape(url or "")
    m = MEDIA_KEY_RE.search(u)
    if m:
        return "media:" + m.group(1)
    m = CARD_KEY_RE.search(u)
    if m:
        return "card:" + m.group(1)
    return u.split("?")[0]


def looks_profile_img(url: str) -> bool:
    lu = (url or "").lower()
    return "profile_images" in lu or "_normal." in lu or "profile_banners" in lu


def extract_urls_from_text(text: str) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in URL_RE.findall(text or ""):
        u = raw.rstrip(").,;!?'\"")
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


def _uniq(items: Iterable[str]) -> list[str]:
    out, seen = [], set()
    for it in items:
        s = (it or "").strip()
        if not s or s in seen:
            continue
        seen.add(s)
        out.append(s)
    return out


def parse_x_profile_html(page: str) -> list[Post]:
    try:
        soup = BeautifulSoup(page or "", "html.parser")
    except Exception:
        return []
    posts: list[Post] = []
    seen: set[str] = set()
    for art in soup.find_all("article"):
        try:
            if art.find_parent("article") is not None:
                continue
            tid = (art.get("data-tweet-id") or "").strip() or meta_content(
                art, "identifier"
            )
            if not tid or not tid.isdigit() or tid in seen:
                continue
            seen.add(tid)
            post = _post_from_article(art, tid)
            if post:
                posts.append(post)
        except Exception:
            logging.getLogger(__name__).debug(
                "Datensatz wegen Ausnahme übersprungen",
                exc_info=True,
            )
            continue
    return posts


def _visible_tweet_text(art) -> str:
    """Sichtbarer Tweet-Text aus dem HTML-Knoten (oft länger als itemprop-Meta)."""
    chunks: list[str] = []
    for sel in (
        {"attrs": {"data-testid": "tweetText"}},
        {"name": "p", "attrs": {"lang": True}},
        {"class_": re.compile(r"tweet-text|e-entry-title", re.IGNORECASE)},
    ):
        for el in art.find_all(**sel):
            t = el.get_text("\n", strip=True)
            if t and len(t) > 5:
                chunks.append(t)
        if chunks:
            break
    if not chunks:
        # Fallback: alle <p>
        for el in art.find_all("p"):
            t = el.get_text("\n", strip=True)
            if t and len(t) > 5:
                chunks.append(t)
    if not chunks:
        return ""
    # längsten Block nehmen (vermeidet Autor-Zeilen)
    best = max(chunks, key=len)
    return re.sub(r"\n{3,}", "\n\n", best).strip()


def _post_from_article(art, tid: str) -> Post | None:
    text_meta = meta_content(art, "text")
    text_vis = _visible_tweet_text(art)
    # Längere Quelle gewinnt (Meta oft abgeschnitten)
    if text_vis and (not text_meta or len(text_vis) > len(text_meta)):
        text = text_vis
    else:
        text = text_meta
    author = ""
    for div in art.find_all(True):
        if _itemprop(div) == "author":
            author = meta_content(div, "alternateName") or meta_content(div, "name")
            break
    permalink = meta_content(art, "url") or f"https://x.com/{SCREEN_NAME}/status/{tid}"
    created = meta_content(art, "datePublished") or meta_content(art, "dateCreated")
    photos: list[str] = []

    def absorb(val: str) -> None:
        if not val or looks_profile_img(val):
            return
        if "pbs.twimg.com/media/" in val or "pbs.twimg.com/card_img/" in val:
            photos.append(normalize_pbs(val))

    for el in art.find_all("meta"):
        if _itemprop(el) in ("contenturl", "url", "image", "thumbnailurl"):
            absorb(html.unescape(el.get("content") or ""))
    for img in art.find_all("img"):
        absorb(html.unescape(img.get("src") or ""))

    blob = str(art).lower()
    is_reply = bool(re.match(r"^@[A-Za-z0-9_]+", text or ""))
    is_quote = _detect_quote_html(art, blob)
    is_retweet = bool(author) and author.lower() != SCREEN_NAME.lower()
    if not is_retweet:
        head = blob[:500]
        is_retweet = "reposted by" in head or "retweeted by" in head
    is_poll = bool(re.search(r"\bpoll\b|umfrage|aria-label=\"poll\"", blob))
    is_video = _detect_video_html(art, blob)
    return Post(
        id=tid,
        text=text,
        created=created,
        author=author or SCREEN_NAME,
        permalink=permalink,
        urls=extract_urls_from_text(text),
        photos=_dedupe_photos(photos),
        is_reply=is_reply,
        is_quote=is_quote,
        is_retweet=is_retweet,
        is_poll=is_poll,
        is_video=is_video,
    )


def _detect_quote_html(art, blob: str) -> bool:
    if art.find(attrs={"data-testid": "quoteTweet"}):
        return True
    markers = (
        "quoted tweet",
        "zitat-tweet",
        "zitat tweet",
        "quoting",
        'data-testid="quotetweet"',
        "tweet-quoted",
        "quoted-status",
    )
    return any(m in blob for m in markers)


def _detect_video_html(art, blob: str) -> bool:
    """True wenn der Post natives Video/GIF enthält (nicht nur Link zu YouTube)."""
    if art.find("video"):
        return True
    if art.find(attrs={"data-testid": re.compile(r"video", re.IGNORECASE)}):
        return True
    markers = (
        "video.twimg.com",
        "ext_tw_video",
        "amplify_video",
        "tweetvideoplayer",
        "videoplayer",
        "video player",
        "animated_gif",
        "gifplayer",
        "playable media",
        'data-testid="videoComponent"',
        'data-testid="videoplayer"',
        "/tweet_video/",
        "player:stream",
    )
    return any(m in blob for m in markers)


def _dedupe_photos(photos: list[str]) -> list[str]:
    out, keys = [], set()
    for p in photos:
        key = media_key(p)
        if not key or key in keys:
            continue
        keys.add(key)
        out.append(p)
    return out


def parse_syndication_html(page: str, log_fn: LogFn = None) -> list[Post]:
    m = re.search(
        r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
        page,
        re.DOTALL,
    )
    if not m:
        return []
    try:
        data = json.loads(m.group(1))
    except json.JSONDecodeError as exc:
        _log(log_fn, f"syndication JSON: {exc}")
        return []
    raw_entries = (
        ((data.get("props") or {}).get("pageProps") or {}).get("timeline") or {}
    ).get("entries") or []
    if not isinstance(raw_entries, list):
        return []
    posts: list[Post] = []
    for ent in raw_entries:
        if not isinstance(ent, dict):
            continue
        tw = (ent.get("content") or {}).get("tweet") or {}
        if not isinstance(tw, dict):
            continue
        tid = str(tw.get("id_str") or tw.get("id") or "").strip()
        if not tid.isdigit():
            continue
        user = tw.get("user") if isinstance(tw.get("user"), dict) else {}
        ents = tw.get("entities") if isinstance(tw.get("entities"), dict) else {}
        ext = (
            tw.get("extended_entities")
            if isinstance(tw.get("extended_entities"), dict)
            else {}
        )
        author = str(user.get("screen_name") or "")
        text = str(tw.get("full_text") or tw.get("text") or "")
        media = list(ext.get("media") or ents.get("media") or [])
        photos: list[str] = []
        is_video = False
        for md in media:
            if not isinstance(md, dict):
                continue
            mtype = (md.get("type") or "").lower()
            if mtype in ("video", "animated_gif"):
                is_video = True
                continue  # nie Video/GIF-URLs als Foto
            if mtype != "photo":
                # unbekannte Medientypen: lieber als Video behandeln
                if mtype and mtype not in ("", "photo"):
                    is_video = True
                continue
            pic = md.get("media_url_https") or md.get("media_url") or ""
            if pic:
                photos.append(normalize_pbs(pic))
        urls = []
        for u in ents.get("urls") or []:
            if isinstance(u, dict):
                exp = (u.get("expanded_url") or u.get("url") or "").strip()
                if exp:
                    urls.append(exp)
        urls.extend(extract_urls_from_text(text))
        permalink = (
            tw.get("permalink") or f"https://x.com/{author or SCREEN_NAME}/status/{tid}"
        )
        if str(permalink).startswith("/"):
            permalink = "https://x.com" + permalink
        in_reply = bool(
            tw.get("in_reply_to_status_id_str") or tw.get("in_reply_to_user_id_str")
        )
        is_quote = bool(
            tw.get("is_quote_status")
            or tw.get("quoted_status")
            or tw.get("quoted_status_id_str")
            or tw.get("quoted_status_id")
        )
        is_rt = bool(
            tw.get("retweeted_status")
            or (author and author.lower() != SCREEN_NAME.lower())
        )
        card = tw.get("card") if isinstance(tw.get("card"), dict) else {}
        card_name = str((card or {}).get("name") or "").lower()
        # Player-Cards (Amplify etc.) = Video
        if any(
            x in card_name
            for x in ("player", "video", "amplify", "broadcast", "periscope")
        ):
            is_video = True
        posts.append(
            Post(
                id=tid,
                text=text,
                created=str(tw.get("created_at") or ""),
                author=author or SCREEN_NAME,
                permalink=str(permalink),
                urls=_uniq(urls),
                photos=_dedupe_photos(photos),
                is_reply=in_reply or bool(re.match(r"^@[A-Za-z0-9_]+", text)),
                is_quote=is_quote,
                is_retweet=is_rt,
                is_poll="poll" in card_name,
                is_video=is_video,
            )
        )
    return posts


def merge_posts(primary: list[Post], extra: list[Post]) -> list[Post]:
    by_id: dict[str, Post] = {}
    for p in extra + primary:
        old = by_id.get(p.id)
        if old is None:
            by_id[p.id] = p
            continue
        if p.text and (not old.text or len(p.text) > len(old.text)):
            old.text = p.text
        old.urls = _uniq(old.urls + p.urls)
        old.photos = _dedupe_photos(old.photos + p.photos)
        old.is_reply = old.is_reply or p.is_reply
        old.is_quote = old.is_quote or p.is_quote
        old.is_retweet = old.is_retweet or p.is_retweet
        old.is_poll = old.is_poll or p.is_poll
        old.is_video = old.is_video or p.is_video
        if p.permalink and not old.permalink:
            old.permalink = p.permalink
        if p.author and not old.author:
            old.author = p.author
    return list(by_id.values())


def collect_posts(log_fn: LogFn = None) -> list[Post]:
    posts: list[Post] = []
    x_html = fetch_html(PROFILE_URL, log_fn=log_fn)
    if x_html:
        try:
            got = parse_x_profile_html(x_html)
        except Exception as exc:
            _log(log_fn, f"x.com parse: {exc}")
            got = []
        _log(log_fn, f"x.com HTML: {len(got)} Posts")
        posts = merge_posts(got, [])
    else:
        _log(log_fn, "x.com HTML leer")
    if not posts:
        synd_html = fetch_html(SYND_URL, log_fn=log_fn)
        if synd_html:
            try:
                got = parse_syndication_html(synd_html, log_fn=log_fn)
            except Exception as exc:
                _log(log_fn, f"syndication parse: {exc}")
                got = []
            _log(log_fn, f"syndication HTML: {len(got)} Posts")
            posts = merge_posts(posts, got)
        else:
            _log(log_fn, "syndication HTML leer")
    if not posts:
        raise RuntimeError("keine Posts aus HTML gelesen")
    return posts


def fetch_fxtwitter(
    post_id: str, author: str = "", log_fn: LogFn = None
) -> dict[str, Any] | None:
    """Volltext + Media-Flags über fxtwitter (öffentliche API)."""
    screen = (author or SCREEN_NAME).lstrip("@") or SCREEN_NAME
    urls = [
        f"https://api.fxtwitter.com/{screen}/status/{post_id}",
        f"https://api.vxtwitter.com/{screen}/status/{post_id}",
    ]
    for url in urls:
        try:
            r = session.get(url, timeout=15)
            if r.status_code >= 400:
                continue
            data = r.json()
            if not isinstance(data, dict):
                continue
            # fxtwitter: { tweet: {...} }  |  vxtwitter: flat
            tw = data.get("tweet") if isinstance(data.get("tweet"), dict) else data
            if not isinstance(tw, dict):
                continue
            text = str(tw.get("text") or tw.get("full_text") or "").strip()
            if not text and isinstance(tw.get("raw_text"), dict):
                text = str((tw.get("raw_text") or {}).get("text") or "").strip()
            if (
                text
                or tw.get("mediaURLs")
                or tw.get("media_extended")
                or tw.get("media")
            ):
                return tw
        except Exception as exc:
            _log(log_fn, f"fxtwitter {post_id}: {exc}")
    return None


def apply_fxtwitter(post: Post, tw: dict[str, Any], log_fn: LogFn = None) -> None:
    """Übernimmt längeren Text und Media-Flags aus fxtwitter/vxtwitter."""
    text = str(tw.get("text") or tw.get("full_text") or "").strip()
    if not text and isinstance(tw.get("raw_text"), dict):
        text = str((tw.get("raw_text") or {}).get("text") or "").strip()
    # t.co am Ende oft nur Media-Link – für Body behalten wir URLs separat
    if text and (not post.text or len(text) > len(post.text)):
        _log(log_fn, f"Volltext {post.id}: {len(post.text or '')}→{len(text)} Zeichen")
        post.text = text
        post.urls = _uniq(post.urls + extract_urls_from_text(text))

    # Quote (nur bei echtem Quote-Objekt, nicht leere Keys)
    q = tw.get("quote") or tw.get("qrt") or tw.get("quoted_tweet")
    if isinstance(q, dict) and (q.get("id") or q.get("url") or q.get("text")):
        post.is_quote = True
    qurl = tw.get("qrtURL") or tw.get("quote_url")
    if isinstance(qurl, str) and qurl.strip():
        post.is_quote = True

    # Video / GIF
    media_ext = tw.get("media_extended") or tw.get("media") or []
    if isinstance(media_ext, list):
        for md in media_ext:
            if not isinstance(md, dict):
                continue
            mtype = (md.get("type") or "").lower()
            if mtype in ("video", "gif", "animated_gif"):
                post.is_video = True
            elif mtype == "photo" or md.get("url") or md.get("thumbnail_url"):
                pic = md.get("thumbnail_url") or md.get("url") or ""
                if pic and "video.twimg.com" not in pic and not pic.endswith(".mp4"):
                    if "pbs.twimg.com" in pic or mtype == "photo":
                        post.photos = _dedupe_photos(post.photos + [normalize_pbs(pic)])
    for u in tw.get("mediaURLs") or []:
        su = str(u)
        if _looks_like_video_url(su):
            post.is_video = True
        elif "pbs.twimg.com/media" in su:
            post.photos = _dedupe_photos(post.photos + [normalize_pbs(su)])


def enrich_post(post: Post, log_fn: LogFn = None) -> None:
    """Reichert Text/Media an: fxtwitter (Volltext) + optional Status-HTML."""
    # 1) fxtwitter/vxtwitter – oft vollständiger als x.com-HTML-Meta
    try:
        tw = fetch_fxtwitter(post.id, post.author or SCREEN_NAME, log_fn=log_fn)
        if tw:
            apply_fxtwitter(post, tw, log_fn=log_fn)
    except Exception as exc:
        _log(log_fn, f"fxtwitter enrich: {exc}")

    # 2) Status-HTML (Fallback / Ergänzung)
    page = fetch_html(STATUS_URL.format(id=post.id), timeout=15, tries=2, log_fn=log_fn)
    if not page:
        sanitize_post_media(post)
        return
    extras = parse_x_profile_html(page)
    for other in extras:
        if other.id != post.id:
            continue
        merged = merge_posts([post], [other])
        if merged:
            fresh = merged[0]
            if fresh.text and (not post.text or len(fresh.text) > len(post.text)):
                post.text = fresh.text
            post.urls = _uniq(post.urls + fresh.urls)
            post.photos = _dedupe_photos(post.photos + fresh.photos)
            post.is_reply = fresh.is_reply or post.is_reply
            post.is_quote = fresh.is_quote or post.is_quote
            post.is_retweet = fresh.is_retweet or post.is_retweet
            post.is_poll = fresh.is_poll or post.is_poll
            post.is_video = fresh.is_video or post.is_video
        break
    # og:description als letzter Fallback wenn noch kurz
    try:
        from bs4 import BeautifulSoup as _BS

        soup = _BS(page, "lxml")
        for prop in ("og:description", "twitter:description"):
            for el in soup.find_all("meta"):
                p = (el.get("property") or el.get("name") or "").lower()
                if p == prop:
                    desc = html.unescape(el.get("content") or "").strip()
                    if desc and (not post.text or len(desc) > len(post.text)):
                        post.text = desc
                        post.urls = _uniq(post.urls + extract_urls_from_text(desc))
    except Exception:
        logging.getLogger(__name__).debug(
            "Nichtkritischer Fehler wird bewusst ignoriert",
            exc_info=True,
        )
    sanitize_post_media(post)


def expand_url(url: str) -> str:
    u = (url or "").strip()
    if not u:
        return u
    try:
        r = session.head(u, allow_redirects=True, timeout=10)
        if r.url:
            return r.url
    except Exception:
        logging.getLogger(__name__).debug(
            "Nichtkritischer Fehler wird bewusst ignoriert",
            exc_info=True,
        )
    try:
        r = session.get(u, allow_redirects=True, timeout=12, stream=True)
        final = r.url or u
        try:
            r.close()
        except Exception:
            logging.getLogger(__name__).debug(
                "Nichtkritischer Fehler wird bewusst ignoriert",
                exc_info=True,
            )
        return final
    except Exception:
        return u


def netloc_of(url: str) -> str:
    try:
        return urllib.parse.urlparse(url).netloc.split(":")[0].lower()
    except Exception:
        return ""


def is_internal(domain: str) -> bool:
    d = (domain or "").lower()
    return d in INTERNAL_DOMAINS or d.endswith(".x.com") or "nitter" in d


def select_link(urls: list[str]) -> str:
    expanded = []
    for u in urls:
        try:
            expanded.append(expand_url(u))
        except Exception:
            expanded.append(u)
    for u in expanded:
        host = netloc_of(u)
        for my in MY_DOMAINS:
            if host == my or host.endswith("." + my):
                return u
    for u in expanded:
        if not is_internal(netloc_of(u)):
            return u
    return FALLBACK_URL


def _looks_like_video_url(url: str) -> bool:
    lu = (url or "").lower()
    return any(
        x in lu
        for x in (
            "video.twimg.com",
            "ext_tw_video",
            "amplify_video",
            "/tweet_video/",
            "video.php",
            ".mp4",
            ".m3u8",
        )
    )


def sanitize_post_media(post: Post) -> None:
    """Entfernt Video-URLs aus photos; setzt is_video bei Treffer."""
    if not post.photos:
        return
    keep: list[str] = []
    for p in post.photos:
        if _looks_like_video_url(p):
            post.is_video = True
            continue
        keep.append(p)
    post.photos = keep


def skip_reason(post: Post) -> str | None:
    """Filter vor dem Transport. Quotes und Videos: immer blockiert (kein Env-Override)."""
    sanitize_post_media(post)
    # Hart: Zitate und Videos nie transportieren
    if post.is_quote:
        return "quote"
    if post.is_video:
        return "video"

    author = (post.author or "").lstrip("@").lower()
    if author and author != SCREEN_NAME.lower() and not post.is_retweet:
        return "fremd"
    if post.is_reply and not INCLUDE_REPLIES:
        return "reply"
    if post.is_retweet and not INCLUDE_RETWEETS:
        return "retweet"
    if post.is_poll and not INCLUDE_POLLS:
        return "poll"
    return None


def strip_inline_urls(text: str) -> str:
    t = URL_RE.sub(" ", text or "")
    return re.sub(r"\s+", " ", t).strip()


def hard_trim(text: str, limit: int) -> str:
    t = (text or "").strip()
    if len(t) <= limit:
        return t
    if limit < 8:
        return t[:limit]
    cut = t[: limit - 1]
    for sep in (". ", "! ", "? ", "\n"):
        i = cut.rfind(sep)
        if i >= limit // 2:
            return cut[: i + 1].strip()
    i = cut.rfind(" ")
    if i >= limit // 2:
        return cut[:i].rstrip() + "…"
    return cut.rstrip() + "…"


def fit_text(text: str, limit: int, log_fn: LogFn = None) -> str:
    """
    Kürzt nur bei Überschreiten des Limits – Originalwortlaut, kein Umschreiben.

    Satzgrenzen bevorzugt (hard_trim). Keine KI-Neutexte.
    """
    t = (text or "").strip()
    if limit < 1:
        return ""
    if len(t) <= limit:
        return t
    trimmed = hard_trim(t, limit)
    _log(log_fn, f"Text gekürzt {len(t)}→{len(trimmed)} (Limit {limit}, Original)")
    return trimmed


def compose_caption(
    text: str,
    link: str,
    *,
    has_photo: bool,
    log_fn: LogFn = None,
    max_chars: int | None = None,
) -> str:
    """
    Baut Caption + optionalen Link aus dem Originaltext.

    Kürzt nur, wenn der Text (inkl. Link) das Limit überschreitet.
    Keine KI-Erweiterung, keine eigenen Texte.
    """
    body = strip_inline_urls(text)
    link = (link or "").strip()
    if max_chars is None:
        max_chars = DEFAULT_CAPTION_MAX
    max_chars = max(32, int(max_chars))

    room = max_chars - (len(link) + 1 if link else 0)
    room = max(room, 40)

    if len(body) > room:
        body = fit_text(body, room, log_fn=log_fn)

    if link and link not in body:
        cap = f"{body}\n{link}".strip()
    else:
        cap = body.strip()

    if len(cap) > max_chars:
        keep_link = link if link and cap.rstrip().endswith(link) else ""
        room2 = max_chars - (len(keep_link) + 1 if keep_link else 0)
        body2 = hard_trim(body, max(room2, 0))
        cap = f"{body2}\n{keep_link}".strip() if keep_link else body2
        cap = cap[:max_chars]
    return cap


def state_path(platform: str = "bluesky") -> Path:
    name = re.sub(r"[^a-z0-9]+", "", (platform or "bluesky").lower()) or "bluesky"
    return STATE_DIR / f"state_{name}.json"


def load_state(log_fn: LogFn = None, platform: str = "bluesky") -> dict:
    empty = {"seen_ids": [], "last_id": "", "seen_images": []}
    path = state_path(platform)
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        _log(log_fn, f"State-Verzeichnis: {exc}")
    if not path.is_file():
        return empty
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("state ist kein Objekt")
        data.setdefault("seen_ids", [])
        data.setdefault("last_id", "")
        data.setdefault("seen_images", [])
        if not isinstance(data["seen_ids"], list):
            data["seen_ids"] = []
        if not isinstance(data["seen_images"], list):
            data["seen_images"] = []
        return data
    except Exception as exc:
        _log(log_fn, f"State unlesbar ({path.name}): {exc}")
        last = ""
        try:
            raw = path.read_text(encoding="utf-8", errors="replace")
            m = re.search(r'"last_id"\s*:\s*"(\d+)"', raw)
            if m:
                last = m.group(1)
        except OSError:
            logging.getLogger(__name__).debug(
                "Nichtkritischer Fehler wird bewusst ignoriert",
                exc_info=True,
            )
        if last:
            _log(log_fn, f"State: last_id {last} gerettet")
            return {"seen_ids": [last], "last_id": last, "seen_images": []}
        raise RuntimeError(f"{path.name} unlesbar: {exc}") from exc


def save_state(st: dict, platform: str = "bluesky") -> None:
    seen = st.get("seen_ids") or []
    imgs = st.get("seen_images") or []
    if isinstance(seen, list):
        st["seen_ids"] = seen[-SEEN_LIMIT:]
    if isinstance(imgs, list):
        st["seen_images"] = imgs[-IMAGE_SEEN_LIMIT:]
    path = state_path(platform)
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(st, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
    except OSError as exc:
        raise RuntimeError(f"{path.name} nicht speicherbar: {exc}") from exc


def already_seen(st: dict, eid: str) -> bool:
    return bool(eid) and eid in (st.get("seen_ids") or [])


def mark_seen(st: dict, eid: str) -> None:
    if not eid:
        return
    seen = list(st.get("seen_ids") or [])
    if eid not in seen:
        seen.append(eid)
    st["seen_ids"] = seen[-SEEN_LIMIT:]
    if not st.get("last_id") or id_int(eid) > id_int(st.get("last_id")):
        st["last_id"] = eid


def remember_images(st: dict, urls: list[str]) -> None:
    have = list(st.get("seen_images") or [])
    keys = set(have)
    for u in urls:
        k = media_key(u)
        if k and k not in keys:
            have.append(k)
            keys.add(k)
    st["seen_images"] = have[-IMAGE_SEEN_LIMIT:]


def unused_photos(st: dict, urls: list[str]) -> list[str]:
    have = set(st.get("seen_images") or [])
    out = []
    local: set[str] = set()
    for u in urls:
        k = media_key(u)
        if not k or k in have or k in local:
            continue
        local.add(k)
        out.append(u)
    return out


def id_int(eid: Any) -> int:
    try:
        return int(str(eid))
    except (TypeError, ValueError):
        return 0


def snowflake_time(eid: str) -> datetime:
    ms = (id_int(eid) >> 22) + 1288834974657
    return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc)


def select_new_posts(posts: list[Post], st: dict) -> list[Post]:
    last = id_int(st.get("last_id"))
    posts = sorted(posts, key=lambda p: p.id_int)
    new: list[Post] = []
    for p in posts:
        if not p.id_int or already_seen(st, p.id):
            continue
        if last and p.id_int <= last:
            continue
        new.append(p)
    if not last and new:
        return new[-1:]
    now = datetime.now(timezone.utc)
    fresh = []
    for p in new:
        try:
            age = (now - snowflake_time(p.id)).total_seconds()
        except Exception:
            logging.getLogger(__name__).debug(
                "Zeitstempel für Post %s konnte nicht gelesen werden",
                p.id,
                exc_info=True,
            )
            fresh.append(p)
            continue
        if age <= 3 * 86400:
            fresh.append(p)
    if fresh:
        new = fresh
    elif new:
        # nur alte Kandidaten – nicht den Stapel nachreichen
        return []
    return new[:MAX_PER_RUN]
