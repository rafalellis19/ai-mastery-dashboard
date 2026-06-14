#!/usr/bin/env python3
"""
reading_updater.py — Daily + Weekly news digest for the AI Mastery Dashboard

Usage:
    python3 reading_updater.py --daily    # every day at 07:00 UTC (GitHub Actions)
    python3 reading_updater.py --weekly   # every Monday at 06:00 UTC

Environment variables:
    ANTHROPIC_API_KEY   — always required
    NEWSAPI_KEY         — required for daily (skipped gracefully if missing)
    GH_TOKEN            — only needed for local git push (Actions uses GITHUB_TOKEN)
"""

import argparse
import json
import os
import re
import sys
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import anthropic
import requests

# ── Paths ──────────────────────────────────────────────────────────────────────
ROOT        = Path(__file__).parent
DASHBOARD   = ROOT / "dashboard.html"
DAILY_CACHE = ROOT / "daily_cache.json"

# ── Injection markers (must exist in dashboard.html) ──────────────────────────
MARKER_BEGIN = "<!-- AUTO:DIGEST:BEGIN -->"
MARKER_END   = "<!-- AUTO:DIGEST:END -->"

# ── Clients ────────────────────────────────────────────────────────────────────
client      = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
NEWSAPI_KEY = os.environ.get("NEWSAPI_KEY", "")
TODAY       = date.today().isoformat()


# ══════════════════════════════════════════════════════════════════════════════
#  SOURCE FETCHERS
# ══════════════════════════════════════════════════════════════════════════════

def fetch_newsapi(query: str, days: int = 1) -> list[dict]:
    """Pull articles from NewsAPI for the past `days` days."""
    if not NEWSAPI_KEY:
        print(f"  [SKIP] NEWSAPI_KEY not set — skipping: {query}")
        return []
    from_dt = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        r = requests.get(
            "https://newsapi.org/v2/everything",
            params={"q": query, "from": from_dt, "sortBy": "publishedAt",
                    "language": "en", "pageSize": 15, "apiKey": NEWSAPI_KEY},
            timeout=15,
        )
        r.raise_for_status()
        return [
            {"title": a.get("title", ""), "source": a.get("source", {}).get("name", ""),
             "url": a.get("url", ""),
             "description": (a.get("description") or a.get("content") or "")[:500],
             "published": a.get("publishedAt", "")}
            for a in r.json().get("articles", [])
            if a.get("title") and "[Removed]" not in (a.get("title") or "")
        ]
    except Exception as e:
        print(f"  [ERROR] NewsAPI ({query}): {e}")
        return []


def fetch_rss(url: str, source_name: str, max_items: int = 8) -> list[dict]:
    """Parse an RSS 2.0 or Atom feed."""
    try:
        r = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
        root = ET.fromstring(r.content)
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        items = []
        for item in root.findall(".//item")[:max_items]:
            items.append({
                "title": (item.findtext("title") or "").strip(),
                "source": source_name,
                "url": (item.findtext("link") or "").strip(),
                "description": (item.findtext("description") or "").strip()[:400],
                "published": item.findtext("pubDate") or "",
            })
        if not items:
            for entry in root.findall("atom:entry", ns)[:max_items]:
                link_el = entry.find("atom:link", ns)
                items.append({
                    "title": (entry.findtext("atom:title", namespaces=ns) or "").strip(),
                    "source": source_name,
                    "url": link_el.get("href", "") if link_el is not None else "",
                    "description": (entry.findtext("atom:summary", namespaces=ns) or "").strip()[:400],
                    "published": entry.findtext("atom:updated", namespaces=ns) or "",
                })
        return [i for i in items if i["title"]]
    except Exception as e:
        print(f"  [ERROR] RSS ({url}): {e}")
        return []


def fetch_sec_8k(cik_padded: str, company: str) -> list[dict]:
    """Fetch recent 8-K / 10-Q filings from SEC EDGAR (US-listed companies only)."""
    headers = {"User-Agent": "dashboard-bot rafa.lellis@gmail.com"}
    try:
        r = requests.get(
            f"https://data.sec.gov/submissions/CIK{cik_padded}.json",
            headers=headers, timeout=10,
        )
        r.raise_for_status()
        data   = r.json()
        recent = data.get("filings", {}).get("recent", {})
        cutoff = (date.today() - timedelta(days=14)).isoformat()
        results = []
        for form, d, acc, doc in zip(
            recent.get("form", []), recent.get("filingDate", []),
            recent.get("accessionNumber", []), recent.get("primaryDocument", []),
        ):
            if form in ("8-K", "10-Q", "10-K") and d >= cutoff:
                acc_fmt = acc.replace("-", "")
                cik_int = int(cik_padded)
                results.append({
                    "title": f"{company} filed {form} ({d})",
                    "source": "SEC EDGAR",
                    "url": f"https://www.sec.gov/Archives/edgar/data/{cik_int}/{acc_fmt}/{doc}",
                    "description": f"{company} {form} regulatory filing, dated {d}.",
                    "published": d,
                })
        return results
    except Exception as e:
        print(f"  [WARN] EDGAR ({company}): {e}")
        return []


