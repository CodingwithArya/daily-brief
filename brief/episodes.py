"""Plan NotebookLM episodes: classic-paper "then & now" episodes plus topic bundles of fresh sources.

State it keeps (in state.json):
  pod_pool     {item_id: candidate}   rolling window of things worth podcasting
  podcasted    {item_id: date}        never podcast the same source twice
  topic_log    {topic: date}          last time each topic got an episode (for cooldown)
  episode_log  {date: [episodes]}     makes re-running the same day idempotent
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from difflib import SequenceMatcher

from .models import Item
from .rank import compile_interests

POOL_SECTIONS = {"lab", "papers_hot", "ai", "swe", "tech", "watch", "pool"}


def _slug(s: str, n: int = 50) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:n].rstrip("-")


def _topic_matchers(nb: dict):
    return [(t["name"], float(t.get("boost", 1.0)), compile_interests({k: 1 for k in t.get("keywords", [])}))
            for t in nb.get("topics", [])]


def best_topic(title: str, summary: str, matchers) -> tuple[str | None, float]:
    text = f"{title} {title} {summary}".lower()
    best, best_hits = None, 0.0
    for name, _boost, pats in matchers:
        hits = sum(min(len(p.findall(text)), 2) for p, _ in pats)
        if hits > best_hits:
            best, best_hits = name, hits
    return best, best_hits


def update_pool(items: list[Item], cfg: dict, state: dict, date: str, now: datetime) -> dict:
    nb = cfg.get("notebooklm") or {}
    matchers = _topic_matchers(nb)
    pool = state.setdefault("pod_pool", {})
    done = state.setdefault("podcasted", {})
    cutoff = (now - timedelta(days=nb.get("pool_days", 7))).strftime("%Y-%m-%d")

    for k in [k for k, v in pool.items() if v["added"] < cutoff or k in done]:
        del pool[k]
    for it in items:
        if it.section not in POOL_SECTIONS or it.id in done or it.kind in ("podcast", "event"):
            continue
        topic, hits = best_topic(it.title, it.summary, matchers)
        if not topic:
            continue
        if it.kind != "paper" and hits < 2:      # posts/videos must be clearly on-topic
            continue
        # the same arXiv paper can arrive via HF Daily Papers and several arXiv queries → one candidate
        key = f"arxiv:{it.extra['arxiv_id']}" if it.extra.get("arxiv_id") else it.id
        if key in done:
            continue
        prev = pool.get(key, {})
        pool[key] = {
            "id": key, "item_ids": sorted(set(prev.get("item_ids", [])) | {it.id}), "title": it.title, "kind": it.kind, "section": it.section, "source": it.source,
            "url": it.extra.get("pdf") or it.url, "page": it.url, "topic": topic, "hits": hits,
            "score": max(it.score, prev.get("score", 0)), "added": prev.get("added", date),
        }
    return pool


def _pick_bundle(cands: list[dict], nb: dict) -> list[dict]:
    max_src = nb.get("max_sources_per_episode", 4)
    max_non = nb.get("max_non_papers_per_episode", 2)
    out, non = [], 0
    # papers first within equal-ish scores, then by score
    for c in sorted(cands, key=lambda c: (-(c["score"] + (0.3 if c["kind"] == "paper" else 0)))):
        if len(out) >= max_src:
            break
        if any(SequenceMatcher(None, c["title"].lower(), o["title"].lower()).ratio() > 0.85 for o in out):
            continue
        if c["kind"] != "paper":
            if non >= max_non:
                continue
            non += 1
        out.append(c)
    return out


def plan_episodes(curriculum: dict, cfg: dict, state: dict, date: str, now: datetime) -> list[dict]:
    nb = cfg.get("notebooklm") or {}
    if not nb.get("enabled"):
        return []
    log = state.setdefault("episode_log", {})
    if date in log:                                    # same-day re-run → same episodes
        return log[date]

    pool = state.setdefault("pod_pool", {})
    boosts = {t["name"]: float(t.get("boost", 1.0)) for t in nb.get("topics", [])}
    topic_log = state.setdefault("topic_log", {})
    cooldown = (now - timedelta(days=nb.get("topic_cooldown_days", 3))).strftime("%Y-%m-%d")
    n_eps = nb.get("episodes_per_day", 2)
    used: set[str] = set()
    episodes: list[dict] = []

    def free(topic=None):
        return [c for c in pool.values() if c["id"] not in used and (topic is None or c["topic"] == topic)]

    if not pool and not curriculum.get("classic"):
        return []

    # 1) classic paper of the day, plus up to 2 fresh items on the same topic
    if curriculum.get("classic"):
        c = curriculum["classic"]
        related = _pick_bundle(free(c.get("topic")), {**nb, "max_sources_per_episode": 2}) if c.get("topic") else []
        used.update(r["id"] for r in related)
        classic_src = {"id": f"classic:{c['url']}", "title": c["title"], "kind": "paper", "url": c["url"],
                       "page": c["url"], "source": f"Classic · {c.get('track', '')}"}
        episodes.append({
            "kind": "classic", "topic": c.get("topic"),
            "title": c["title"] + (" — then & now" if related else ""),
            "sources": [classic_src] + related,
        })

    # 2) topic bundles from the rolling pool
    while len(episodes) < n_eps:
        groups: dict[str, list[dict]] = {}
        for cand in free():
            groups.setdefault(cand["topic"], []).append(cand)
        best, best_val = None, 0.0
        for allow_recent in (False, True):        # prefer topics not covered in the last few days
            for topic, cands in groups.items():
                recent = topic_log.get(topic, "") >= cooldown or any(e.get("topic") == topic for e in episodes)
                if recent and not allow_recent:
                    continue
                bundle = _pick_bundle(cands, nb)
                if len(bundle) < nb.get("min_sources_per_topic", 2) or not any(b["kind"] == "paper" for b in bundle):
                    continue
                val = boosts.get(topic, 1.0) * sum(b["score"] for b in bundle) * (1 + 0.15 * (len(bundle) - 1))
                if val > best_val:
                    best, best_val = (topic, bundle), val
            if best:
                break
        if best:
            topic, bundle = best
            n_p = sum(b["kind"] == "paper" for b in bundle)
            what = f"{n_p} new paper{'s' if n_p > 1 else ''}" + (f" + {len(bundle) - n_p} more" if len(bundle) > n_p else "")
            episodes.append({"kind": "topic", "topic": topic, "title": f"{topic}: {what}", "sources": bundle})
            used.update(b["id"] for b in bundle)
            continue
        # 3) fallback: single strongest paper still in the pool
        papers = sorted((c for c in free() if c["kind"] == "paper"), key=lambda c: -c["score"])
        if not papers:
            break
        p = papers[0]
        episodes.append({"kind": "single", "topic": p["topic"], "title": p["title"], "sources": [p]})
        used.add(p["id"])

    done = state.setdefault("podcasted", {})
    for ep in episodes:
        ep["date"] = date
        ep["key"] = f"ep:{date}:{_slug(ep['title'])}"
        ep["item_ids"] = [i for s in ep["sources"] for i in s.get("item_ids", [s["id"]])]
        if ep.get("topic") and ep["kind"] != "classic":
            topic_log[ep["topic"]] = date
        for s in ep["sources"]:
            done[s["id"]] = date
            pool.pop(s["id"], None)
    log[date] = episodes
    for d in [d for d in log if d < (now - timedelta(days=30)).strftime("%Y-%m-%d")]:
        del log[d]
    return episodes
