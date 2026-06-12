# Publishing the dashboard — free, always up to date

Goal: open the dashboard from any computer, and have it update itself in the cloud
(even with your Mac off).

## The free stack: GitHub Pages + GitHub Actions

**One-time setup (~10 min):**

```bash
cd "/Users/rafaellalellis/Documents/Piloto IA Laser Dream"

# 1. Init repo (the .github/workflows folder is already here)
git init
printf "updater.log\ndashboard.autoupdate-backup.html\ndashboard.backup-*.html\n.DS_Store\n" > .gitignore
git add dashboard.html server.py reading_updater.py setup_auto_update.sh PUBLISH.md .github .gitignore
git commit -m "intelligence dashboard"

# 2. Push to your existing repo (or create a new one on github.com)
git remote add origin https://github.com/rafalellis19/ai-mastery-dashboard.git
git branch -M main
git push -u origin main --force   # only use --force if replacing the old repo content
```

**3. Add the API key secret** (powers the cloud auto-update):
Repo → Settings → Secrets and variables → Actions → New repository secret
`ANTHROPIC_API_KEY` = your key.

**4. Enable Pages:**
Repo → Settings → Pages → Source: "Deploy from a branch" → Branch `main`, folder `/ (root)`.
After ~1 min your dashboard is live at:
`https://rafalellis19.github.io/ai-mastery-dashboard/dashboard.html`

**5. Done — it stays current by itself:** the included workflow
(`.github/workflows/update_dashboard.yml`) runs daily at 07:00 UTC (news) and
Mondays (deep digest + model/pricing re-verification), commits to `main`, and
Pages republishes automatically.

## What works where

| Feature | Published (Pages) | Local (server.py) |
|---|---|---|
| All 19 sections, quiz, lab, study plan | ✅ | ✅ |
| Auto-updated news + pricing | ✅ (via Actions) | ✅ (via scheduled task / launchd) |
| Expert Advisor — live Claude recommendations | ⚠️ rule-based fallback (static page, no API key in the browser — by design) | ✅ live |

Never put the API key inside dashboard.html — anyone could read it on a public page.
If you want the live Expert on the web later: deploy `server.py` on a free tier
(e.g. Render/Fly.io) and point the fetch to it. Worth doing only if you actually
use the Expert away from your Mac.

## Notes

- Public repo = public dashboard. There's nothing sensitive in it today (no keys,
  no Intuit-confidential data) — keep it that way, especially during the internship.
  If you'd rather keep it private, GitHub Pro (free via the Student Developer Pack)
  enables Pages on private repos.
- Your local copy and the published copy update independently (launchd/scheduled
  task locally, Actions in the cloud). They don't need to sync; if you want them
  identical, `git pull` occasionally.
