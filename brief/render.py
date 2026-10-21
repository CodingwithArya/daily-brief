from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .models import Item

ROOT = Path(__file__).resolve().parent.parent
env = Environment(loader=FileSystemLoader(ROOT / "templates"), autoescape=select_autoescape(["html", "j2"]))


def _ago(it: Item, now: datetime) -> str:
    h = it.age_hours(now)
    if h < 1:
        return "just now"
    if h < 36:
        return f"{int(h)}h ago"
    return f"{int(h // 24)}d ago"


def build_context(chosen, cfg, extras, curriculum, now: datetime, episodes: list | None = None) -> dict:
    tz = ZoneInfo(cfg.get("timezone", "UTC"))
    local = now.astimezone(tz)
    sections, n_items, read_min, listen_min = [], 0, 0, 0
    titles = {s["id"]: s["title"] for s in cfg["sections"]}
    for sec in cfg["sections"]:
        items = chosen.get(sec["id"])
        if not items:
            continue
        for it in items:
            it.extra["ago"] = _ago(it, now)
            if it.kind == "event" and it.extra.get("start"):
                it.extra["start_local"] = it.extra["start"].astimezone(tz).strftime("%a %b %-d, %-I:%M %p")
            n_items += 1
            if it.kind in ("podcast", "video"):
                listen_min += it.duration_min or 15
            else:
                read_min += 3
        sections.append({"id": sec["id"], "title": titles[sec["id"]],
                         "items": [dict(vars(i), ago=i.extra["ago"], id=i.id) for i in items]})

    queue = []
    labels = {"classic": "Classic paper", "topic": "Topic episode", "single": "New paper"}
    eps = []
    for ep in episodes or []:
        c = curriculum.get("classic") if ep["kind"] == "classic" else None
        label = labels[ep["kind"]] + (f" {c['n']}/{c['total']}" if c else "") + (f" · {ep['topic']}" if ep.get("topic") else "")
        eps.append({"key": ep["key"], "label": label, "title": ep["title"], "url": None,
                    "sources": [{"title": x["title"], "url": x.get("page") or x["url"], "kind": x["kind"]}
                                for x in ep["sources"]],
                    "note": "Audio appears here after the NotebookLM run"})
    if curriculum.get("series"):
        s = curriculum["series"]
        queue.append({"label": f"{s['series']} · {s['n']}/{s['total']} (Saturday evening)",
                      "title": s["title"], "url": s["url"], "note": "Code along — don't just watch."})

    return dict(
        date_long=local.strftime("%A, %B %-d, %Y"), weekday=local.strftime("%A"),
        sections=sections, queue=queue, episodes=eps, tldr=extras.get("tldr", []), opinions=extras.get("opinions", []),
        n_items=n_items, read_min=read_min, listen_min=listen_min,
        generated=local.strftime("%-I:%M %p %Z"),
        feed_url=("podcasts/feed.xml" if (cfg.get("notebooklm") or {}).get("enabled") else None),
    )


def apply_podcasts(ctx: dict, index: dict) -> dict:
    """Attach generated NotebookLM audio (from podcasts/index.json) to a stored brief context."""
    def link(rec):
        return rec.get("audio_url") if (rec.get("audio_url") or "").startswith("http") else rec.get("notebook_url")
    by_item = {i: rec for rec in index.values() for i in rec.get("item_ids", [])}
    for sec in ctx["sections"]:
        for it in sec["items"]:
            if it.get("id") in by_item:
                it.setdefault("extra", {})["podcast"] = link(by_item[it["id"]])
    for ep in ctx.get("episodes", []):
        rec = index.get(ep["key"])
        if rec:
            ep["url"], ep["notebook"] = link(rec), rec.get("notebook_url")
            ep["note"] = "🎧 Ready — tap the title to listen"
    return ctx


def render_html(ctx: dict) -> str:
    return env.get_template("digest.html.j2").render(**ctx)


def render_markdown(ctx: dict) -> str:
    lines = [f"# Daily Brief — {ctx['date_long']}", ""]
    if ctx["tldr"]:
        lines += ["**The day in 30 seconds**"] + [f"- {b}" for b in ctx["tldr"]] + [""]
    if ctx.get("episodes"):
        lines.append("## 🎧 Today's podcast episodes")
        for ep in ctx["episodes"]:
            lines.append(f"- **{ep['label']}** — " + (f"[{ep['title']}]({ep['url']})" if ep.get("url") else ep["title"]))
            lines += [f"  - [{x['title']}]({x['url']})" for x in ep["sources"]]
        lines.append("")
    if ctx["queue"]:
        lines += ["## 🎯 Today's deliberate practice"] + [f"- **{q['label']}** — [{q['title']}]({q['url']})" for q in ctx["queue"]] + [""]
    for sec in ctx["sections"]:
        lines.append(f"## {sec['title']}")
        for it in sec["items"]:
            meta = it["source"] + (f" · {it['duration_min']} min" if it.get("duration_min") else "")
            lines.append(f"- [{it['title']}]({it['url']}) — _{meta}_")
            if it.get("why"):
                lines.append(f"  - {it['why']}")
        lines.append("")
    if ctx["opinions"]:
        lines.append("## 🗣️ Take a stance")
        for o in ctx["opinions"]:
            lines += [f"- **{o.get('q')}** {o.get('context', '')}",
                      f"  - For: {o.get('for', '')}", f"  - Against: {o.get('against', '')}"]
    return "\n".join(lines) + "\n"


def write_archive(out_dir: Path) -> None:
    """index.html = today's brief (written by run.py); archive.html lists every day."""
    pages = sorted((p.name for p in out_dir.glob("20??-??-??.html")), reverse=True)
    links = "\n".join(f'<li><a href="{p}">{p[:-5]}</a></li>' for p in pages)
    (out_dir / "archive.html").write_text(f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Brief Archive</title>
<style>body{{font:16px/1.6 Georgia,serif;max-width:640px;margin:40px auto;padding:0 16px;background:#f7f5f0;color:#1d1b18}}
@media(prefers-color-scheme:dark){{body{{background:#141311;color:#ece7dc}}a{{color:#7fb2e5}}}}</style></head>
<body><h1>Daily Brief archive</h1><p><a href="index.html">Today</a> · <a href="podcasts/feed.xml">Paper podcast RSS feed</a></p>
<ul>{links}</ul></body></html>""")
