"""
health_check.py — automated test that the dashboard is actually self-updating.

Run manually:        python3 health_check.py
Runs automatically:  after every launchd update (see setup_auto_update.sh)
                     and as a verify step in GitHub Actions.

Exit code 0 = all good. Non-zero = something is stale/broken (details printed).
"""

import datetime
import json
import os
import re
import subprocess
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
DASHBOARD = os.path.join(BASE, "dashboard.html")
LOG = os.path.join(BASE, "updater.log")

OK, FAIL = "✅", "❌"
failures: list[str] = []


def check(name: str, passed: bool, detail: str = "") -> None:
    print(f"{OK if passed else FAIL} {name}" + (f" — {detail}" if detail else ""))
    if not passed:
        failures.append(name)


def main() -> int:
    today = datetime.date.today()
    s = open(DASHBOARD, encoding="utf-8").read()

    # 1. Structural contract the updater depends on
    check("AUTO:DIGEST markers present",
          s.count("<!-- AUTO:DIGEST:BEGIN -->") == 1 and s.count("<!-- AUTO:DIGEST:END -->") == 1)

    m = re.search(r'<script id="market-data" type="application/json">\s*(\{.*?\})\s*</script>', s, re.S)
    check("market-data block present", bool(m))

    # 2. market-data parses and is fresh (re-verified Mondays → allow 8 days)
    if m:
        try:
            data = json.loads(m.group(1))
            verified = datetime.date.fromisoformat(data["last_verified"])
            age = (today - verified).days
            check("market-data JSON valid", True)
            check("pricing verified in the last 8 days", age <= 8, f"last_verified={verified} ({age}d ago)")
            check("anthropic model list non-empty", len(data.get("anthropic", [])) >= 3)
        except Exception as e:
            check("market-data JSON valid", False, str(e))

    # 3. News digest freshness — badge date must be within 2 days
    # Accepts ISO format (2026-06-14) or human format (14 Jun 2026)
    b = re.search(r'id="reading-lastupdate"[^>]*>Today:\s*([\d\w\- ]+?)<', s)
    if b:
        raw = b.group(1).strip()
        try:
            badge = None
            for fmt in ("%Y-%m-%d", "%d %b %Y", "%d %B %Y"):
                try:
                    badge = datetime.datetime.strptime(raw, fmt).date()
                    break
                except ValueError:
                    continue
            if badge is None:
                raise ValueError(f"unrecognized date format: {raw!r}")
            age = (today - badge).days
            check("news digest updated in the last 2 days", age <= 2, f"badge={badge} ({age}d ago)")
        except ValueError as e:
            check("news badge parseable", False, str(e))
    else:
        check("news badge present", False)

    # 4. Digest has real content (not an empty block)
    digest = re.search(r"<!-- AUTO:DIGEST:BEGIN -->(.*?)<!-- AUTO:DIGEST:END -->", s, re.S)
    check("digest has content cards", bool(digest) and digest.group(1).count('class="card"') >= 1)

    # 5. No leftover stale model ids the sweep should have killed
    stale = re.findall(r"claude-sonnet-4-5|claude-opus-4(?![\.\d-])", s)
    check("no stale model ids", not stale, f"found {len(stale)}" if stale else "")

    # 5b. Public-page hygiene — broken sanitization / insider voice must never ship
    FORBIDDEN = ["the your company", "na your company", "da your company",
                 "your company internship", "(ours or", "eat our category",
                 "Rafa Lellis"]
    hits = [f for f in FORBIDDEN if f in s]
    check("no forbidden/broken strings (public page)", not hits, ", ".join(hits))

    # 5c. No real API keys in the published file (placeholders are fine)
    keys = [k for k in re.findall(r"sk-ant-[A-Za-z0-9_-]{12,}", s)
            if not re.search(r"your|sua|key-here|xxx", k, re.I)]
    check("no real API keys leaked", not keys, f"found {len(keys)}" if keys else "")

    # 6. Updater ran recently (local machine only — skipped on CI)
    if os.environ.get("GITHUB_ACTIONS") != "true":
        if os.path.exists(LOG):
            age_h = (datetime.datetime.now()
                     - datetime.datetime.fromtimestamp(os.path.getmtime(LOG))).total_seconds() / 3600
            check("updater ran in the last 26h (updater.log)", age_h <= 26, f"{age_h:.0f}h ago")

            # 6b. ...and ran WITHOUT errors — a log full of failures still has a
            # fresh mtime, which fooled this check before (Jun 2026).
            tail = open(LOG, encoding="utf-8", errors="replace").read()[-4000:]
            bad = re.findall(r"Operation not permitted|Traceback|can't open file|Error",
                             tail, re.I)
            check("updater.log has no errors (last run succeeded)", not bad,
                  f"{len(bad)} error line(s) — grant Full Disk Access to python3/cron "
                  "or rely on the GitHub Action (cloud-first)")
        else:
            check("updater.log exists (has the updater ever run?)", False,
                  "run: python3 reading_updater.py --daily")

        # 7. launchd job loaded (macOS only)
        if sys.platform == "darwin":
            r = subprocess.run(["launchctl", "list"], capture_output=True, text=True)
            check("launchd auto-update job installed", "com.rafa.dashboard-updater" in r.stdout,
                  "run: bash setup_auto_update.sh")

    print()
    if failures:
        print(f"{FAIL} {len(failures)} check(s) failed: {', '.join(failures)}")
        return 1
    print(f"{OK} All checks passed — the dashboard is updating itself.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
