"""
Cover letter generation: resume matching + company research + Claude-written
letter, saved to disk.

Ported from ai-job-assistant/phase1_matcher.py (embed_query/search_pinecone)
and phase3_cover_letter.py. The old cross-repo import
(`AI_JOB_ASSISTANT_PATH = Path.home() / "Developer" / "ai-job-assistant"`,
`sys.path.insert(...)`) is gone — this module now imports resume search and
company research as local siblings inside this same server package, so the
whole thing runs from one self-contained project instead of two repos glued
together with a path hack.

Standardized on claude-sonnet-5 across every Claude call in this server —
this file, company_research.py, and react-agent-toolkit's agent loop now
all agree on one model, closing the mismatch flagged in an earlier version
of this comment (this file was on sonnet-4-6, the agent loop was already
on sonnet-5).

NOTE: this is an ANNOTATED copy for learning. The actual project file
(tools/cover_letter.py) has the same logic without these extra comments.
"""
import os
import re          # strips unsafe characters out of filenames before saving to disk
import json        # pretty-prints the company profile dict into the prompt text
from pathlib import Path
from datetime import datetime

import anthropic
from dotenv import load_dotenv

# These two imports are the whole reason the old sys.path hack could be
# deleted: resume search and company research now live INSIDE this same
# package (tools/resume_search.py, tools/company_research.py), so this
# file just imports them like any other local module — no cross-repo
# path manipulation needed anymore.
from .resume_search import search_resume_bullets_raw
from .company_research import research_company

load_dotenv()

MODEL = "claude-sonnet-5"
COVER_LETTER_DIR = Path("data/cover_letters")

# The "personality" instructions — this never changes per-request, so
# it's defined once here and reused on every call below.
SYSTEM_PROMPT = """You are an expert career coach and professional writer
specializing in AI/ML and tech roles. You write compelling, authentic cover
letters that:

- Open with a specific hook tied to the company's mission or recent work
- Weave in the candidate's exact experience naturally (don't just list bullets)
- Connect the candidate's background to the company's specific needs
- Stay under 400 words (3-4 paragraphs)
- Sound human and confident, never generic or robotic
- Use professional but conversational tone - no fluff, no buzzwords

Formatting rules:
- Do NOT include a salutation (no Dear Hiring Manager)
- Do NOT include a subject line or date
- Start directly with the opening paragraph
- End after the closing paragraph - no signature block
- Plain text only, no markdown formatting"""

# Same lazy-singleton pattern as the other two tool files.
_client = None


def _get_client():
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    return _client


def get_matched_bullets(jd_text: str, top_k: int = 5) -> list[dict]:
    """
    Formerly phase1_matcher.embed_query + search_pinecone, combined.
    Now just delegates to resume_search.py's already-built structured
    search instead of duplicating the embedding + Pinecone query logic
    a second time in this file.
    """
    return search_resume_bullets_raw(jd_text, top_k=top_k)


def get_company_profile(company: str, refresh: bool = False) -> dict:
    # Thin wrapper — just renames "refresh" to the "force_refresh" param
    # research_company() actually expects.
    return research_company(company, force_refresh=refresh)


def generate_cover_letter(company, role, jd_text, bullets, profile) -> str:
    # Turn the list of {"bullet": ..., "score": ...} dicts into readable,
    # numbered lines Claude can read directly inside the prompt.
    bullets_text = "\n".join(
        f"  {i + 1}. [score: {b['score']:.2f}] {b['bullet']}"
        for i, b in enumerate(bullets)
    )
    # json.dumps here isn't talking to an API — it's just turning the
    # profile dict into readable text to paste into the prompt below.
    profile_text = json.dumps(profile, indent=2)

    user_prompt = f"""Write a tailored cover letter for this job application.

== TARGET ROLE ==
Company: {company}
Position: {role}

== JOB DESCRIPTION ==
{jd_text[:3000]}

== CANDIDATE'S TOP MATCHING RESUME BULLETS ==
{bullets_text}

== COMPANY RESEARCH PROFILE ==
{profile_text}

Instructions:
- Open by referencing something specific from the company profile
- Naturally work in 2-3 of the top resume bullets as proof of fit
- The candidate has an MS in Information Technology Management from
  Illinois Institute of Technology and hands-on AI/ML project experience
- Close with enthusiasm and a clear call to action
- Plain text only, under 400 words"""
    # jd_text[:3000] caps the job description at 3000 characters — a
    # cheap guardrail against an unusually long JD blowing up token
    # usage (and cost) on a single call.

    response = _get_client().messages.create(
        model=MODEL,
        max_tokens=1536,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_prompt}],
    )

    # Unlike company_research.py, this doesn't loop over response.content
    # filtering by block type — no tools were given to this call, so
    # Claude's reply is guaranteed to be exactly one text block at index 0.
    return response.content[0].text.strip()


def save_cover_letter(company: str, role: str, letter: str) -> Path:
    COVER_LETTER_DIR.mkdir(parents=True, exist_ok=True)

    # re.sub replaces anything that ISN'T a word character or hyphen with
    # an underscore — same filename-safety goal as the MD5 hashing in
    # company_research.py, just a different technique (readable filenames
    # instead of hashed ones, since company + role names are short and
    # already human-friendly, unlike arbitrary company name strings).
    safe_company = re.sub(r"[^\w\-]", "_", company.lower())
    safe_role = re.sub(r"[^\w\-]", "_", role.lower().replace(" ", "_"))
    date_str = datetime.now().strftime("%Y%m%d")
    filename = f"{safe_company}_{safe_role}_{date_str}.txt"

    output_path = COVER_LETTER_DIR / filename
    output_path.write_text(letter, encoding="utf-8")
    return output_path


def run_cover_letter_pipeline(
    company: str, role: str, jd_text: str, force_refresh: bool = False
) -> dict:
    """
    Full pipeline in one call: match resume -> research company -> generate
    letter -> save to disk. This is exactly what step3_agent_loop.py's
    run_tool() did inline in its generate_cover_letter branch — pulled out
    here so the MCP tool wrapper can just call one function.
    """
    # These run in sequence. Resume matching and company research don't
    # actually depend on each other's output, but generate_cover_letter
    # needs BOTH results — a possible later optimization would be running
    # them concurrently instead of one after another.
    bullets = get_matched_bullets(jd_text)
    profile = get_company_profile(company, force_refresh)
    letter = generate_cover_letter(company, role, jd_text, bullets, profile)
    output_path = save_cover_letter(company, role, letter)

    return {"cover_letter": letter, "saved_to": str(output_path)}