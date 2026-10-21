#!/usr/bin/env bash
# Runs on your laptop each morning (after the GitHub Action builds the brief):
# pulls today's planned papers, makes NotebookLM podcasts, uploads audio to a GitHub
# Release, refreshes the feed + brief links, and pushes so Pages redeploys.
set -euo pipefail
cd "$(dirname "$0")/.."
source .venv/bin/activate 2>/dev/null || true

git pull --rebase --autostash -q
notebooklm auth refresh --quiet || { echo "NotebookLM auth expired — run: notebooklm login"; exit 1; }
python run.py podcasts --storage github_release

git add site
git diff --cached --quiet || { git commit -qm "podcasts $(date +%F)"; git push -q; }
# (the push touches site/podcasts/, which redeploys GitHub Pages without rebuilding the brief)
