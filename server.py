"""
The MCP server itself: wraps the three tool functions from tools/ as
MCP tools using FastMCP's @mcp.tool decorator.

Nothing in here talks to Claude, VoyageAI, or Pinecone directly — this
file's only job is exposing tools/*.py in the standard MCP shape. All the
descriptions below are ported straight from react-agent-toolkit's
step3_agent_loop.py tools list (the docstrings ARE the tool descriptions
an MCP client will see — same role your hardcoded "description" fields
used to play).
"""
from fastmcp import FastMCP

from tools.resume_search import search_resume_bullets as _search_resume_bullets
from tools.company_research import research_company as _research_company
from tools.cover_letter import run_cover_letter_pipeline

mcp = FastMCP("job-assistant-tools")


@mcp.tool
def search_resume_bullets(query: str, top_k: int = 5) -> str:
    """
    Semantically searches Syed's resume bullets and returns the most relevant
    ones for a given query. Use this to find evidence of specific skills,
    tools, or experience relevant to a job description, role title, or
    requirement. The query is embedded and matched by meaning, not exact
    keywords.
    """
    return _search_resume_bullets(query, top_k=top_k)


@mcp.tool
def research_company(company_name: str, force_refresh: bool = False) -> dict:
    """
    Researches a company using live web search and returns a structured
    profile: mission/values, recent news, tech stack, and culture notes.
    Use this when you need concrete, current information about a specific
    company - e.g. before assessing culture fit or before drafting a cover
    letter. Results are cached for 7 days, so repeated calls for the same
    company are fast.
    """
    return _research_company(company_name, force_refresh=force_refresh)


@mcp.tool
def generate_cover_letter(
    company: str, role: str, jd_text: str, force_refresh: bool = False
) -> dict:
    """
    Generates a tailored, ready-to-send cover letter (under 400 words) for
    a specific job application, and saves it to disk. This tool internally
    re-runs resume matching and company research on its own, so call it
    directly once you have the company name, role title, and job
    description - you don't need to call search_resume_bullets or
    research_company first, though doing so can help decide whether
    generating a letter is actually warranted.
    """
    return run_cover_letter_pipeline(company, role, jd_text, force_refresh=force_refresh)
