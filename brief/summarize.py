"""Optional: ask Claude for one-line 'why it matters' notes + opinion prompts.

Enabled when ANTHROPIC_API_KEY is set and config llm.enabled is true. Any failure → brief still renders.
"""
from __future__ import annotations

import json
import logging
import os
import re

import requests

from .models import Item

log = logging.getLogger(__name__)

SYSTEM = """You write the annotations for a personal daily brief. The reader: a UW CS senior about to
start as a new-grad SWE, works in the Mobile Intelligence Lab (hearables, acoustic/wireless sensing,
on-device AI), cares about AI/ML, distributed systems and being a well-informed, articulate person.

For each item, write `why`: ONE sentence (≤ 28 words) on why it matters or the single most interesting
takeaway — concrete, no hype, no restating the title. If the item is thin, say what to look for.

Then write opinion prompts: debatable questions raised by today's items that a thoughtful person
should be able to argue. For each give the question, 1-sentence context, and the strongest
one-line case for two opposing sides. Be balanced on political topics; never tell the reader what
to think.

Return ONLY JSON: {"tldr": ["3 bullets, ≤20 words each, the day in a nutshell"],
"why": {"<id>": "..."}, "opinions": [{"q": "...", "context": "...", "for": "...", "against": "..."}]}"""


def annotate(chosen: dict[str, list[Item]], cfg: dict) -> dict:
    llm = cfg.get("llm") or {}
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not (llm.get("enabled") and key):
        return {}
    # prioritise news-ish sections; keep the request small
    order = ["world", "tech", "ai", "papers_hot", "lab", "swe", "newsletters"]
    items = [it for sid in order for it in chosen.get(sid, [])][: llm.get("max_items", 14)]
    if not items:
        return {}
    payload = [{"id": it.id, "section": it.section, "source": it.source,
                "title": it.title, "summary": it.summary[:300]} for it in items]
    user = (f"Write {llm.get('opinion_prompts', 3)} opinion prompts.\n\nItems:\n"
            + json.dumps(payload, ensure_ascii=False, indent=0))
    try:
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
            json={"model": llm.get("model", "claude-sonnet-5-5"), "max_tokens": 3000, "system": SYSTEM,
                  "messages": [{"role": "user", "content": user}]},
            timeout=120,
        )
        r.raise_for_status()
        text = "".join(b.get("text", "") for b in r.json().get("content", []))
        m = re.search(r"\{.*\}", text, re.S)
        data = json.loads(m.group(0)) if m else {}
    except Exception as e:
        log.warning("LLM annotation skipped: %s", e)
        return {}
    for it in items:
        it.why = (data.get("why") or {}).get(it.id, "")
    return {"tldr": data.get("tldr") or [], "opinions": data.get("opinions") or []}
