#!/usr/bin/env python3
"""daily-brief — build today's brief and (optionally) turn papers into NotebookLM podcasts.

  python run.py digest                 # fetch → rank → site/YYYY-MM-DD.html (+ index.html, .md)
  python run.py podcasts               # NotebookLM audio for today's planned papers (run locally)
  python run.py all                    # both
  python run.py digest --date 2026-10-19 --no-llm
"""
from __future__ import annotations

import argparse
import json
import logging
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

from brief import episodes, podcastify, rank, render, state as st
from brief.notify import send_email
from brief.sources import fetch_all
from brief.summarize import annotate

log = logging.getLogger("brief")


def load_cfg(path: str) -> dict:
    return yaml.safe_load(Path(path).read_text())


def _now(cfg: dict, date: str | None) -> datetime:
    if not date:
        return datetime.now(timezone.utc)
    tz = ZoneInfo(cfg.get("timezone", "UTC"))
    return datetime.fromisoformat(date).replace(hour=6, tzinfo=tz).astimezone(timezone.utc)


def cmd_digest(cfg: dict, args) -> str:
    now = _now(cfg, args.date)
    date = now.astimezone(ZoneInfo(cfg.get("timezone", "UTC"))).strftime("%Y-%m-%d")
    site = Path(cfg.get("output_dir", "site"))
    (site / "data").mkdir(parents=True, exist_ok=True)

    state = st.load()
    seen = {k: v for k, v in st.prune_seen(state, now).items() if v != date}  # re-runs today are idempotent

    items = fetch_all(cfg["sources"], cfg, state, now)
    log.info("fetched %d fresh items from %d sources", len(items), len(cfg["sources"]))
    rank.score_items(items, cfg, now)
    chosen = rank.select(items, cfg, seen, now)
    curriculum = rank.curriculum_for_today(cfg, state, now)
    if args.no_llm:
        cfg.setdefault("llm", {})["enabled"] = False
    extras = annotate(chosen, cfg)
    episodes.update_pool(items, cfg, state, date, now)
    jobs = episodes.plan_episodes(curriculum, cfg, state, date, now)

    ctx = render.build_context(chosen, cfg, extras, curriculum, now, jobs)
    pod_index = site / "podcasts" / "index.json"
    if pod_index.exists():
        render.apply_podcasts(ctx, json.loads(pod_index.read_text()))
    (site / "data" / f"{date}.json").write_text(json.dumps({"ctx": ctx, "jobs": jobs}, indent=1, default=str))
    _write_pages(site, date, ctx)

    st.mark_seen(state, [it.id for its in chosen.values() for it in its], date)
    st.save(state)
    log.info("brief → %s/%s.html  (%d items, %d podcast jobs planned)", site, date, ctx["n_items"], len(jobs))

    if not args.no_email:
        send_email(cfg, f"Daily Brief · {ctx['date_long']}", render.render_html(ctx), render.render_markdown(ctx))
    return date


def _write_pages(site: Path, date: str, ctx: dict) -> None:
    html = render.render_html(ctx)
    (site / f"{date}.html").write_text(html)
    (site / f"{date}.md").write_text(render.render_markdown(ctx))
    newest = max((p.stem for p in site.glob("20??-??-??.html")), default=date)
    if date >= newest:
        shutil.copy(site / f"{date}.html", site / "index.html")
    render.write_archive(site)


def cmd_podcasts(cfg: dict, args) -> None:
    if not (cfg.get("notebooklm") or {}).get("enabled"):
        log.info("notebooklm disabled in config")
        return
    site = Path(cfg.get("output_dir", "site"))
    tz = ZoneInfo(cfg.get("timezone", "UTC"))
    today = _now(cfg, args.date).astimezone(tz).date()
    # today's jobs plus anything missed in the last 3 days (laptop asleep, auth expired, ...)
    days = [(today - timedelta(days=d)).isoformat() for d in range(args.catchup_days + 1)]
    data = {d: json.loads((site / "data" / f"{d}.json").read_text())
            for d in days if (site / "data" / f"{d}.json").exists()}
    jobs = [j for d in days if d in data for j in data[d]["jobs"]]
    if not jobs:
        log.info("no paper jobs planned for %s (run `digest` first)", days[0])
        return
    index = podcastify.run_jobs(jobs, cfg, site, storage=args.storage)
    for d, blob in data.items():   # re-render those days so the brief links the new audio
        _write_pages(site, d, render.apply_podcasts(blob["ctx"], index))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["digest", "podcasts", "all"])
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--date", help="YYYY-MM-DD (default: today)")
    ap.add_argument("--no-llm", action="store_true", help="skip Claude annotations")
    ap.add_argument("--no-email", action="store_true")
    ap.add_argument("--storage", choices=["local", "github_release"], default="local",
                    help="where podcast audio lives (github_release needs the gh CLI)")
    ap.add_argument("--catchup-days", type=int, default=3)
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S")
    cfg = load_cfg(args.config)
    if args.command in ("digest", "all"):
        cmd_digest(cfg, args)
    if args.command in ("podcasts", "all"):
        cmd_podcasts(cfg, args)


if __name__ == "__main__":
    main()
