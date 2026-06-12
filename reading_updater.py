"""
reading_updater.py — keeps dashboard.html current without manual work.

Modes:
  python3 reading_updater.py --daily    # refresh Reading Room news digest (Haiku)
  python3 reading_updater.py --weekly   # deeper strategic digest (Sonnet) + re-verify
                                        # model list & pricing in the market-data block

Contract with dashboard.html:
  - News digest lives between <!-- AUTO:DIGEST:BEGIN --> and <!-- AUTO:DIGEST:END -->
  - Date badge: <span id="reading-lastupdate" ...>Today: ...</span>
  - Market data: <script id="market-data" type="application/json"> ... </script>

Requires: ANTHROPIC_API_KEY in environment. pip install anthropic requests
"""

import os
import re
import json
import sys
import datetime
import xml.etree.ElementTree as ET

import requests
import anthropic

BASE = os.path.dirname(os.path.abspath(__file__))
DASHBOARD = os.path.join(BASE, "dashboard.html")
API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
client = anthropic.Anthropic(api_key=API_KEY)

HAIKU = os.environ.get("UPDATER_SMALL_MODEL", "claude-haiku-4-5")
SONNET = os.environ.get("UPDATER_BIG_MODEL", "claude-sonnet-4-6")

FEEDS = [
    # (label, url)
    ("TechCrunch AI", "https://techcrunch.com/category/artificial-intelligence/feed/"),
    ("Google News: Intuit", "https://news.google.com/rss/search?q=Intuit+OR+QuickBooks&hl=en-US&gl=US&ceid=US:en"),
    ("Google News: SMB accounting competitors", "https://news.google.com/rss/search?q=Xero+OR+FreshBooks+OR+%22Sage+accounting%22+OR+%22Wave+accounting%22&hl=en-US&gl=US&ceid=US:en"),
    ("Google News: fintech M&A", "https://news.google.com/rss/search?q=fintech+SMB+acquisition&hl=en-US&gl=US&ceid=US:en"),
    ("Anthropic news", "https://news.google.com/rss/search?q=Anthropic+Claude&hl=en-US&gl=US&ceid=US:en"),
]


def fetch_feed_items(max_per_feed: int = 8) -> list[dict]:
    items = []
    for label, url in FEEDS:
        try:
            r = requests.get(url, timeout=15, headers={"User-Agent": "dashboard-updater/1.0"})
            r.raise_for_status()
            root = ET.fromstring(r.content)
            for item in root.iter("item"):
                title = (item.findtext("title") or "").strip()
                link = (item.findtext("link") or "").strip()
                pub = (item.findtext("pubDate") or "").strip()
                desc = re.sub(r"<[^>]+>", "", item.findtext("description") or "")[:400]
                if title:
                    items.append({"feed": label, "title": title, "link": link,
                                  "date": pub, "summary": desc})
                if sum(1 for i in items if i["feed"] == label) >= max_per_feed:
                    break
            print(f"  ✓ {label}: ok")
        except Exception as e:
            print(f"  ⚠ {label}: {e}")
    return items


DIGEST_SYSTEM = """You write the news digest for an Intuit Corporate Strategy intern's
learning dashboard (focus: SMB accounting — Xero, FreshBooks, Wave, Sage — plus the
Claude/AI ecosystem). From the raw feed items, select the genuinely relevant ones
(usually 5-7), deduplicate, and produce HTML ONLY (no markdown, no commentary) with
EXACTLY this structure — three cards, same inline styles:

<div class="card" style="border-left:4px solid #0369a1;margin-bottom:12px">
  <div style="font-weight:700;font-size:14px;color:#0369a1;margin-bottom:10px">🏦 Intuit &amp; Fintech SMB <span style="font-weight:400;font-size:11px;color:var(--gray-400)">· {date_label}</span></div>
  <div style="display:flex;flex-direction:column;gap:8px">
    <!-- 1-3 items, each: -->
    <div style="background:var(--gray-50);border-radius:6px;padding:12px">
      <div style="font-weight:600;font-size:13px;margin-bottom:4px">{headline}</div>
      <div style="font-size:12px;color:var(--gray-600);margin-bottom:6px">{2-3 sentence summary with concrete facts}</div>
      <div style="font-size:11px;color:#0369a1;font-weight:600">💡 {implication for an Intuit Corp Strategy intern}</div>
    </div>
  </div>
</div>
<!-- card 2: 🤖 AI — What Changed, color #7c3aed; card 3: ⚔️ Strategy & M&A, color #059669 -->

Rules: only facts present in the feed items — do not invent numbers. If a category has
no relevant news, output the card with one item saying 'Quiet week' and a one-line note.
Escape & as &amp;. Output raw HTML only."""


