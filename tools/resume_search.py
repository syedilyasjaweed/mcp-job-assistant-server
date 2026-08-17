"""
Resume bullet semantic search — VoyageAI embeddings + Pinecone vector search.

Ported from react-agent-toolkit/step2_real_tool.py. The matching logic is
unchanged. One deliberate change: the VoyageAI/Pinecone clients are now
created lazily (on first call) instead of at import time. That matters here
specifically because this module gets imported the moment the MCP server
boots — if the clients were built eagerly like before, a missing API key
would crash the whole server at startup instead of failing just the one
tool call that needed it.

NOTE: this is an ANNOTATED copy for learning. The actual project file
(tools/resume_search.py) has the same logic without these extra comments.
"""
import os
from dotenv import load_dotenv
import voyageai
from pinecone import Pinecone

load_dotenv()

VOYAGE_API_KEY = os.getenv("VOYAGE_API_KEY")
PINECONE_API_KEY = os.getenv("PINECONE_API_KEY")
INDEX_NAME = "resume-bullets"  # the Pinecone index your resume bullets were uploaded into ahead of time

# Two separate lazy singletons — one per external service. Each is only
# built the first time its getter is actually called.
_voyage_client = None
_pinecone_index = None


def _get_voyage_client():
    global _voyage_client
    if _voyage_client is None:
        _voyage_client = voyageai.Client(api_key=VOYAGE_API_KEY)
    return _voyage_client


def _get_pinecone_index():
    global _pinecone_index
    if _pinecone_index is None:
        pc = Pinecone(api_key=PINECONE_API_KEY)
        _pinecone_index = pc.Index(INDEX_NAME)
    return _pinecone_index


def _raw_matches(query: str, top_k: int) -> list[dict]:
    # Step 1: turn the plain-text query into a vector VoyageAI understands.
    # input_type="query" tells the model this text is a SEARCH QUERY, not
    # a document being indexed — VoyageAI embeds those two roles slightly
    # differently internally for better matching quality.
    embedding = _get_voyage_client().embed(
        texts=[query],
        model="voyage-3-large",
        input_type="query",
    ).embeddings[0]

    # Step 2: ask Pinecone for the top_k resume bullets whose vectors are
    # closest to this query's vector. include_metadata=True is what gets
    # the actual bullet TEXT back, not just an ID and a similarity score.
    results = _get_pinecone_index().query(
        vector=embedding,
        top_k=top_k,
        include_metadata=True,
    )
    return results["matches"]


def search_resume_bullets(query: str, top_k: int = 5) -> str:
    """
    Semantically search resume bullets for the given query. Returns the
    top matches formatted as one "- bullet text (score: 0.xx)" per line.

    This is the exact shape the original tool returned to Claude, kept
    as-is since this becomes the search_resume_bullets MCP tool directly.
    """
    matches = _raw_matches(query, top_k)
    # Each match's "score" is a cosine similarity — closer to 1.0 means a
    # closer semantic match, not an exact keyword hit.
    lines = [
        f"- {match['metadata']['text']} (score: {match['score']:.2f})"
        for match in matches
    ]
    return "\n".join(lines)


def search_resume_bullets_raw(query: str, top_k: int = 5) -> list[dict]:
    """
    Same search, structured instead of pre-formatted: [{"bullet": ..., "score": ...}, ...]

    Used internally by the cover letter pipeline (tools/cover_letter.py),
    which needs the raw score values to build its prompt. This replaces
    the separate embed_query()/search_pinecone() pair that used to live in
    ai-job-assistant/phase1_matcher.py — same Pinecone index, one code path
    instead of two.
    """
    matches = _raw_matches(query, top_k)
    return [
        {"bullet": match["metadata"]["text"], "score": match["score"]}
        for match in matches
    ]


# This block only runs when you execute this file directly
# (python3 resume_search.py). When the MCP server imports this module
# instead, __name__ is "tools.resume_search", not "__main__", so this
# never fires — it's a manual smoke test, kept here (unlike
# company_research.py's fuller CLI, which got removed) because it's a
# single harmless line, not a whole argument-parsing script.
if __name__ == "__main__":
    print(search_resume_bullets("Oracle PL/SQL database developer"))