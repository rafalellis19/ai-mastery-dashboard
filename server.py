"""
server.py — Dashboard server with live Claude Expert API
Fetches real-time model list from Anthropic before every recommendation.
Run: python3 server.py
Opens on http://localhost:3456
"""

import os, json, time
from pathlib import Path
from flask import Flask, send_from_directory, request, jsonify
from flask_cors import CORS
import anthropic
import requests as req_lib

app = Flask(__name__, static_folder=".")
CORS(app)

BASE_DIR  = Path(__file__).parent
API_KEY   = os.environ.get("ANTHROPIC_API_KEY", "")
client    = anthropic.Anthropic(api_key=API_KEY)

# ── Single source of truth for pricing (per 1M tokens) ──────────────────────
# Updated by reading_updater.py --weekly, or manually. Verify: anthropic.com/pricing
PRICING_LAST_VERIFIED = "2026-06-11"
PRICING = {
    "claude-fable-5":    {"in": 10.00, "out": 50.00, "note": "premium Mythos-class"},
    "claude-opus-4-8":   {"in": 5.00,  "out": 25.00, "note": "flagship reasoning"},
    "claude-sonnet-4-6": {"in": 3.00,  "out": 15.00, "note": "workhorse"},
    "claude-haiku-4-5":  {"in": 1.00,  "out": 5.00,  "note": "fast/cheap"},
}
EXPERT_MODEL = os.environ.get("EXPERT_MODEL", "claude-sonnet-4-6")

def pricing_text() -> str:
    lines = [f"- {mid}: ${v['in']:.2f} input / ${v['out']:.2f} output per 1M tokens ({v['note']})"
             for mid, v in PRICING.items()]
    return (f"CURRENT PRICING (last verified {PRICING_LAST_VERIFIED} — confirm at anthropic.com/pricing):\n"
            + "\n".join(lines)
            + "\n- Batch API: 50% discount on all models (async, <24h)"
            + "\n- Prompt caching: ~90% discount on cached input tokens")

# ── Model cache (refresh every 60 min) ───────────────────────────────────────
_model_cache = {"data": None, "fetched_at": 0}

def get_current_models() -> str:
    """Fetch real-time model list from Anthropic API. Cache for 60 min."""
    now = time.time()
    if _model_cache["data"] and (now - _model_cache["fetched_at"]) < 3600:
        return _model_cache["data"]

    try:
        resp = req_lib.get(
            "https://api.anthropic.com/v1/models",
            headers={
                "x-api-key": API_KEY,
                "anthropic-version": "2023-06-01"
            },
            timeout=8
        )
        if resp.status_code == 200:
            models = resp.json().get("data", [])
            lines = []
            for m in models:
                mid = m.get("model_id") or m.get("id", "")
                disp = m.get("display_name", mid)
                lines.append(f"- {mid}  ({disp})")
            result = "CURRENTLY AVAILABLE ANTHROPIC MODELS (fetched live right now):\n" + "\n".join(lines)
            _model_cache["data"] = result
            _model_cache["fetched_at"] = now
            print(f"✅ Fetched {len(models)} models from Anthropic API")
            return result
    except Exception as e:
        print(f"⚠️  Could not fetch models: {e}")

    # Fallback if API call fails
    fallback = ("ANTHROPIC MODELS (fallback — could not reach API; from PRICING dict, "
                f"last verified {PRICING_LAST_VERIFIED}):\n" +
                "\n".join(f"- {mid}  (${v['in']:.2f}/${v['out']:.2f} per 1M, {v['note']})"
                           for mid, v in PRICING.items()))
    _model_cache["data"] = fallback
    _model_cache["fetched_at"] = now
    return fallback


