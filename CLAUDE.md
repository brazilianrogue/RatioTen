# RatioTen — Claude Code Context

## Project Overview
RatioTen is a personal nutrition/fitness tracking PWA. It uses Google Sheets as the data backend, a Python FastAPI server for scoring and AI coaching, and a single-page `index.html` frontend.

## Stack
- **Frontend:** `index.html` — single-file PWA (vanilla JS, no build step)
- **Backend:** `server.py` — FastAPI + Uvicorn
- **Scoring logic:** `scoring.py`
- **AI persona/coaching:** `persona.py`
- **Constants/config:** `constants.py`
- **Data layer:** Google Sheets via `gspread` (`sheets_client.py`)
- **Deployment:** Render (auto-deploys on push to `main`)

## Deployment Workflow
Render watches the `main` branch and auto-deploys on every push.

**`gh` CLI is not installed.** To deploy:
1. Commit and push the feature branch
2. Open the PR via the GitHub URL printed by `git push` (e.g. `https://github.com/brazilianrogue/RatioTen/pull/new/<branch>`)
3. Set base to `main` and merge — Render picks it up automatically

When the user says "commit and deploy", the workflow is:
1. `git add <files>`
2. `git commit`
3. `git push origin <branch>`
4. Provide the GitHub PR URL for the user to open and merge

## Repository
- **Remote:** https://github.com/brazilianrogue/RatioTen
- **Main branch:** `main`
- **Worktrees:** `.claude/worktrees/` (Claude Code uses git worktrees per session)