def build_digest(items: list[dict], model: str, deep: bool) -> str:
    date_label = datetime.date.today().strftime("%b %d, %Y")
    extra = ("\nThis is the MONDAY DEEP DIGEST: prioritize strategic synthesis — patterns "
             "across the week, what to raise with the manager, second-order implications."
             if deep else "")
    msg = client.messages.create(
        model=model, max_tokens=3500,
        system=DIGEST_SYSTEM + extra,
        messages=[{"role": "user", "content":
                   f"date_label: week of {date_label}\n\nRaw feed items:\n"
                   + json.dumps(items, ensure_ascii=False, indent=1)}])
    html = msg.content[0].text.strip()
    html = re.sub(r"^```(html)?|```$", "", html, flags=re.M).strip()
    if "<div class=\"card\"" not in html:
        raise ValueError("Model did not return expected HTML — keeping existing digest")
    return html


def replace_digest(html: str) -> None:
    s = open(DASHBOARD, encoding="utf-8").read()
    pat = re.compile(r"(<!-- AUTO:DIGEST:BEGIN -->).*?(<!-- AUTO:DIGEST:END -->)", re.S)
    if not pat.search(s):
        raise RuntimeError("AUTO:DIGEST markers not found in dashboard.html")
    s = pat.sub(lambda m: m.group(1) + "\n\n" + html + "\n\n" + m.group(2), s, count=1)
    today = datetime.date.today().strftime("%-d %b %Y")
    s = re.sub(r'(<span id="reading-lastupdate"[^>]*>)[^<]*(</span>)',
               rf"\g<1>Today: {today}\g<2>", s, count=1)
    open(DASHBOARD, "w", encoding="utf-8").write(s)
    print(f"✅ Digest updated ({today})")


# ── Weekly: re-verify models & pricing ───────────────────────────────────────

def refresh_anthropic_models() -> list[str]:
    """Live model list from Anthropic API (authoritative)."""
    r = requests.get("https://api.anthropic.com/v1/models",
                     headers={"x-api-key": API_KEY, "anthropic-version": "2023-06-01"},
                     timeout=15)
    r.raise_for_status()
    return [m.get("id", "") for m in r.json().get("data", [])]


MARKET_SYSTEM = """You maintain a JSON block of LLM API pricing. You have web search.
Verify CURRENT per-1M-token prices (standard tier) on the official pricing pages of
Anthropic, OpenAI, Google (Gemini API) and Perplexity (Sonar). Then output ONLY the
updated JSON — same schema as the input, updated prices/models, last_verified set to
today. If you cannot verify a provider, keep its existing values unchanged. No
markdown, no commentary: raw JSON only."""


def refresh_market_data() -> None:
    s = open(DASHBOARD, encoding="utf-8").read()
    pat = re.compile(r'(<script id="market-data" type="application/json">\s*)(\{.*?\})(\s*</script>)', re.S)
    m = pat.search(s)
    if not m:
        raise RuntimeError("market-data block not found")
    current = m.group(2)

    try:
        msg = client.messages.create(
            model=SONNET, max_tokens=4000,
            system=MARKET_SYSTEM,
            tools=[{"type": "web_search_20250305", "name": "web_search", "max_uses": 8}],
            messages=[{"role": "user", "content":
                       f"Today: {datetime.date.today().isoformat()}\n\nCurrent JSON:\n{current}"}])
        raw = "".join(b.text for b in msg.content if b.type == "text").strip()
        raw = re.sub(r"^```(json)?|```$", "", raw, flags=re.M).strip()
        data = json.loads(raw)  # validation gate
        for key in ("last_verified", "anthropic", "competitors"):
            assert key in data, f"missing key {key}"
    except Exception as e:
        print(f"⚠ market refresh skipped (validation failed): {e}")
        return

    # sanity: cross-check Anthropic ids against the live /v1/models list
    try:
        live = set(refresh_anthropic_models())
        for entry in data["anthropic"]:
            if live and entry["id"] not in live:
                print(f"  ⚠ note: {entry['id']} not in live model list")
    except Exception as e:
        print(f"  ⚠ live model check skipped: {e}")

    new_json = json.dumps(data, ensure_ascii=False, indent=2)
    s = pat.sub(lambda mm: mm.group(1) + new_json + mm.group(3), s, count=1)
    open(DASHBOARD, "w", encoding="utf-8").write(s)
    print(f"✅ Market data refreshed (last_verified={data['last_verified']})")


def main() -> None:
    if not API_KEY:
        sys.exit("ANTHROPIC_API_KEY not set")
    mode = sys.argv[1] if len(sys.argv) > 1 else "--daily"
    print(f"🔄 Updater running: {mode}")
    print("Fetching feeds…")
    items = fetch_feed_items()
    if not items:
        sys.exit("No feed items fetched — aborting without touching dashboard")
    if mode == "--weekly":
        replace_digest(build_digest(items, SONNET, deep=True))
        refresh_market_data()
    else:
        replace_digest(build_digest(items, HAIKU, deep=False))


if __name__ == "__main__":
    main()
