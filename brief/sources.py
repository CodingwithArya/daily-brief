"""Fetchers. Each returns a list[Item]; failures are logged and return []."""
from __future__ import annotations

import html
import json
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher

import feedparser

from .http import get_json, get_text
from .models import Item

log = logging.getLogger(__name__)
UTC = timezone.utc


# ── helpers ──────────────────────────────────────────────────────────────────
def clean(text: str | None, limit: int = 400) -> str:
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(re.sub(r"\s+", " ", text)).strip()
    return text if len(text) <= limit else text[: limit - 1].rsplit(" ", 1)[0] + "…"


def _dt(struct) -> datetime | None:
    if not struct:
        return None
    try:
        return datetime.fromtimestamp(time.mktime(struct), tz=UTC)
    except (OverflowError, ValueError, TypeError):
        return None


def _iso(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def _duration_min(raw) -> int | None:
    if not raw:
        return None
    raw = str(raw).strip()
    try:
        if ":" in raw:
            secs = 0
            for part in raw.split(":"):
                secs = secs * 60 + int(float(part))
        else:
            secs = int(float(raw))
        return max(1, round(secs / 60))
    except ValueError:
        return None


def _base(src: dict, **kw) -> dict:
    return dict(source=src["name"], section=src["section"], weight=float(src.get("weight", 1.0)),
                also_sections=[src["also"]] if src.get("also") else [], **kw)


# ── RSS / Atom ───────────────────────────────────────────────────────────────
def fetch_rss(src: dict, ctx: dict) -> list[Item]:
    txt = get_text(src["url"])
    if not txt:
        return []
    feed = feedparser.parse(txt)
    out = []
    for e in feed.entries[:40]:
        title = clean(e.get("title"), 200)
        if not title:
            continue
        # Google News titles end with " - Publisher"
        out.append(Item(
            title=title, url=e.get("link", ""),
            published=_dt(e.get("published_parsed") or e.get("updated_parsed")),
            summary=clean(e.get("summary") or e.get("description")),
            **_base(src, kind=src.get("item_kind", "article")),
        ))
    return out


# ── Hacker News (Algolia) ────────────────────────────────────────────────────
def fetch_hn(src: dict, ctx: dict) -> list[Item]:
    since = int((ctx["now"] - timedelta(hours=ctx["lookback"])).timestamp())
    data = get_json("https://hn.algolia.com/api/v1/search", params={
        "tags": "story", "hitsPerPage": 60,
        "numericFilters": f"points>{src.get('min_points', 200)},created_at_i>{since}",
    })
    out = []
    for h in (data or {}).get("hits", []):
        hn_url = f"https://news.ycombinator.com/item?id={h['objectID']}"
        out.append(Item(
            title=clean(h.get("title"), 200), url=h.get("url") or hn_url,
            published=datetime.fromtimestamp(h["created_at_i"], tz=UTC),
            popularity=float(h.get("points", 0)),
            summary=f"{h.get('points', 0)} points · {h.get('num_comments', 0)} comments on HN",
            extra={"discussion": hn_url}, **_base(src),
        ))
    return out


# ── Hugging Face Daily Papers (community-upvoted arXiv papers) ──────────────
def fetch_hf_papers(src: dict, ctx: dict) -> list[Item]:
    data = get_json("https://huggingface.co/api/daily_papers", params={"limit": 60}) or []
    out = []
    for d in data:
        p = d.get("paper", d)
        pid = p.get("id")
        ups = p.get("upvotes", d.get("upvotes", 0)) or 0
        if not pid or ups < src.get("min_upvotes", 10):
            continue
        out.append(Item(
            title=clean(p.get("title") or d.get("title"), 220),
            url=f"https://huggingface.co/papers/{pid}",
            published=_iso(d.get("publishedAt") or p.get("publishedAt")),
            summary=clean(p.get("ai_summary") or p.get("summary"), 350),
            popularity=float(ups), kind="paper",
            extra={"pdf": f"https://arxiv.org/pdf/{pid}", "arxiv_id": pid,
                   "authors": ", ".join(a.get("name", "") for a in p.get("authors", [])[:4])},
            **_base(src),
        ))
    return out


# ── arXiv API ────────────────────────────────────────────────────────────────
_ARXIV_LAST = [0.0]


def fetch_arxiv(src: dict, ctx: dict) -> list[Item]:
    wait = 3.1 - (time.time() - _ARXIV_LAST[0])   # arXiv asks for ≤1 req / 3 s
    if wait > 0:
        time.sleep(wait)
    _ARXIV_LAST[0] = time.time()
    query = re.sub(r"\s+", " ", src["query"]).strip()
    txt = get_text("http://export.arxiv.org/api/query", params={
        "search_query": query, "sortBy": "submittedDate", "sortOrder": "descending",
        "max_results": src.get("max_results", 40),
    }, timeout=40)
    if not txt:
        return []
    out = []
    for e in feedparser.parse(txt).entries:
        abs_url = e.get("id", "").replace("http://", "https://")
        aid = re.sub(r"v\d+$", "", abs_url.rsplit("/abs/", 1)[-1])
        authors = [a.get("name", "") for a in e.get("authors", [])]
        out.append(Item(
            title=clean(e.get("title"), 220), url=f"https://arxiv.org/abs/{aid}",
            published=_dt(e.get("published_parsed")), summary=clean(e.get("summary"), 350),
            kind="paper",
            extra={"pdf": f"https://arxiv.org/pdf/{aid}", "arxiv_id": aid,
                   "authors": ", ".join(authors[:4]) + (" et al." if len(authors) > 4 else "")},
            **_base(src),
        ))
    return out


# ── YouTube (channel RSS; handle → channel id resolved once and cached) ─────
def _resolve_youtube(src: dict, ctx: dict) -> str | None:
    if src.get("channel_id"):
        return src["channel_id"]
    cache = ctx["state"].setdefault("resolved", {})
    key = f"yt:{src['handle']}"
    if key in cache:
        return cache[key]
    page = get_text(f"https://www.youtube.com/@{src['handle']}")
    m = page and (re.search(r'"externalId":"(UC[\w-]{22})"', page)
                  or re.search(r'<link rel="canonical" href="https://www\.youtube\.com/channel/(UC[\w-]{22})"', page)
                  or re.search(r'"channelId":"(UC[\w-]{22})"', page))
    if m:
        cache[key] = m.group(1)
        return m.group(1)
    log.warning("could not resolve YouTube handle @%s — add channel_id to config", src["handle"])
    return None


def fetch_youtube(src: dict, ctx: dict) -> list[Item]:
    cid = _resolve_youtube(src, ctx)
    if not cid:
        return []
    txt = get_text("https://www.youtube.com/feeds/videos.xml", params={"channel_id": cid})
    if not txt:
        return []
    out = []
    for e in feedparser.parse(txt).entries[:15]:
        link = e.get("link", "")
        title = clean(e.get("title"), 200)
        if "/shorts/" in link or "#shorts" in title.lower():
            continue
        views = 0
        try:
            views = int(e.get("media_statistics", {}).get("views", 0))
        except (TypeError, ValueError):
            pass
        out.append(Item(
            title=title, url=link, published=_dt(e.get("published_parsed")),
            summary=clean(e.get("summary") or e.get("media_description"), 250),
            popularity=views / 1000, kind="video",
            extra={"thumb": (e.get("media_thumbnail") or [{}])[0].get("url")},
            **_base(src),
        ))
    return out


# ── Podcasts (iTunes directory → RSS) ────────────────────────────────────────
def _resolve_podcast(src: dict, ctx: dict) -> str | None:
    if src.get("url"):
        return src["url"]
    cache = ctx["state"].setdefault("resolved", {})
    key = f"pod:{src['name']}"
    if key in cache:
        return cache[key]
    data = get_json("https://itunes.apple.com/search", params={
        "term": src["name"], "media": "podcast", "entity": "podcast", "limit": 8})
    best, best_score = None, 0.0
    for r in (data or {}).get("results", []):
        if not r.get("feedUrl"):
            continue
        s = SequenceMatcher(None, src["name"].lower(), r.get("collectionName", "").lower()).ratio()
        if s > best_score:
            best, best_score = r["feedUrl"], s
    if best and best_score > 0.45:
        cache[key] = best
        return best
    log.warning("could not resolve podcast %r — add url: to config", src["name"])
    return None


def fetch_podcast(src: dict, ctx: dict) -> list[Item]:
    feed_url = _resolve_podcast(src, ctx)
    if not feed_url:
        return []
    txt = get_text(feed_url, timeout=30)
    if not txt:
        return []
    out = []
    for e in feedparser.parse(txt).entries[:6]:
        dur = _duration_min(e.get("itunes_duration"))
        if src.get("max_minutes") and dur and dur > src["max_minutes"]:
            continue
        audio = next((l.get("href") for l in e.get("links", []) if l.get("rel") == "enclosure"), None)
        out.append(Item(
            title=clean(e.get("title"), 200), url=e.get("link") or audio or feed_url,
            published=_dt(e.get("published_parsed")), summary=clean(e.get("summary"), 300),
            duration_min=dur, kind="podcast", extra={"audio": audio}, **_base(src),
        ))
    return out


# ── Luma event calendars (best effort: parses the page's embedded JSON) ─────
def _walk(o):
    if isinstance(o, dict):
        yield o
        for v in o.values():
            yield from _walk(v)
    elif isinstance(o, list):
        for v in o:
            yield from _walk(v)


def fetch_luma(src: dict, ctx: dict) -> list[Item]:
    page = get_text(src["url"])
    m = page and re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', page, re.S)
    if not m:
        return []
    try:
        data = json.loads(m.group(1))
    except ValueError:
        return []
    out, seen = [], set()
    for d in _walk(data):
        if not (isinstance(d.get("name"), str) and d.get("start_at") and d.get("url")):
            continue
        start = _iso(d["start_at"])
        if not start or start < ctx["now"] or start > ctx["now"] + timedelta(days=45) or d["url"] in seen:
            continue
        seen.add(d["url"])
        url = d["url"] if d["url"].startswith("http") else f"https://luma.com/{d['url']}"
        out.append(Item(title=clean(d["name"], 200), url=url, published=ctx["now"], kind="event",
                        extra={"start": start}, **_base(src)))
    return sorted(out, key=lambda i: i.extra["start"])


FETCHERS = {
    "rss": fetch_rss, "hn": fetch_hn, "hf_papers": fetch_hf_papers, "arxiv": fetch_arxiv,
    "youtube": fetch_youtube, "podcast": fetch_podcast, "luma": fetch_luma,
}


def fetch_all(sources: list[dict], cfg: dict, state: dict, now: datetime) -> list[Item]:
    def run(src):
        lookback = cfg.get("weekly_lookback_hours", 192) if src.get("cadence") == "weekly" \
            else cfg.get("lookback_hours", 36)
        ctx = {"now": now, "lookback": lookback, "state": state}
        fn = FETCHERS.get(src.get("kind"))
        if not fn:
            log.warning("unknown source kind %r for %s", src.get("kind"), src.get("name"))
            return []
        try:
            items = fn(src, ctx)
        except Exception as e:  # one bad source never kills the brief
            log.warning("source %s crashed: %s", src.get("name"), e)
            return []
        cutoff = now - timedelta(hours=lookback)
        kept = [i for i in items if i.kind == "event" or (i.published or now) >= cutoff]
        log.info("%-40s %3d items (%d fresh)", src["name"][:40], len(items), len(kept))
        return kept

    # arXiv must be sequential (rate limit); everything else in parallel.
    arxiv = [s for s in sources if s.get("kind") == "arxiv"]
    rest = [s for s in sources if s.get("kind") != "arxiv"]
    out: list[Item] = []
    with ThreadPoolExecutor(max_workers=12) as pool:
        futs = [pool.submit(run, s) for s in rest]
        for s in arxiv:
            out.extend(run(s))
        for f in futs:
            out.extend(f.result())
    return out
