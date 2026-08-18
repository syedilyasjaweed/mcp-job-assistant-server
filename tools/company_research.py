"""
Company research — Claude + web search, with a 7-day file cache.

Ported from ai-job-assistant/phase2_company_research.py, logic unchanged.
The __main__ CLI block was dropped since this is now called as a library
function by the MCP tool layer, not run standalone.

NOTE: this is an ANNOTATED copy for learning. The actual project file
(tools/company_research.py) has the same logic without these comments.
"""
import os
import json          # talks to Claude's JSON reply, and reads/writes the cache file
import hashlib        # turns a messy company name into a safe, unique filename
from pathlib import Path
from datetime import datetime, timedelta

from dotenv import load_dotenv
from anthropic import Anthropic

load_dotenv()  # reads .env so os.getenv() below can actually find your API key

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")

# NOTE: this is a RELATIVE path — it resolves based on wherever you run
# `uvicorn api:app` FROM, not based on where this file physically lives.
# Run the server from a different folder and your cache ends up somewhere
# unexpected. Worth remembering once you're inside a Docker container.
CACHE_DIR = Path("data/company_research_cache")
CACHE_TTL_DAYS = 7  # how many days a cached profile is considered "fresh"

# Standardized on claude-sonnet-5 across every Claude call in this server
# (see cover_letter.py) — cheaper per token than sonnet-4-6 ($2/$10 vs
# $3/$15), and materially stronger on agentic/tool-use tasks, which is
# exactly what this call is (Claude decides when/how to use web_search).
# Its tokenizer produces ~30% more tokens for the same text, so max_tokens
# below was given some extra headroom rather than left at the old value.
MODEL = "claude-sonnet-5"

# Lazy singleton, same pattern as resume_search.py: the Anthropic client
# isn't created until the first tool call actually needs it. That means
# importing this module never crashes just because a key is missing or
# wrong — only a real call would fail, not the whole server at boot.
_client = None


def _get_client():
    global _client
    if _client is None:
        _client = Anthropic(api_key=ANTHROPIC_API_KEY)
    return _client


def _cache_path(company_name: str) -> Path:
    # Company names can contain spaces, slashes, "&", etc. — characters
    # that are illegal or just awkward in filenames on some OSes.
    # Hashing sidesteps all of that: any string goes in, one clean
    # 10-character filename comes out, consistently unique per company.
    safe_name = hashlib.md5(company_name.lower().encode()).hexdigest()[:10]
    return CACHE_DIR / f"{safe_name}.json"


def _load_from_cache(company_name: str) -> dict | None:
    path = _cache_path(company_name)
    if not path.exists():
        return None  # never researched this company before — no file at all

    with open(path, "r") as f:
        cached = json.load(f)

    # Compare "now" to the timestamp _save_to_cache() stamped on the file
    # when it was written. Anything older than CACHE_TTL_DAYS is treated
    # as stale and NOT returned — the caller will fall through and
    # re-research instead of serving old data.
    cached_time = datetime.fromisoformat(cached["_cached_at"])
    if datetime.now() - cached_time > timedelta(days=CACHE_TTL_DAYS):
        return None

    return cached


def _save_to_cache(company_name: str, profile: dict) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)  # create data/company_research_cache/ if it's not there yet
    profile["_cached_at"] = datetime.now().isoformat()  # the timestamp _load_from_cache() reads back later
    profile["_company_name"] = company_name  # not strictly needed to re-read the file, but useful for debugging

    with open(_cache_path(company_name), "w") as f:
        json.dump(profile, f, indent=2)


def research_company(company_name: str, force_refresh: bool = False) -> dict:
    """
    Research a company via Claude's web search tool and return a
    structured profile dict: mission, recent_news, tech_stack, culture_notes.
    """
    # force_refresh is the override valve. Even a perfectly fresh cache
    # gets skipped entirely if the caller explicitly asks for new data.
    if not force_refresh:
        cached = _load_from_cache(company_name)
        if cached:
            return cached  # cache hit — skip the Claude call entirely, fast path

    # This prompt does double duty: it tells Claude WHAT to research, and
    # it tells Claude the EXACT JSON shape to answer in. That second part
    # is what lets json.loads() below work without a formal schema tool —
    # it's "prompted" structured output, not enforced structured output.
    prompt = f"""Research the company "{company_name}" using web search.

Find and summarize:
1. Mission / values - what they say their purpose and values are
2. Recent news - 2-3 notable recent developments (product launches, funding, etc.)
3. Tech stack - technologies, languages, or platforms they use or are known for
4. Culture notes - anything about work culture, remote policy, or what they value in employees

Respond ONLY with a JSON object in this exact format, no preamble, no markdown:
{{
  "mission": "...",
  "recent_news": ["...", "..."],
  "tech_stack": ["...", "..."],
  "culture_notes": "..."
}}"""

    response = _get_client().messages.create(
        model=MODEL,
        max_tokens=2048,
        # This "tools" parameter is what actually lets Claude search the
        # web instead of answering purely from what it already knows.
        tools=[{"type": "web_search_20250305", "name": "web_search"}],
        messages=[{"role": "user", "content": prompt}],
    )

    # response.content is a LIST of blocks — text blocks, tool-use blocks,
    # tool-result blocks all mixed together, because Claude may have
    # called web_search one or more times before writing its final
    # answer. We only want the text Claude actually wrote, so filter down
    # to block.type == "text" and stitch those pieces back into one string.
    text_parts = [block.text for block in response.content if block.type == "text"]
    full_text = "\n".join(text_parts).strip()

    # Defensive cleanup: models sometimes wrap JSON in a ```markdown code
    # fence even when explicitly told not to. Strip it before parsing,
    # rather than letting json.loads() blow up on the backticks.
    if full_text.startswith("```"):
        full_text = full_text.strip("`")
        if full_text.startswith("json"):
            full_text = full_text[4:].strip()

    try:
        profile = json.loads(full_text)
    except json.JSONDecodeError:
        # Safety net: if Claude's output still isn't clean JSON after the
        # cleanup above, don't crash the whole tool call. Degrade
        # gracefully into an empty-shaped profile — same keys the caller
        # expects — with the raw text preserved in _raw_response so you
        # can see what actually went wrong instead of just a stack trace.
        profile = {
            "mission": "",
            "recent_news": [],
            "tech_stack": [],
            "culture_notes": "",
            "_raw_response": full_text,
        }

    _save_to_cache(company_name, profile)  # cache miss or forced refresh — write the fresh result for next time
    return profile