# ══════════════════════════════════════════════════════════════════════════════
#  HAIKU FILTERING + EXTRACTION
# ══════════════════════════════════════════════════════════════════════════════

_FILTER_SYSTEM = (
    "You are a triage assistant for a Corp Strategy analyst at Intuit "
    "(QuickBooks, TurboTax, Credit Karma). Mark each article as YES or SKIP.\n"
    "YES if relevant to: SMB fintech competitors, AI in financial software, "
    "M&A in fintech/SaaS, Intuit news, regulatory changes for SMB software, "
    "Anthropic/OpenAI/Google model updates that affect product strategy.\n"
    "Reply ONLY with a JSON array:\n"
    '[{"index":0,"verdict":"YES","reason":"one line"},{"index":1,"verdict":"SKIP"},...]\n'
    "No text outside the JSON."
)

_EXTRACT_SYSTEM = (
    "You are a Corp Strategy analyst at Intuit. Extract structured data.\n"
    "Reply ONLY with a JSON array:\n"
    "[\n"
    "  {\n"
    '    "title": "clean headline ≤12 words",\n'
    '    "source": "publication",\n'
    '    "url": "url",\n'
    '    "summary": "one sentence — what happened",\n'
    '    "implication": "one sentence — so what for Intuit / Corp Strategy",\n'
    '    "urgency": "High|Medium",\n'
    '    "category": "SMB Fintech|AI|M&A|Intuit|Regulatory"\n'
    "  }\n"
    "]\n"
    "No text outside the JSON."
)


