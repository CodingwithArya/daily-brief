# daily-brief

A personal morning brief: pulls ~60 sources (news, AI/ML, systems blogs, arXiv, HF Daily Papers,
podcasts, YouTube, Seattle AI events), ranks them against **your** interests, drops anything you've
already seen, and renders one page you can read in ~15 minutes. Twice a week it also turns a paper into
a **NotebookLM podcast** (your 14-paper interview list, in your order) plus the best fresh paper from your
lab's area, and publishes them as a private podcast feed you can subscribe to for runs.

```
GitHub Action (6:17am)                     Your laptop (6:45am, optional)
  fetch → rank → dedupe → Claude notes        git pull → NotebookLM: paper → audio
  → site/2026-10-21.html + index.html         → GitHub Release + podcasts/feed.xml
  → GitHub Pages (+ email)                    → push → Pages redeploys with 🎧 links
```

## What a day looks like

| Section | Sources | When |
|---|---|---|
| 🌍 World | AP, BBC, NPR, Reuters, Quanta | daily |
| 📰 Tech & Seattle | GeekWire, Hacker News (250+ pts), Ars, Verge | daily |
| 🤖 AI / ML | Import AI, The Batch, Simon Willison, Interconnects, Latent Space, Karpathy, Anthropic, AI2, Google Research | daily |
| 🔥 Hot papers | Hugging Face Daily Papers (community upvotes) | daily |
| 🎧 Lab radar | arXiv: hearables, earables, target speech, acoustic/wireless sensing, backscatter, on-device… + Gollakota-lab authors | daily |
| 🛠️ SWE & systems | Pragmatic Engineer, ByteByteGo, Uber/Netflix/Meta/Cloudflare eng blogs, Kleppmann, Murat Demirbas, arXiv cs.DC | daily |
| 🎙️ Listen | No Priors, Lenny's, SE Daily, Lex, Acquired, Dwarkesh, Latent Space, Hard Fork, Up First, Huberman, Hardcore History, Mindscape, Radiolab | daily (1 ep/show max) |
| 📺 Watch | Karpathy, 3Blue1Brown, ByteByteGo, Fireship, Two Minute Papers, AI Explained, Kurzgesagt, TED, Crash Course | daily (Shorts filtered) |
| 📬 Newsletter catch-up | Lenny's, The Batch, Import AI | Sundays |
| 📅 Events | AI House Seattle (Luma) | Mon & Thu |
| 🎧 Podcast episodes | 2/day: topic bundles of fresh sources; Mon/Thu one is a classic paper + new work on its topic | daily |
| 🎯 Deliberate practice | *Zero to Hero* next video | Saturdays |
| 🗣️ Take a stance | 3 debatable questions from today's items, with the best case for each side (needs Claude key) | daily |

X/Twitter isn't scraped (the API is paywalled and scraping breaks constantly) — the brief reminds you to do your 10-minute list scroll instead.

## Setup (≈10 min)

