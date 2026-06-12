#!/bin/bash
# publish.sh — one-command publish to GitHub Pages with cloud auto-update.
# Run:  bash "/Users/rafaellalellis/Documents/Projetos IA/publish.sh"
# You'll only be asked to authorize GitHub in the browser (one click).
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"
REPO_NAME="ai-mastery-dashboard"
OLD_REPO_NAME="intuit-intelligence-dashboard"

echo "── 1/7 GitHub CLI ──────────────────────────────"
if ! command -v gh >/dev/null 2>&1; then
  # reuse a previous local install if present
  LOCAL_GH=$(ls -d "$DIR/.tools/"gh_*/bin 2>/dev/null | head -1)
  if [ -n "$LOCAL_GH" ]; then
    export PATH="$LOCAL_GH:$PATH"
  elif command -v brew >/dev/null 2>&1; then
    brew install gh
  else
    echo "   Installing GitHub CLI locally (no Homebrew, no admin password needed)…"
    ARCH=$(uname -m); if [ "$ARCH" = "arm64" ]; then GHARCH="macOS_arm64"; else GHARCH="macOS_amd64"; fi
    TAG=$(curl -fsSL https://api.github.com/repos/cli/cli/releases/latest | sed -n 's/.*"tag_name": *"\(v[^"]*\)".*/\1/p' | head -1)
    VER="${TAG#v}"
    curl -fsSL "https://github.com/cli/cli/releases/download/${TAG}/gh_${VER}_${GHARCH}.zip" -o /tmp/gh.zip
    mkdir -p "$DIR/.tools" && unzip -oq /tmp/gh.zip -d "$DIR/.tools"
    export PATH="$DIR/.tools/gh_${VER}_${GHARCH}/bin:$PATH"
  fi
fi
gh --version | head -1

echo "── 2/7 GitHub login (browser opens — just authorize) ──"
gh auth status >/dev/null 2>&1 || gh auth login --hostname github.com --git-protocol https --web
GH_USER=$(gh api user -q .login)
echo "   Logged in as: $GH_USER"
# One-time: rename the old Intuit-branded repo if present
if gh repo view "$GH_USER/$OLD_REPO_NAME" >/dev/null 2>&1 && ! gh repo view "$GH_USER/$REPO_NAME" >/dev/null 2>&1; then
  echo "   Renaming repo: $OLD_REPO_NAME → $REPO_NAME"
  gh repo rename "$REPO_NAME" --repo "$GH_USER/$OLD_REPO_NAME" --yes
fi

echo "── 3/7 Git repo (ONLY dashboard files — your other documents are NOT included) ──"
# Deny-all gitignore with explicit whitelist: this folder contains unrelated
# private files (contracts, spreadsheets) that must never reach a public repo.
cat > .gitignore <<'EOF'
*
!dashboard.html
!server.py
!reading_updater.py
!setup_auto_update.sh
!health_check.py
!publish.sh
!PUBLISH.md
!index.html
!.nojekyll
!.gitignore
!.github/
!.github/workflows/
!.github/workflows/*.yml
EOF
if [ ! -d .git ]; then git init -b main; fi
git config user.name  "Rafa Lellis"
git config user.email "rafa.lellis@gmail.com"
git add -A
git -c core.hooksPath=/dev/null commit -m "intelligence dashboard $(date +%F)" || echo "   (nothing new to commit)"
# Safety: verify no private files staged
TRACKED=$(git ls-files | grep -vE '^(dashboard\.html|server\.py|reading_updater\.py|setup_auto_update\.sh|health_check\.py|publish\.sh|PUBLISH\.md|index\.html|\.nojekyll|\.gitignore|\.github/)' || true)
if [ -n "$TRACKED" ]; then echo "❌ Unexpected files tracked — aborting:"; echo "$TRACKED"; exit 1; fi
echo "   Files going public:"; git ls-files | sed 's/^/     /'

echo "── 4/7 Push ────────────────────────────────────"
if gh repo view "$GH_USER/$REPO_NAME" >/dev/null 2>&1; then
  git remote remove origin 2>/dev/null || true
  git remote add origin "https://github.com/$GH_USER/$REPO_NAME.git"
  git push -u origin main --force
else
  gh repo create "$REPO_NAME" --public --source . --remote origin --push
fi

echo "── 5/7 GitHub Pages ────────────────────────────"
PAGES_JSON='{"source":{"branch":"main","path":"/"}}'
if gh api "repos/$GH_USER/$REPO_NAME/pages" >/dev/null 2>&1; then
  echo "$PAGES_JSON" | gh api -X PUT "repos/$GH_USER/$REPO_NAME/pages" --input - >/dev/null 2>&1 || true
  echo "   ✅ Pages already enabled"
else
  if echo "$PAGES_JSON" | gh api -X POST "repos/$GH_USER/$REPO_NAME/pages" --input - >/dev/null 2>&1; then
    echo "   ✅ Pages enabled now"
  else
    echo "   ❌ Could not enable Pages via API."
    echo "      Manual (2 clicks): https://github.com/$GH_USER/$REPO_NAME/settings/pages → Source: Deploy from a branch → main / (root) → Save"
  fi
fi

echo "── 6/7 API key secret (read from your ~/.zshrc — never printed) ──"
KEY=$(zsh -ic 'echo $ANTHROPIC_API_KEY' 2>/dev/null | tail -1)
if [ -n "$KEY" ]; then
  printf '%s' "$KEY" | gh secret set ANTHROPIC_API_KEY --repo "$GH_USER/$REPO_NAME"
  echo "   ✅ ANTHROPIC_API_KEY set (cloud auto-update enabled)"
else
  echo "   ⚠️ ANTHROPIC_API_KEY not found in ~/.zshrc — cloud auto-update will fail."
  echo "      Fix later with: gh secret set ANTHROPIC_API_KEY --repo $GH_USER/$REPO_NAME"
fi

echo "── 7/7 First cloud update + health check ───────"
sleep 3
gh workflow run "Update dashboard" --repo "$GH_USER/$REPO_NAME" -f mode=--daily 2>/dev/null \
  && echo "   ✅ First update triggered (check Actions tab in ~2 min)" \
  || echo "   ⚠️ Trigger manually later: repo → Actions → Update dashboard → Run workflow"

echo "── 8/8 Local auto-update (launchd, 4:30am daily) ──"
bash "$DIR/setup_auto_update.sh" || echo "   ⚠️ launchd setup skipped — run later: bash setup_auto_update.sh"

echo "── Verifying the live page (can take a couple of minutes) ──"
URL="https://$GH_USER.github.io/$REPO_NAME/dashboard.html"
CODE="000"
for i in $(seq 1 20); do
  CODE=$(curl -s -o /dev/null -w "%{http_code}" "$URL")
  if [ "$CODE" = "200" ]; then break; fi
  printf "   waiting deploy… (%s, HTTP %s)\r" "$i" "$CODE"
  sleep 15
done
echo ""
if [ "$CODE" = "200" ]; then echo "   ✅ LIVE — confirmed with HTTP 200"
else echo "   ⚠️ Still HTTP $CODE after 5 min — check https://github.com/$GH_USER/$REPO_NAME/settings/pages and the Actions tab"; fi

echo ""
echo "════════════════════════════════════════════════"
echo "🚀 Published: https://$GH_USER.github.io/$REPO_NAME/dashboard.html"
echo "   (add to iPad Home Screen from Safari)"
echo "   Cloud auto-update: daily 07:00 UTC + Mondays deep refresh."
echo "════════════════════════════════════════════════"
