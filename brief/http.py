"""Tiny HTTP layer with a polite UA, retries and an offline fixture mode."""
from __future__ import annotations

import hashlib
import logging
import os
import time
from pathlib import Path

import requests

log = logging.getLogger(__name__)

UA = "daily-brief/1.0 (+personal news digest; contact via GitHub)"
FIXTURE_DIR = os.environ.get("BRIEF_FIXTURES")  # set → read responses from disk, no network
_session = requests.Session()
_session.headers.update({"User-Agent": UA, "Accept-Language": "en-US,en;q=0.8"})


def _fixture_path(url: str) -> Path:
    return Path(FIXTURE_DIR) / (hashlib.sha1(url.encode()).hexdigest()[:16] + ".txt")


def get_text(url: str, *, params: dict | None = None, timeout: int = 20, retries: int = 2) -> str | None:
    full = requests.Request("GET", url, params=params).prepare().url
    if FIXTURE_DIR:
        p = _fixture_path(full)
        return p.read_text() if p.exists() else None
    for attempt in range(retries + 1):
        try:
            r = _session.get(full, timeout=timeout)
            if r.status_code == 429 and attempt < retries:
                time.sleep(3 * (attempt + 1))
                continue
            r.raise_for_status()
            return r.text
        except requests.RequestException as e:
            if attempt == retries:
                log.warning("fetch failed %s: %s", full, e)
                return None
            time.sleep(1.5 * (attempt + 1))
    return None


def get_json(url: str, **kw):
    import json

    txt = get_text(url, **kw)
    if txt is None:
        return None
    try:
        return json.loads(txt)
    except ValueError:
        log.warning("bad json from %s", url)
        return None