1. **Make a GitHub repo** (private is fine) and push this folder.
2. **Turn on Pages**: Settings → Pages → Source: *GitHub Actions*.
3. **Optional secrets** (Settings → Secrets → Actions):
   - `ANTHROPIC_API_KEY` → adds a one-line "why it matters" under each item, a 3-bullet TL;DR, and the opinion prompts. Costs a few cents a day.
   - Email: `SMTP_HOST` (`smtp.gmail.com`), `SMTP_USER`, `SMTP_PASS` (a [Gmail app password](https://myaccount.google.com/apppasswords)), `BRIEF_EMAIL_TO`; then set `email.enabled: true` in `config.yaml`.
4. Actions → *daily-brief* → **Run workflow** to get your first brief. Bookmark the Pages URL on your phone's home screen.

Run locally any time:
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python run.py digest            # → site/index.html
python -m tests.smoke_test      # offline test of the whole pipeline
```

## Podcast episodes (NotebookLM)

**How episodes are chosen.** Every run adds candidates to a rolling 7-day pool: every fetched paper
(lab arXiv, HF Daily Papers, systems arXiv, plus a podcast-only arXiv feed on efficient/on-device/audio ML),
and AI/SWE blog posts or YouTube videos that clearly match a topic. Each candidate is tagged with one of
the `notebooklm.topics` in `config.yaml` (hearables, wireless sensing, on-device ML, reasoning, agents,
training & scaling, LLM serving/ML infra, distributed systems, interpretability, recsys, multimodal).
Then, each day, up to `episodes_per_day` episodes:

1. **Classic days (Mon/Thu, while the list lasts):** the next classic paper **plus up to 2 brand-new items
   on the same topic** in one notebook — e.g. *Raft* + a new flexible-quorum replication paper. The
   episode is structured "then & now".
2. **Topic episodes:** the strongest topic in the pool gets 2–4 sources (≥1 paper, ≤2 posts/videos)
   in **one notebook → one episode**, prompted to *synthesize* (shared problem, how the approaches
   differ, where they disagree, what matters most) rather than summarize each paper. Topics covered in
   the last 2 days only repeat if nothing else qualifies; lab topics have a boost. Bundles of 3+ sources
   automatically get NotebookLM's long length.
3. **Fallback:** if no topic has 2+ sources, the single strongest new paper gets its own deep dive.

Once the 15 classics are done, both daily slots are fresh topic episodes, indefinitely. Nothing is ever
podcasted twice (the same arXiv paper arriving from HF and arXiv counts as one), and each source in the
brief gets a 🎧 link to the episode it ended up in.

**Setup** — Google has no public NotebookLM API for personal accounts, so this uses the unofficial
[`notebooklm-py`](https://github.com/teng-lin/notebooklm-py) library, which drives your own NotebookLM
account with your browser login. Run it on **your laptop**, not in CI (it holds your Google session).

```bash
pip install "notebooklm-py[browser]" && playwright install chromium
notebooklm login                     # one-time browser sign-in
python run.py podcasts               # today's planned episodes → podcasts/*.m4a + site/podcasts/feed.xml
```

Notebooks are kept by default, so after listening you can open one and ask the sources follow-up questions.

**Automate it:** `scripts/podcasts_local.sh` pulls, generates, uploads audio to a GitHub Release
(`--storage github_release`, keeps big files out of git), and pushes the updated feed + 🎧 links.
On macOS, install `scripts/com.dailybrief.podcasts.plist` with launchd (edit the paths); on Linux,
`crontab -e` → `45 6 * * * /path/to/daily-brief/scripts/podcasts_local.sh >> podcasts.log 2>&1`.

**Subscribing on your phone:** release assets are only downloadable by podcast apps if the repo is
public. If you'd rather keep it private, skip `--storage github_release`, point `notebooklm.output_dir`
at an iCloud/Google Drive folder, and play the files from there — or just open the NotebookLM app, where
every generated overview also lives.

Limits to know: it's unofficial and can break when Google changes things (`notebooklm auth refresh`
fixes most auth issues; `pip install -U notebooklm-py` the rest), and NotebookLM has daily generation
quotas, which is why `episodes_per_day` defaults to 2.

## Tuning it

Everything is in `config.yaml`:
- **`interests`** — keyword → weight. This is the main ranking lever. Add terms from your current project; use negative weights to bury topics.
- **`sources`** — add any RSS feed, podcast (just the show name; the feed URL is looked up automatically), YouTube handle, or arXiv query. `weight` raises a source; `cadence: weekly` gives low-frequency sources an 8-day window.
- **`sections`** — order, size, and which weekdays they appear.
- **`notebooklm.topics`** — how sources get grouped into episodes; add a topic with a few keywords and it starts getting episodes. `boost` favors it.
- **`notebooklm`** — `episodes_per_day`, `max_sources_per_episode`, `format` (`debate` is fun for contested topics), `topic_cooldown_days`.
- **`curriculum`** — the classic-paper queue (each tagged with a `topic` for then-&-now bundling) and the Saturday video series. Progress is tracked in `state.json` (set `classic_idx` there to skip ahead).

Ranking is `source weight × interest match × recency + popularity` (HN points, HF upvotes, views), with at
most 2 items per source per section, near-duplicate headlines merged, and anything shown in the last 21
days suppressed.
