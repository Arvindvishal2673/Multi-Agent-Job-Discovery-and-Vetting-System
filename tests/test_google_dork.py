"""Unit tests for GoogleATSDorkAgent."""

from unittest.mock import MagicMock, patch
from job_hunter.agents.google_dork_agent import GoogleATSDorkAgent
from job_hunter.models import JobListing


def test_google_dork_agent_initialization():
    agent = GoogleATSDorkAgent()
    assert agent.name == "google_ats_dork"


@patch("job_hunter.agents.google_dork_agent.requests.get")
def test_google_dork_agent_search_parses_results(mock_get):
    mock_resp = MagicMock()
    mock_resp.ok = True
    mock_resp.status_code = 200
    mock_resp.text = """
    <html>
      <body>
        <a class="result__url" href="https%3A%2F%2Fboards.greenhouse.io%2Fswiggy%2Fjobs%2F12345">boards.greenhouse.io/swiggy/jobs/12345</a>
        <a class="result__a" href="https://boards.greenhouse.io/swiggy/jobs/12345">Senior Software Engineer - Swiggy</a>
      </body>
    </html>
    """
    mock_get.return_value = mock_resp

    agent = GoogleATSDorkAgent()
    results = agent.search(["Software Engineer"], max_results=5)

    assert len(results) >= 1
    listing = results[0]
    assert isinstance(listing, JobListing)
    assert "Greenhouse" in listing.source
    assert listing.url == "https://boards.greenhouse.io/swiggy/jobs/12345"
    assert "Swiggy" in listing.company or "Swiggy" in listing.title
