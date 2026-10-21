"""Offline smoke test: fakes every network response, runs the full digest pipeline twice,
and checks ranking, dedupe, curriculum, podcast job planning, and feed writing.

    python -m tests.smoke_test
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

NOW = datetime(2026, 10, 19, 13, 0, tzinfo=timezone.utc)  # a Monday → classic paper day


def rfc(dt):
    return dt.strftime("%a, %d %b %Y %H:%M:%S +0000")


def rss(title_prefix, n=4, hours=(2, 8, 20, 60), extra=""):
    items = "".join(
        f"<item><title>{title_prefix} {i}</title><link>https://ex.com/{abs(hash(title_prefix))}/{i}</link>"
        f"<pubDate>{rfc(NOW - timedelta(hours=hours[i % len(hours)]))}</pubDate>"
        f"<description>Summary {i}. {extra}</description><itunes:duration>00:42:10</itunes:duration>"
        f"<enclosure url='https://cdn.ex.com/{i}.mp3' type='audio/mpeg' length='1'/></item>"
        for i in range(n))
    return (f"<?xml version='1.0'?><rss xmlns:itunes='http://www.itunes.com/dtds/podcast-1.0.dtd'>"
            f"<channel><title>x</title>{items}</channel></rss>")


TITLES = {
    "geekwire": ["UW spinout raises $40M for on-device AI hearing aids", "Seattle startup layoffs",
                 "Microsoft earnings preview", "Deal of the day: headphones"],
    "importai": ["Import AI 431: scaling laws for agents", "x", "y", "z"],
    "uber": ["How Uber built its feature store for real-time ML", "Raft at Uber scale: consensus lessons",
             "Uber postmortem: a latency incident", "Hiring at Uber"],
}


CLOCK = [NOW]


def fake_get_text(url, params=None, **kw):
    import requests
    full = requests.Request("GET", url, params=params).prepare().url
    NOW = CLOCK[0]
    day = NOW.strftime('%m%d')
    if "hn.algolia" in full:
        return json.dumps({"hits": [
            {"objectID": str(i), "title": t, "url": f"https://hn.ex/{i}", "points": p, "num_comments": 50,
             "created_at_i": int((NOW - timedelta(hours=h)).timestamp())}
            for i, (t, p, h) in enumerate([("Show HN: A Raft implementation in 500 lines of Rust", 640, 5),
                                           ("Postgres 19 released", 900, 10),
                                           ("Crypto exchange collapses again", 1200, 3)])]})
    if "huggingface.co/api/daily_papers" in full:
        return json.dumps([{"paper": {"id": f"2610.{day}9{i}", "title": t, "summary": "We show...",
                                      "upvotes": u, "authors": [{"name": "A. Author"}]},
                            "publishedAt": (NOW - timedelta(hours=10)).isoformat()}
                           for i, (t, u) in enumerate([(f"Test-time scaling for reasoning LLMs ({day})", 120), (f"RL with verifiers improves math reasoning ({day})", 90),
                                                        ("Quantization of on-device language models", 60),
                                                        ("A diffusion model for images", 30)])])
    if "export.arxiv.org" in full:
        if "Gollakota" in full or "hearable" in full:
            titles = ["Target speech hearing on wireless earbuds with on-device streaming AI",
                      "Binaural speech separation for hearing aids under 5 ms latency",
                      "Acoustic sensing of breathing with commodity earbuds"]
        elif "quantization" in full:
            titles = ["Speculative decoding for small language models on-device",
                      "KV cache quantization for mobile inference"]
        else:
            titles = ["Fault tolerance for LLM inference serving", "Raft replication with flexible quorums"]
        tag = abs(hash(full)) % 1000
        entries = "".join(
            f"<entry><id>http://arxiv.org/abs/2610.{day}{tag:03d}{i}v1</id><title>{t} ({day})</title>"
            f"<summary>We present a system. {t}.</summary>"
            f"<published>{(NOW - timedelta(hours=30)).isoformat().replace('+00:00', 'Z')}</published>"
            f"<author><name>Shyam Gollakota</name></author></entry>" for i, t in enumerate(titles))
        return f"<feed xmlns='http://www.w3.org/2005/Atom'>{entries}</feed>"
    if "itunes.apple.com" in full:
        term = dict(p.split("=") for p in full.split("?")[1].split("&"))["term"].replace("+", " ")
        from urllib.parse import unquote
        term = unquote(term)
        return json.dumps({"results": [{"collectionName": term, "feedUrl": f"https://pod.ex/{abs(hash(term))}"}]})
    if "pod.ex" in full:
        return rss(f"Episode {full[-6:]}: LLM inference at scale with an engineer", 3, hours=(12, 50, 100))
    if "youtube.com/@" in full:
        return '<html>"externalId":"UCabcdefghijklmnopqrstuv"</html>'
    if "youtube.com/feeds" in full:
        return ("<feed xmlns='http://www.w3.org/2005/Atom' xmlns:media='http://search.yahoo.com/mrss/'>"
                f"<entry><title>System design: distributed rate limiter ({full[-6:]})</title><link href='https://youtube.com/watch?v={abs(hash(full))}'/>"
                f"<published>{(NOW - timedelta(hours=20)).isoformat()}</published></entry>"
                f"<entry><title>#shorts quick tip</title><link href='https://youtube.com/shorts/x'/>"
                f"<published>{(NOW - timedelta(hours=2)).isoformat()}</published></entry></feed>")
    if "luma.com" in full:
        ev = {"props": {"events": [{"name": "AI House: On-device AI night", "url": "abc",
                                    "start_at": (NOW + timedelta(days=4)).isoformat()}]}}
        return f'<script id="__NEXT_DATA__" type="application/json">{json.dumps(ev)}</script>'
    for key, titles in TITLES.items():
        if key in full:
            items = "".join(f"<item><title>{t}</title><link>https://{key}.ex/{i}</link>"
                            f"<pubDate>{rfc(NOW - timedelta(hours=3 + i))}</pubDate></item>" for i, t in enumerate(titles))
            return f"<rss><channel>{items}</channel></rss>"
    return rss(f"Story from {full.split('/')[2]}", 4)


def main():
    work = Path(tempfile.mkdtemp())
    shutil.copy(ROOT / "config.yaml", work / "config.yaml")
    os.chdir(work)
    import run
    from brief import podcastify, render
    args = SimpleNamespace(date=None, no_llm=True, no_email=True)
    with mock.patch("brief.http.get_text", fake_get_text), mock.patch("brief.sources.get_text", fake_get_text), \
         mock.patch("run.datetime") as dt, mock.patch("brief.sources.time.sleep"):
        dt.now.return_value = NOW
        dt.fromisoformat = datetime.fromisoformat
        cfg = run.load_cfg("config.yaml")
        date = run.cmd_digest(cfg, args)
        blob = json.loads(Path(f"site/data/{date}.json").read_text())
        ctx, jobs = blob["ctx"], blob["jobs"]
        secs = {s["id"]: [i["title"] for i in s["items"]] for s in ctx["sections"]}
        for k, v in secs.items():
            print(f"[{k}]"); [print("   ", t) for t in v]
        def show(jobs):
            for j in jobs:
                print(f"  🎧 [{j['kind']}] {j['title']}")
                for x in j["sources"]:
                    print(f"       - {x['title']}  ({x['kind']}, {x.get('source', '')})")
        print("episodes:"); show(jobs)

        assert date == "2026-10-19"
        assert "lab" in secs and "earbuds" in secs["lab"][0].lower(), "lab paper should rank first"
        assert not any("shorts" in t for t in secs.get("watch", [])), "shorts must be filtered"
        assert not any("Deal of the day" in t for t in secs["tech"][:3]), "negative interest demotes"
        assert secs["tech"][0].startswith("UW spinout"), "interest-matched GeekWire story should lead tech"
        assert "events" in secs, "events show on Mondays"
        assert "newsletters" not in secs, "newsletter catch-up is Sunday-only"
        # Monday: classic #1 bundled with fresh same-topic items, plus a multi-source topic episode
        assert jobs[0]["kind"] == "classic" and jobs[0]["sources"][0]["title"].startswith("Hidden Technical Debt")
        assert len(jobs[0]["sources"]) > 1, "classic should be bundled with related new work"
        assert jobs[1]["kind"] == "topic" and len(jobs[1]["sources"]) >= 2
        assert jobs[1]["topic"].startswith("Hearables"), "lab topic should win (boost + 3 lab papers)"
        assert len(ctx["episodes"]) == 2

        # Re-run same day → identical episodes and items
        cfg = run.load_cfg("config.yaml")
        run.cmd_digest(cfg, args)
        blob2 = json.loads(Path(f"site/data/{date}.json").read_text())
        assert [j["key"] for j in blob2["jobs"]] == [j["key"] for j in jobs]
        assert sum(len(s["items"]) for s in blob2["ctx"]["sections"]) == ctx["n_items"]

        # Simulate 3 weeks: no source is ever podcasted twice; topics rotate; episodes keep
        # coming after all classics are used up.
        all_src, kinds, topics = {}, [], []
        for d in range(1, 21):
            CLOCK[0] = NOW + timedelta(days=d)
            dt.now.return_value = CLOCK[0]
            cfg = run.load_cfg("config.yaml")
            if d == 10:   # pretend the 15 classics are finished
                st = json.loads(Path("state.json").read_text()); st["classic_idx"] = 99
                Path("state.json").write_text(json.dumps(st))
            dd = run.cmd_digest(cfg, args)
            js = json.loads(Path(f"site/data/{dd}.json").read_text())["jobs"]
            if d in (3, 14):
                print(f"\n{dd} ({CLOCK[0].strftime('%a')}):"); show(js)
            for j in js:
                kinds.append((d, j["kind"])); topics.append(j["topic"])
                for x in j["sources"]:
                    assert x["id"] not in all_src, f"source podcasted twice: {x['title']}"
                    all_src[x["id"]] = dd
        assert all(n >= 1 for n in [sum(1 for d, k in kinds if d == day) for day in range(1, 21)]), "every day gets episodes"
        assert not any(k == "classic" for d, k in kinds if d >= 10), "no classics after the list is done"
        assert sum(1 for d, k in kinds if d >= 10) >= 20, "2 fresh episodes/day continue after classics"
        assert len(set(topics)) >= 4, f"topics should rotate, got {set(topics)}"
        print("\ntopics covered over 3 weeks:", sorted(set(topics)))

    # Fake NotebookLM output → feed + re-render links
    ep = jobs[1]
    index = {ep["key"]: {**ep, "file": "a.m4a", "notebook_id": "nb1", "created": NOW.isoformat(),
                         "notebook_url": "https://notebooklm.google.com/notebook/nb1",
                         "audio_url": "https://github.com/u/r/releases/download/podcasts-2026-10/a.m4a"}}
    Path("podcasts").mkdir(exist_ok=True)
    (Path("podcasts") / "a.m4a").write_bytes(b"0" * 1000)
    Path("site/podcasts").mkdir(exist_ok=True)
    podcastify.write_feed(index, cfg, Path("site/podcasts/feed.xml"), Path("podcasts"))
    feed = Path("site/podcasts/feed.xml").read_text()
    import xml.dom.minidom; xml.dom.minidom.parseString(feed)
    assert "[Topic] Hearables" in feed and 'length="1000"' in feed and "Sources:" in feed
    ctx_r = render.apply_podcasts(ctx, index)
    assert ctx_r["episodes"][1]["url"].endswith("a.m4a")
    lab_ids = {i["id"]: i for s in ctx_r["sections"] for i in s["items"] if s["id"] == "lab"}
    assert any(i.get("extra", {}).get("podcast") for i in lab_ids.values()), "lab items link to their episode"
    out = work / "site" / f"{date}.html"
    out.write_text(render.render_html(ctx_r))
    print("\nALL CHECKS PASSED →", out)
    return work


if __name__ == "__main__":
    main()