def get_expert_system(models_text: str) -> str:
    return f"""You are an expert advisor on Anthropic's Claude ecosystem.
You have access to the LIVE current model list fetched directly from Anthropic's API right now.

{models_text}

{pricing_text()}

CURRENT CAPABILITIES to consider:
- Extended thinking: available on Sonnet and Opus (budget_tokens param) — for hard multi-step reasoning
- Tool use / function calling: structured JSON schema — for agents that call real functions
- Computer use: Sonnet can control a browser/desktop
- Claude Code CLI: agentic coding agent, /plan /compact /review /memory /cost /model commands
- Claude.ai Projects: persistent system prompt + uploaded docs, no API needed
- MCP (Model Context Protocol): connect Claude to external tools (Notion, Slack, GitHub, databases)
- Batch API: async bulk processing, 50% discount, <24h turnaround
- Files API: upload documents once, reference by ID across calls
- Streaming: real-time token output for responsive UIs

Your job: given a user's problem, recommend the SINGLE BEST approach using the MOST CURRENT available models and features.

Always recommend by name the actual model ID from the live list above (not a generic name).
If a new model has been released since your training, use it — the live list is authoritative.

Return ONLY a valid JSON object — no markdown, no text outside the JSON:
{{
  "title": "short title (3-5 words)",
  "subtitle": "one sentence describing the approach",
  "emoji": "single emoji",
  "why": "2-3 sentences explaining why this is the right approach for THIS specific problem",
  "model": {{
    "name": "exact model id from the live list above (e.g. claude-sonnet-4-6)",
    "why": "2 sentences: why this specific model for this specific task",
    "config": ["key setting 1", "key setting 2", "key setting 3"],
    "cost_est": "estimated cost per run with numbers",
    "cheaper": "one concrete way to reduce cost if needed"
  }},
  "tools": [
    {{"label": "primary tool name", "primary": true}},
    {{"label": "secondary tool", "primary": false}}
  ],
  "steps": [
    {{"text": "step 1 — specific and immediately actionable"}},
    {{"text": "step 2"}},
    {{"text": "step 3"}},
    {{"text": "step 4"}}
  ],
  "effort": [
    {{"icon": "⏱️", "label": "Time to start", "value": "X min"}},
    {{"icon": "💰", "label": "Cost", "value": "$X per run"}},
    {{"icon": "🧩", "label": "Setup", "value": "None/Low/Medium/High"}}
  ],
  "template": "copy-paste starter code or prompt (use \\\\n for newlines)",
  "current_note": "mention any model from the live list that is especially relevant, or a recent capability that changes the recommendation. Be specific about the model ID and why."
}}"""


# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return send_from_directory(BASE_DIR, "dashboard.html")


@app.route("/<path:filename>")
def static_files(filename):
    return send_from_directory(BASE_DIR, filename)


@app.route("/api/models", methods=["GET"])
def models_endpoint():
    """Expose current model list for debugging."""
    return jsonify({"models": get_current_models()})


@app.route("/api/expert", methods=["POST"])
def expert():
    data    = request.get_json()
    problem = data.get("problem", "").strip()
    answers = data.get("answers", [])

    if not problem:
        return jsonify({"error": "No problem provided"}), 400
    if not API_KEY:
        return jsonify({"error": "ANTHROPIC_API_KEY not set"}), 500

    # 1. Fetch live model list
    models_text = get_current_models()

    # 2. Build user message with full context
    qa_text = ""
    if answers:
        qa_text = "\n\nUser's clarifying answers:\n" + "\n".join(
            f"- {a.get('question','')}: {a.get('answer','')}" for a in answers
        )

    user_msg = (
        f"Problem: {problem}{qa_text}\n\n"
        "Give me the best Claude approach, using the most appropriate model "
        "from the live model list in your system prompt."
    )

    try:
        response = client.messages.create(
            model=EXPERT_MODEL,
            max_tokens=1800,
            system=get_expert_system(models_text),
            messages=[{"role": "user", "content": user_msg}]
        )

        raw = response.content[0].text.strip()

        # Strip markdown fences if present
        if "```" in raw:
            parts = raw.split("```")
            for part in parts:
                part = part.strip()
                if part.startswith("json"):
                    part = part[4:].strip()
                try:
                    rec = json.loads(part)
                    return jsonify({"ok": True, "rec": rec, "models_used": models_text})
                except json.JSONDecodeError:
                    continue

        rec = json.loads(raw)
        return jsonify({"ok": True, "rec": rec, "models_used": models_text})

    except json.JSONDecodeError as e:
        return jsonify({"error": f"JSON parse failed: {e}", "raw": raw[:500]}), 500
    except anthropic.APIError as e:
        return jsonify({"error": str(e)}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    if not API_KEY:
        print("⚠️  ANTHROPIC_API_KEY not set — run: source ~/.zshrc")
    else:
        print(f"✅ API key loaded: {API_KEY[:15]}...")

    # Pre-fetch models on startup
    print("🔄 Fetching current Anthropic models...")
    print(get_current_models())

    print("\n🚀 Dashboard running at http://localhost:3456")
    print("   Expert uses live model list, refreshed every 60 min\n")
    app.run(port=3456, debug=False)
