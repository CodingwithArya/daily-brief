"""Turn papers (alone or bundled by topic) into NotebookLM Audio Overviews and publish them as a private podcast feed.

Uses the unofficial `notebooklm-py` library (cookie auth from `notebooklm login`). It drives your own
NotebookLM account, so run it on your laptop (cron/launchd), not on a shared CI runner.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import shutil
import subprocess
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path
from xml.sax.saxutils import escape

log = logging.getLogger(__name__)


def slug(s: str, n: int = 60) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:n].rstrip("-")


# ── prompts ──────────────────────────────────────────────────────────────────
def _instructions(job: dict, nb: dict) -> str:
    base = f"{nb.get('listener_profile', '').strip()} {nb.get('prompt', '').strip()}"
    srcs = job["sources"]
    if job["kind"] == "classic" and len(srcs) > 1:
        extra = (f"The first source is a classic paper ({srcs[0]['title']}); the others are brand-new work on "
                 f"the same topic ({job.get('topic')}). Structure it as THEN & NOW: explain the classic "
                 "thoroughly first, then show how the new work builds on, challenges, or replaces its ideas.")
    elif job["kind"] == "classic":
        extra = ("This is a classic paper. Situate it historically, then explain how its ideas live on in "
                 "today's systems and what modern practice changed.")
    elif len(srcs) > 1:
        extra = (f"These {len(srcs)} sources are all recent work on {job.get('topic')}. Don't summarize them one "
                 "by one — synthesize: what problem they share, how their approaches differ, where they agree "
                 "or contradict each other, which result matters most and why, and what this says about where "
                 "the field is heading. Name each paper when you discuss it.")
        if job.get("topic", "").startswith(("Hearables", "Wireless")):
            extra += (" This is the listener's own research area: be technical about power, latency and "
                      "hardware constraints, and suggest a follow-up project idea.")
    else:
        extra = ("This is a brand-new paper. Focus on what is genuinely novel versus prior work, whether "
                 "the evidence supports the claims, and what it means for practitioners.")
    return f"{base} {extra}".strip()


# ── generation (local, needs `notebooklm login` once) ───────────────────────
async def _generate(jobs: list[dict], cfg: dict, out_dir: Path) -> dict[str, dict]:
    from notebooklm import AudioFormat, AudioLength, NotebookLMClient

    nb_cfg = cfg.get("notebooklm") or {}
    fmt = getattr(AudioFormat, nb_cfg.get("format", "deep-dive").upper().replace("-", "_"))
    results: dict[str, dict] = {}

    async with NotebookLMClient.from_storage() as client:
        started = []
        for job in jobs:
            try:
                nb = await client.notebooks.create(f"🎧 {job['title'][:90]}")
                source_ids = []
                for src in job["sources"]:
                    try:
                        s = await client.sources.add_url(nb.id, src["url"])
                        await client.sources.wait_until_ready(nb.id, s.id, timeout=600)
                        source_ids.append(s.id)
                    except Exception as e:      # one dead link shouldn't sink the episode
                        log.warning("  skipped source %s: %s", src["url"], e)
                if not source_ids:
                    raise RuntimeError("no sources could be added")
                length_name = nb_cfg.get("length", "default")
                if len(source_ids) >= 3 and length_name == "default":
                    length_name = "long"
                task = await client.artifacts.generate_audio(
                    nb.id, source_ids=source_ids, instructions=_instructions(job, nb_cfg),
                    audio_format=fmt, audio_length=getattr(AudioLength, length_name.upper()))
                started.append((job, nb.id, task.task_id))
                log.info("generating (%d sources): %s", len(source_ids), job["title"])
            except Exception as e:
                log.error("could not start %s: %s", job["title"], e)

        for job, nb_id, task_id in started:
            try:
                final = await client.artifacts.wait_for_completion(nb_id, task_id, timeout=1800)
                if not final.is_complete:
                    raise RuntimeError(f"{final.status}: {final.error}")
                fname = f"{job['date']}-{slug(job['title'])}.m4a"
                await client.artifacts.download_audio(nb_id, str(out_dir / fname), artifact_id=task_id)
                results[job["key"]] = {**job, "file": fname, "notebook_id": nb_id,
                                       "notebook_url": f"https://notebooklm.google.com/notebook/{nb_id}",
                                       "created": datetime.now(timezone.utc).isoformat()}
                log.info("✓ %s → %s", job["title"], fname)
                if not nb_cfg.get("keep_notebooks", True):
                    await client.notebooks.delete(nb_id)
            except Exception as e:
                log.error("generation failed for %s: %s", job["title"], e)
    return results


def _publish_github_release(path: Path, tag: str) -> str | None:
    """Upload to a GitHub Release (keeps big audio out of git). Returns the download URL."""
    if not shutil.which("gh"):
        log.warning("gh CLI not found; skipping release upload")
        return None
    if subprocess.run(["gh", "release", "view", tag], capture_output=True).returncode != 0:
        subprocess.run(["gh", "release", "create", tag, "--title", f"Paper podcasts {tag}",
                        "--notes", "Auto-generated NotebookLM audio overviews"], check=False)
    r = subprocess.run(["gh", "release", "upload", tag, str(path), "--clobber"], capture_output=True, text=True)
    if r.returncode:
        log.warning("upload failed: %s", r.stderr.strip())
        return None
    repo = subprocess.run(["gh", "repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner"],
                          capture_output=True, text=True).stdout.strip()
    return f"https://github.com/{repo}/releases/download/{tag}/{path.name}" if repo else None


def run_jobs(jobs: list[dict], cfg: dict, site_dir: Path, storage: str = "local") -> dict:
    """Generate any jobs not yet done; update site/podcasts/index.json and feed.xml."""
    pod_dir = site_dir / "podcasts"
    audio_dir = Path(cfg["notebooklm"].get("output_dir", "podcasts"))
    pod_dir.mkdir(parents=True, exist_ok=True)
    audio_dir.mkdir(parents=True, exist_ok=True)
    index_file = pod_dir / "index.json"
    index = json.loads(index_file.read_text()) if index_file.exists() else {}

    todo = [j for j in jobs if j["key"] not in index]
    if not todo:
        log.info("no new paper podcasts to make")
        return index
    done = asyncio.run(_generate(todo, cfg, audio_dir))
    for key, rec in done.items():
        local = audio_dir / rec["file"]
        url = None
        if storage == "github_release":
            url = _publish_github_release(local, "podcasts-" + rec["date"][:7])
        rec["audio_url"] = url or local.resolve().as_uri()
        index[key] = rec
    index_file.write_text(json.dumps(index, indent=1))
    write_feed(index, cfg, pod_dir / "feed.xml", audio_dir)
    return index


def write_feed(index: dict, cfg: dict, path: Path, audio_dir: Path) -> None:
    nb = cfg.get("notebooklm") or {}
    base = (nb.get("public_base_url") or "").rstrip("/")
    items = []
    for rec in sorted(index.values(), key=lambda r: r.get("created", ""), reverse=True)[:200]:
        f = audio_dir / rec["file"]
        size = f.stat().st_size if f.exists() else 0
        when = datetime.fromisoformat(rec["created"])
        label = {"classic": "Classic", "topic": "Topic", "single": "New paper"}.get(rec["kind"], "Paper")
        srcs = rec.get("sources") or [{"title": rec["title"], "page": rec.get("url", "")}]
        desc = "Sources: " + " | ".join(f"{x['title']} ({x.get('page') or x.get('url')})" for x in srcs) \
            + f" · Chat with them: {rec['notebook_url']}"
        link = srcs[0].get("page") or srcs[0].get("url") or rec["notebook_url"]
        items.append(f"""  <item>
    <title>{escape(f"[{label}] {rec['title']}")}</title>
    <description>{escape(desc)}</description>
    <link>{escape(link)}</link>
    <guid isPermaLink="false">{escape(rec['key'])}</guid>
    <pubDate>{format_datetime(when)}</pubDate>
    <enclosure url="{escape(rec['audio_url'])}" length="{size}" type="audio/mp4"/>
  </item>""")
    path.write_text(f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">
<channel>
  <title>Paper Podcasts (Daily Brief)</title>
  <link>{escape(base or 'https://notebooklm.google.com')}</link>
  <description>NotebookLM audio overviews of classic and brand-new papers, generated daily.</description>
  <language>en-us</language>
  <itunes:block>Yes</itunes:block>
{chr(10).join(items)}
</channel>
</rss>
""")
