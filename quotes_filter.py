import re
from typing import Optional, Dict, Any

try:
    from bs4 import BeautifulSoup
except ImportError:
    BeautifulSoup = None

def clean_main_post(*, html: Optional[str] = None, json_status: Optional[Dict[str, Any]] = None) -> str:
    if html is not None:
        return _from_nitter_html(html)
    if json_status is not None:
        return _from_x_json(json_status)
    raise ValueError("Entweder 'html' oder 'json_status' muss gesetzt sein.")

def _from_nitter_html(html: str) -> str:
    if BeautifulSoup is None:
        raise RuntimeError("bs4 nicht installiert")
    soup = BeautifulSoup(html, "lxml")
    for sel in ["div.quote", "div.quoted-tweet", "div.quoted-status", "article.quote", "blockquote", "div.replying-to", "div.retweet"]:
        for node in soup.select(sel):
            node.decompose()
    status_root = soup.select_one("div.status, article.status, div.main-tweet") or soup
    main_block = status_root.find("div", class_="tweet-content")
    text = main_block.get_text(" ", strip=True) if main_block else soup.get_text(" ", strip=True)
    return _post_cleanup(text)

def _from_x_json(status: Dict[str, Any]) -> str:
    if status.get("retweeted") or status.get("retweeted_status"):
        return ""
    full_text = status.get("full_text") or status.get("text") or ""
    full_text = re.sub(r"https?://(?:www\.)?(?:twitter|x|nitter)\.[^\s]+/status/\d+", "", full_text, flags=re.IGNORECASE)
    return _post_cleanup(full_text)

def _post_cleanup(text: str) -> str:
    if not text:
        return ""
    lines = []
    for line in text.splitlines():
        l = line.strip()
        if l and not l.startswith(">") and not (l.startswith("„") and l.endswith("“")):
            lines.append(l)
    text = "\n".join(lines)
    text = re.sub(r"“[^”]{5,}”", "", text, flags=re.DOTALL)
    text = re.sub(r"\s+", " ", text).strip()
    return text