def _call_haiku(system: str, user: str) -> str:
    msg = client.messages.create(
        model="claude-haiku-4-5",
        max_tokens=2048,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    return msg.content[0].text.strip()


def _extract_json(text: str) -> str:
    m = re.search(r"\[.*\]", text, re.DOTALL)
    return m.group(0) if m else text


def filter_articles(articles: list[dict]) -> list[dict]:
    if not articles:
        return []
    numbered = "\n\n".join(
        f"[{i}] TITLE: {a['title']}\nSOURCE: {a['source']}\nDESC: {a['description'][:300]}"
        for i, a in enumerate(articles)
    )
    try:
        raw      = _call_haiku(_FILTER_SYSTEM, f"Filter:\n\n{numbered}")
        verdicts = json.loads(_extract_json(raw))
        kept     = [articles[v["index"]] for v in verdicts if v.get("verdict") == "YES"]
        print(f"  Filter: {len(kept)}/{len(articles)} relevant")
        return kept
    except Exception as e:
        print(f"  [WARN] filter parse error: {e} — keeping all")
        return articles


def extract_articles(articles: list[dict]) -> list[dict]:
    if not articles:
        return []
    blob = "\n\n".join(
        f"TITLE: {a['title']}\nSOURCE: {a['source']}\nURL: {a['url']}\nDESC: {a['description'][:500]}"
        for a in articles
    )
    try:
        raw = _call_haiku(_EXTRACT_SYSTEM, f"Extract:\n\n{blob}")
        return json.loads(_extract_json(raw))
    except Exception as e:
        print(f"  [WARN] extract parse error: {e} — using fallback")
        return [
            {"title": a["title"], "source": a["source"], "url": a["url"],
             "summary": a["description"][:150], "implication": "",
             "urgency": "Medium", "category": "SMB Fintech"}
            for a in articles
        ]


# ══════════════════════════════════════════════════════════════════════════════
#  HTML BUILDERS
# ══════════════════════════════════════════════════════════════════════════════

_CAT_STYLE = {
    "SMB Fintech": {"icon": "🏦", "color": "#0369a1", "border": "border-left:4px solid #0369a1"},
    "AI":          {"icon": "🤖", "color": "#7c3aed", "border": "border-left:4px solid #7c3aed"},
    "M&A":         {"icon": "⚔️",  "color": "#059669", "border": "border-left:4px solid #059669"},
    "Intuit":      {"icon": "💚", "color": "#166534", "border": "border-left:4px solid #166534"},
    "Regulatory":  {"icon": "⚖️",  "color": "#b45309", "border": "border-left:4px solid #b45309"},
}
_CAT_ORDER = ["Intuit", "SMB Fintech", "AI", "M&A", "Regulatory"]

_URGENCY_BADGE = {
    "High": '<span style="background:#fee2e2;color:#991b1b;font-size:10px;font-weight:700;'
            'padding:1px 6px;border-radius:4px;margin-left:6px">🔴 HIGH</span>',
    "Medium": "",
}


def _article_card_html(a: dict) -> str:
    badge = _URGENCY_BADGE.get(a.get("urgency", "Medium"), "")
    impl  = f'<div style="font-size:11px;color:#0369a1;font-weight:600;margin-top:4px">💡 {a["implication"]}</div>' \
            if a.get("implication") else ""
    return (
        '    <div style="background:var(--gray-50);border-radius:6px;padding:12px">\n'
        f'      <div style="font-weight:600;font-size:13px;margin-bottom:4px">'
        f'<a href="{a.get("url","#")}" target="_blank" rel="noopener" style="color:inherit;text-decoration:none">'
        f'{a.get("title","")}</a>{badge}'
        f'<span style="font-weight:400;font-size:11px;color:var(--gray-400);margin-left:6px">{a.get("source","")}</span>'
        f'</div>\n'
        f'      <div style="font-size:12px;color:var(--gray-600)">{a.get("summary","")}</div>\n'
        f'      {impl}\n'
        '    </div>'
    )


def build_daily_html(articles: list[dict], date_str: str) -> str:
    groups: dict[str, list] = {}
    for a in articles:
        groups.setdefault(a.get("category", "SMB Fintech"), []).append(a)

    ordered = [c for c in _CAT_ORDER if c in groups] + \
              [c for c in groups if c not in _CAT_ORDER]

    blocks = []
    for cat in ordered:
        s     = _CAT_STYLE.get(cat, _CAT_STYLE["SMB Fintech"])
        cards = "\n".join(_article_card_html(a) for a in groups[cat])
        blocks.append(
            f'<div class="card" style="{s["border"]};margin-bottom:12px">\n'
            f'  <div style="font-weight:700;font-size:14px;color:{s["color"]};margin-bottom:10px">'
            f'{s["icon"]} {cat} <span style="font-weight:400;font-size:11px;color:var(--gray-400)">· {date_str}</span></div>\n'
            f'  <div style="display:flex;flex-direction:column;gap:8px">\n{cards}\n  </div>\n</div>'
        )

    return "\n\n".join(blocks) if blocks else (
        f'<div class="card"><p style="color:var(--gray-500);font-size:13px">'
        f'📭 No significant news found for {date_str}.</p></div>'
    )


def build_weekly_html(digest_md: str, date_str: str) -> str:
    """Convert Sonnet markdown digest → styled HTML card."""
    lines, out, in_ul = digest_md.strip().split("\n"), [], False

    def close_ul():
        nonlocal in_ul
        if in_ul:
            out.append("</ul>")
            in_ul = False

    def md_inline(s: str) -> str:
        return re.sub(r"\*\*(.*?)\*\*", r"<strong>\1</strong>", s)

    for line in lines:
        line = line.rstrip()
        if not line:
            close_ul(); out.append(""); continue
        if line.startswith("## "):
            close_ul()
            out.append(f'<h3 style="font-size:14px;font-weight:700;color:var(--primary);margin:16px 0 6px">{md_inline(line[3:])}</h3>')
        elif line.startswith("### "):
            close_ul()
            out.append(f'<h4 style="font-size:13px;font-weight:700;color:var(--gray-800);margin:12px 0 4px">{md_inline(line[4:])}</h4>')
        elif line.startswith(("- ", "• ")):
            if not in_ul:
                out.append('<ul style="margin:4px 0 8px;padding-left:18px;font-size:12px;color:var(--gray-700)">')
                in_ul = True
            out.append(f"<li style='margin-bottom:4px'>{md_inline(line[2:])}</li>")
        elif re.match(r"^\d+\.", line):
            close_ul()
            out.append(f'<p style="font-size:12px;color:var(--gray-700);margin:4px 0">{md_inline(re.sub(r"^\d+\.\s*", "", line))}</p>')
        elif any(e in line for e in ("🔥", "📊", "😴")):
            close_ul()
            out.append(f'<div style="font-size:16px;font-weight:700;text-align:center;padding:10px;background:var(--gray-50);border-radius:8px;margin:10px 0">{md_inline(line)}</div>')
        else:
            close_ul()
            out.append(f'<p style="font-size:12px;color:var(--gray-700);margin:4px 0;line-height:1.6">{md_inline(line)}</p>')

    close_ul()
    body = "\n".join(out)
    return (
        '<div class="card" style="border-left:4px solid #7c3aed;margin-bottom:16px">\n'
        f'  <div style="font-weight:700;font-size:15px;color:#7c3aed;margin-bottom:14px">\n'
        f'    📅 Weekly Strategic Digest\n'
        f'    <span style="font-weight:400;font-size:11px;color:var(--gray-400)">· Week of {date_str}</span>\n'
        f'  </div>\n'
        f'  <div style="line-height:1.7">\n{body}\n  </div>\n'
        '</div>'
    )


# ══════════════════════════════════════════════════════════════════════════════
#  DASHBOARD INJECTION
# ══════════════════════════════════════════════════════════════════════════════

def inject_into_dashboard(new_content: str) -> None:
    html = DASHBOARD.read_text(encoding="utf-8")
    if MARKER_BEGIN not in html or MARKER_END not in html:
        raise ValueError(
            f"Injection markers not found in {DASHBOARD}.\n"
            f"Add these two comment tags:\n  {MARKER_BEGIN}\n  {MARKER_END}"
        )
    pattern = re.compile(re.escape(MARKER_BEGIN) + r".*?" + re.escape(MARKER_END), re.DOTALL)
    updated = pattern.sub(f"{MARKER_BEGIN}\n\n{new_content}\n\n{MARKER_END}", html)
    # Refresh the last-update badge
    updated = re.sub(
        r'id="reading-lastupdate"[^>]*>[^<]*<',
        f'id="reading-lastupdate" class="badge badge-green">Today: {TODAY}<',
        updated,
    )
    DASHBOARD.write_text(updated, encoding="utf-8")
    print(f"  ✅ Injected into dashboard.html")


# ══════════════════════════════════════════════════════════════════════════════
#  CACHE
# ══════════════════════════════════════════════════════════════════════════════

def load_cache() -> list[dict]:
    if not DAILY_CACHE.exists():
        return []
    try:
        data   = json.loads(DAILY_CACHE.read_text())
        cutoff = (date.today() - timedelta(days=7)).isoformat()
        return [a for a in data if a.get("date", "") >= cutoff]
    except Exception:
        return []


def save_cache(articles: list[dict]) -> None:
    existing  = load_cache()
    today_urls = {a.get("url") for a in articles}
    pruned    = [a for a in existing if a.get("url") not in today_urls]
    fresh     = [dict(a, date=TODAY) for a in articles]
    all_items = pruned + fresh
    DAILY_CACHE.write_text(json.dumps(all_items, indent=2, ensure_ascii=False))
    print(f"  Cache: {len(all_items)} articles total ({len(fresh)} new today)")


# ══════════════════════════════════════════════════════════════════════════════
#  MODES
# ══════════════════════════════════════════════════════════════════════════════

_RSS_SOURCES = [
    ("https://www.anthropic.com/feed.rss",             "Anthropic Blog"),
    ("https://feeds.feedburner.com/TechCrunchFintech",  "TechCrunch Fintech"),
    # Add Stratechery email RSS here if subscribed:
    # ("https://stratechery.com/feed/", "Stratechery"),
]

_NEWSAPI_QUERIES = [
    "Intuit OR QuickBooks OR Xero OR FreshBooks OR Wave accounting",
    "fintech M&A SMB software 2026",
    "SMB accounting AI assistant",
    "Anthropic OpenAI Google AI model release",
]

_SEC_COMPANIES = [
    ("0000896878", "Intuit"),   # Intuit CIK (zero-padded to 10 digits)
    ("0001843973", "Toast"),    # Toast CIK
]


def run_daily() -> None:
    print(f"\n{'═'*58}\n  DAILY MODE — {TODAY}\n{'═'*58}")

    # 1. Fetch all sources
    print("\n[1/4] Fetching sources…")
    raw: list[dict] = []
    for query in _NEWSAPI_QUERIES:
        raw += fetch_newsapi(query, days=1)
    for url, name in _RSS_SOURCES:
        raw += fetch_rss(url, name)
    for cik, company in _SEC_COMPANIES:
        raw += fetch_sec_8k(cik, company)

    # Deduplicate by URL
    seen:    set[str]   = set()
    deduped: list[dict] = []
    for a in raw:
        if a["url"] and a["url"] not in seen:
            seen.add(a["url"])
            deduped.append(a)
    print(f"  Raw: {len(deduped)} unique articles")

    # 2. Filter
    print("\n[2/4] Filtering with Haiku…")
    relevant = filter_articles(deduped)
    if not relevant:
        inject_into_dashboard(
            f'<div class="card"><p style="color:var(--gray-500);font-size:13px">'
            f'📭 No significant news found for {TODAY}. Check back tomorrow.</p></div>'
        )
        return

    # 3. Extract structured data
    print("\n[3/4] Extracting with Haiku…")
    structured = extract_articles(relevant)

    # 4. Save cache + inject
    print("\n[4/4] Building HTML and injecting…")
    save_cache(structured)
    html = build_daily_html(structured, TODAY)
    inject_into_dashboard(html)
    print(f"\n  Done — {len(structured)} articles published.")


_WEEKLY_SYSTEM = (
    "You are a senior Corp Strategy analyst at Intuit (QuickBooks, TurboTax, Credit Karma). "
    "Your reader: your VP of Strategy, Monday morning before standup. "
    "Tone: sharp, direct, lead with so-what. No filler."
)

_WEEKLY_PROMPT = """\
Analyze this week's fintech/AI news for Intuit's Corp Strategy team.

ARTICLES (past 7 days):
{articles_json}

Write the weekly digest in EXACTLY this structure:

## 1. Most Important Move This Week
4–6 sentences. What happened, why it signals a shift, what Intuit should watch.

## 2. Strategic Implications for Intuit
3–5 bullets. Each: **Bold claim** — 1–2 sentence explanation. Be specific (name products, segments, revenue lines).

## 3. Raise in Your 1:1 This Week
2–3 questions or hypotheses worth discussing with your manager.
**Question:** or **Hypothesis:** format.

## 4. Recommended Reads This Week
3 articles from the list worth deeper reading.
**Title** — one sentence on why it's worth your time.

## 5. Competitive Week Rating
Pick one and justify in 1 sentence:
🔥 Hot — major moves that shift the landscape
📊 Normal — steady signals, nothing urgent
😴 Quiet — slow week, go deep on fundamentals"""


def run_weekly() -> None:
    print(f"\n{'═'*58}\n  WEEKLY MODE — {TODAY}\n{'═'*58}")

    # Step 0: run today's daily first
    print("\n[0/3] Running daily update first…")
    run_daily()

    # Step 1: load 7-day cache
    print("\n[1/3] Loading weekly cache…")
    articles = load_cache()
    if not articles:
        print("  No cached articles — weekly digest skipped.")
        return
    print(f"  {len(articles)} articles from the past 7 days")

    # Step 2: Sonnet deep analysis
    print("\n[2/3] Generating weekly digest with Sonnet…")
    articles_json = json.dumps(
        [{"title":      a.get("title", ""),
          "source":     a.get("source", ""),
          "summary":    a.get("summary", ""),
          "implication":a.get("implication", ""),
          "category":   a.get("category", ""),
          "urgency":    a.get("urgency", "Medium"),
          "url":        a.get("url", ""),
          "date":       a.get("date", "")}
         for a in articles],
        indent=2,
    )
    msg = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=2200,
        system=_WEEKLY_SYSTEM,
        messages=[{"role": "user", "content": _WEEKLY_PROMPT.format(articles_json=articles_json)}],
    )
    digest_md = msg.content[0].text.strip()

    # Step 3: prepend weekly card above today's daily content
    print("\n[3/3] Injecting weekly digest…")
    html = DASHBOARD.read_text(encoding="utf-8")
    m = re.search(re.escape(MARKER_BEGIN) + r"(.*?)" + re.escape(MARKER_END), html, re.DOTALL)
    current_daily = m.group(1).strip() if m else ""

    weekly_card = build_weekly_html(digest_md, TODAY)
    combined    = f"{weekly_card}\n\n{current_daily}" if current_daily else weekly_card
    inject_into_dashboard(combined)
    print(f"\n  Done — weekly digest published.")


# ══════════════════════════════════════════════════════════════════════════════
#  ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Update the AI Mastery Dashboard reading room")
    group  = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--daily",  action="store_true", help="Daily news refresh")
    group.add_argument("--weekly", action="store_true", help="Full weekly strategic digest")
    args = parser.parse_args()

    if args.weekly:
        run_weekly()
    else:
        run_daily()
