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

**`gh` CLI is not installed.** Do not create PRs — merge directly into `main` via git.

When the user says "commit and deploy", the workflow is:
1. `git add <files>` and `git commit` on the worktree/feature branch
2. From the main repo (`C:\Users\Brazi\VibesForClaude\RatioTen`), run:
   ```
   git fetch origin <branch>
   git merge origin/<branch> --no-edit
   git push origin main
   ```
3. Render auto-deploys from the push — no PR or manual merge needed.

## Repository
- **Remote:** https://github.com/brazilianrogue/RatioTen
- **Main branch:** `main`
- **Worktrees:** `.claude/worktrees/` (Claude Code uses git worktrees per session)
