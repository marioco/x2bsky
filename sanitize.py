import logging
import os
import re
import html
from typing import List, Optional

def init_sanitize_logger(log_path=None, debug_env="0"):
    logger = logging.getLogger("sanitize")
    if getattr(logger, "_initialized", False):
        return logger
    logger.setLevel(logging.DEBUG)
    log_path = log_path or os.getenv("SANITIZE_LOG", "logs/sanitize.log")
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    fh = logging.FileHandler(log_path, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fmt = logging.Formatter("%(asctime)s %(levelname)s: %(message)s")
    fh.setFormatter(fmt)
    logger.addHandler(fh)
    if os.getenv("SANITIZE_DEBUG", debug_env) == "1":
        ch = logging.StreamHandler()
        ch.setLevel(logging.DEBUG)
        ch.setFormatter(fmt)
        logger.addHandler(ch)
    logger._initialized = True
    return logger

def clean_extracted_urls(urls, logger=None, debug=False):
    lg = logger or init_sanitize_logger()
    out = []
    seen = set()
    for raw in urls or []:
        m = re.search(r'https?://[^\s"\'<]+', str(raw), re.IGNORECASE)
        if not m:
            continue
        cleaned = m.group(0)
        cleaned = re.sub(r'[)\].,>\'"]+$', '', cleaned)
        norm = re.sub(r'#.*$', '', cleaned).rstrip('/')
        if norm in seen:
            continue
        seen.add(norm)
        out.append(cleaned)
    return out

def clean_text_fields(raw_html, urls, my_domains, debug=False, logger=None):
    lg = logger or init_sanitize_logger()
    if not raw_html:
        return ""
    t = re.sub(r'<[^>]+>', ' ', html.unescape(str(raw_html)))
    t = re.sub(r'https?://\S+', ' ', t)
    t = re.sub(r'\s+', ' ', t).strip()
    return t

def final_sanitize_caption(caption_text, link, my_domains, debug=False, logger=None):
    lg = logger or init_sanitize_logger()
    text = caption_text or ""
    text = re.sub(r'<[^>]+>', ' ', html.unescape(text))
    text = re.sub(r'https?://\S+', ' ', text)
    text = re.sub(r'\s+', ' ', text).strip()
    if link:
        clean_link = re.sub(r'[)\].,>\'"]+$', '', link)
        text = f"{text}\n{clean_link}" if text else clean_link
    return text.strip()
