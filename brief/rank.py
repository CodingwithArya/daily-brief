"""Score items by (source weight × interest match × recency) + popularity, then fill sections."""
from __future__ import annotations

import math
import re
from collections import defaultdict
from datetime import datetime
from difflib import SequenceMatcher

from .models import Item

WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def compile_interests(interests: dict[str, float]):
    return [(re.compile(r"(?<![a-z0-9])" + re.escape(k.lower()) + r"s?(?![a-z0-9])"), float(w))
            for k, w in (interests or {}).items()]


def interest_score(item: Item, patterns) -> tuple[float, list[str]]:
    text = f"{item.title} {item.title} {item.summary}".lower()   # title counts double
    total, hits = 0.0, []
    for pat, w in patterns:
        n = len(pat.findall(text))
        if n:
            total += w * min(n, 2)
            hits.append(pat.pattern)
    return total, hits


def score_items(items: list[Item], cfg: dict, now: datetime) -> None:
    patterns = compile_interests(cfg.get("interests", {}))
    weekly = {s["name"] for s in cfg["sources"] if s.get("cadence") == "weekly"}
    # popularity normalised per source so HN points and HF upvotes are comparable
    max_pop = defaultdict(float)
    for it in items:
        max_pop[it.source] = max(max_pop[it.source], it.popularity)
    for it in items:
        interest, _ = interest_score(it, patterns)
        interest_factor = min(3.0, max(0.25, 1 + 0.2 * interest))
        half_life = 96 if it.source in weekly else 20
        recency = 0.35 + 0.65 * math.exp(-it.age_hours(now) / half_life)
        pop = math.log1p(it.popularity) / math.log1p(max_pop[it.source]) if max_pop[it.source] else 0
        it.score = round(it.weight * interest_factor * recency + 0.6 * pop, 4)


def _similar(a: str, b: str) -> bool:
    return SequenceMatcher(None, a.lower(), b.lower()).ratio() > 0.82


def select(items: list[Item], cfg: dict, seen: dict, now: datetime) -> dict[str, list[Item]]:
    """Pick the top items per section with per-source caps, cross-day dedupe and near-dup removal."""
    today = WEEKDAYS[now.weekday()]
    by_section: dict[str, list[Item]] = defaultdict(list)
    for it in items:
        if it.id in seen:
            continue
        by_section[it.section].append(it)
        for extra in it.also_sections:
            by_section[extra].append(it)

    chosen: dict[str, list[Item]] = {}
    used_titles: list[str] = []
    for sec in cfg["sections"]:
        if sec.get("weekdays") and today not in sec["weekdays"]:
            continue
        sid = sec["id"]
        per_source_cap = 1 if sid in ("listen", "watch") else 2
        counts: dict[str, int] = defaultdict(int)
        picks: list[Item] = []
        pool = by_section.get(sid, [])
        pool = pool if sid == "events" else sorted(pool, key=lambda i: -i.score)
        for it in pool:
            if len(picks) >= sec.get("max", 5):
                break
            if counts[it.source] >= per_source_cap and sid != "events":
                continue
            if sid != "newsletters" and any(_similar(it.title, t) for t in used_titles):
                continue
            picks.append(it)
            counts[it.source] += 1
            used_titles.append(it.title)
        if picks:
            chosen[sid] = picks
    return chosen


def curriculum_for_today(cfg: dict, state: dict, now: datetime) -> dict:
    """Returns {'classic': {...}|None, 'series': {...}|None}. Advances pointers in `state`."""
    cur = cfg.get("curriculum") or {}
    today = WEEKDAYS[now.weekday()]
    date = now.strftime("%Y-%m-%d")
    out = {"classic": None, "series": None}

    papers = cur.get("classic_papers") or []
    if papers and today in cur.get("classic_papers_weekdays", []):
        log = state.setdefault("classic_log", {})
        if date in log:                       # re-running the same day is idempotent
            idx = log[date]
        else:
            idx = state.get("classic_idx", 0)
            if idx < len(papers):
                log[date] = idx
                state["classic_idx"] = idx + 1
        if idx < len(papers):
            out["classic"] = {**papers[idx], "n": idx + 1, "total": len(papers)}

    series = cur.get("series") or {}
    vids = series.get("videos") or []
    if vids and today == cur.get("series_weekday"):
        log = state.setdefault("series_log", {})
        if date in log:
            idx = log[date]
        else:
            idx = state.get("series_idx", 0)
            if idx < len(vids):
                log[date] = idx
                state["series_idx"] = idx + 1
        if idx < len(vids):
            out["series"] = {**vids[idx], "series": series.get("name"), "n": idx + 1, "total": len(vids)}
    return out
