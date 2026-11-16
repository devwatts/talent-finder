"""MCP server: find and rank LinkedIn candidates for a job description, from inside Claude Code.

    uv run --env-file .env python -m talent.server
"""
from mcp.server.mcpserver import MCPServer

from talent import pipeline

INSTRUCTIONS = """Find and rank LinkedIn candidates for a job description.
Usual order: start_search, ask the user its open questions, draft_rubric with their answers, probe,
show the borderline profiles and ask yes or no on each, calibrate, run_search, then get_results
until it is done. probe and calibrate can be skipped for a quick result. probe and run_search
spend Apify credit: tell the user the estimated cost first. draft_rubric returns the estimates."""

mcp = MCPServer("talent-finder", instructions=INSTRUCTIONS, log_level="WARNING")


@mcp.tool()
def start_search(jd_text: str, company_website: str | None = None) -> dict:
    """Begin a search from a job description. Returns a search_id, what the role involves, its
    deal-breakers, and the open questions to put to the user before drafting the rubric."""
    return pipeline.start_search(jd_text, company_website)


@mcp.tool()
def draft_rubric(search_id: int, answers: str = "") -> dict:
    """Draft the rubric and LinkedIn search filters, using the user's answers to the open questions.
    Call again with feedback to revise. Returns the rubric, anything the bias check removed, and
    what a search of 25, 50 or 100 profiles would cost."""
    return pipeline.draft_rubric(search_id, answers)


@mcp.tool()
def probe(search_id: int, size: int = 15) -> dict:
    """Run a small LinkedIn search and score it with the current rubric. Returns borderline
    profiles for the user to judge, and what a full run would cost. Costs about $0.16."""
    return pipeline.probe(search_id, size)


@mcp.tool()
def calibrate(search_id: int, verdicts: list[dict]) -> dict:
    """Adjust the rubric from the user's verdicts on probe profiles. Each verdict is
    {"profile_id": str, "good_fit": bool, "note": optional str}. Returns what changed."""
    return pipeline.calibrate(search_id, verdicts)


@mcp.tool()
def run_search(search_id: int, max_profiles: int = 50) -> dict:
    """Freeze the rubric and start the full search in the background: search LinkedIn, fetch
    full profiles, score each one. Returns at once with the estimated cost; poll get_results."""
    return pipeline.run_search(search_id, max_profiles)


@mcp.tool()
def get_results(search_id: int, top: int = 10, wait_seconds: int = 30) -> dict:
    """Progress of a full run and, once scored, the ranked candidates with a reason for each.
    While the search is still running this waits up to wait_seconds before answering, so call
    it again until the status is done; there is no need to sleep in between."""
    return pipeline.get_results(search_id, top, wait_seconds)


@mcp.tool()
def get_candidate(search_id: int, profile_id: str) -> dict:
    """Everything behind one candidate's score: each criterion, how strongly the profile shows it,
    and the quoted evidence."""
    return pipeline.get_candidate(search_id, profile_id)


if __name__ == "__main__":
    mcp.run()
