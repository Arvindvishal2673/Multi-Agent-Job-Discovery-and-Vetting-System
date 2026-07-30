"""Offline unit tests for TailorAgent.

All LLM calls are mocked — no API key required.
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from job_hunter.agents.tailor_agent import TailorAgent
from job_hunter.models import CandidateProfile, JobListing, TailoredApplicationPackage


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_profile():
    return CandidateProfile(
        summary="Senior ML Engineer with 6 years building LLM-based products.",
        skills=["Python", "PyTorch", "TensorFlow", "LangChain", "FastAPI", "AWS"],
        seniority="Senior",
        job_titles=["ML Engineer", "AI Engineer", "Research Engineer"],
        search_queries=["senior ML engineer remote", "AI engineer LLM"],
        raw_text="Experienced ML engineer...",
    )


@pytest.fixture
def sample_job():
    return JobListing(
        title="Senior AI Engineer",
        company="Acme AI Corp",
        location="Remote",
        url="https://example.com/job/123",
        source="LinkedIn",
        description="We need a senior AI engineer with strong LLM experience and Python.",
        fit_score=88.0,
        fit_decision="Strong Fit",
        fit_reasons=["Strong LLM experience", "Python expert"],
        gaps_identified=["No Go experience"],
    )


@pytest.fixture
def mock_llm_response():
    return json.dumps({
        "cover_letter": "Dear Hiring Manager at Acme AI Corp,\n\nI am excited to apply...",
        "outreach_message": "Hi team, I saw your opening for Senior AI Engineer...",
        "tailored_summary": "Senior ML Engineer with 6 years of LLM product experience.",
        "key_highlights": [
            "6 years building LLM pipelines aligned with Acme AI's core product",
            "Expert in Python, PyTorch, and FastAPI — exactly your stack",
            "Led end-to-end AI model deployment on AWS at scale",
        ],
    })


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestTailorAgent:

    def test_successful_tailor_returns_package(self, sample_profile, sample_job, mock_llm_response):
        """TailorAgent should return a fully populated TailoredApplicationPackage on success."""
        mock_llm = MagicMock()
        mock_llm.chat.return_value = mock_llm_response

        agent = TailorAgent(llm=mock_llm)
        package = agent.tailor(sample_profile, sample_job)

        assert isinstance(package, TailoredApplicationPackage)
        assert "Acme AI Corp" in package.cover_letter
        assert "Senior AI Engineer" in package.outreach_message or len(package.outreach_message) > 0
        assert len(package.tailored_summary) > 0
        assert len(package.key_highlights) == 3

    def test_cover_letter_is_non_empty(self, sample_profile, sample_job, mock_llm_response):
        """Cover letter should be a non-empty string."""
        mock_llm = MagicMock()
        mock_llm.chat.return_value = mock_llm_response

        agent = TailorAgent(llm=mock_llm)
        package = agent.tailor(sample_profile, sample_job)

        assert isinstance(package.cover_letter, str)
        assert len(package.cover_letter.strip()) > 0

    def test_key_highlights_is_list_of_strings(self, sample_profile, sample_job, mock_llm_response):
        """key_highlights should be a list of non-empty strings."""
        mock_llm = MagicMock()
        mock_llm.chat.return_value = mock_llm_response

        agent = TailorAgent(llm=mock_llm)
        package = agent.tailor(sample_profile, sample_job)

        assert isinstance(package.key_highlights, list)
        for h in package.key_highlights:
            assert isinstance(h, str)
            assert len(h.strip()) > 0

    def test_llm_called_with_correct_messages(self, sample_profile, sample_job, mock_llm_response):
        """TailorAgent must call llm.chat with system and user messages containing job/profile info."""
        mock_llm = MagicMock()
        mock_llm.chat.return_value = mock_llm_response

        agent = TailorAgent(llm=mock_llm)
        agent.tailor(sample_profile, sample_job)

        mock_llm.chat.assert_called_once()
        call_kwargs = mock_llm.chat.call_args
        # Both system and user should be passed
        assert call_kwargs is not None
        # User message should reference the job title and company
        user_msg = call_kwargs.kwargs.get("user") or call_kwargs.args[1]
        assert "Senior AI Engineer" in user_msg
        assert "Acme AI Corp" in user_msg

    def test_fallback_package_on_llm_error(self, sample_profile, sample_job):
        """When LLM raises an exception, TailorAgent should return a graceful fallback package."""
        mock_llm = MagicMock()
        mock_llm.chat.side_effect = RuntimeError("API unreachable")

        agent = TailorAgent(llm=mock_llm)
        package = agent.tailor(sample_profile, sample_job)

        # Fallback must still return a valid TailoredApplicationPackage
        assert isinstance(package, TailoredApplicationPackage)
        assert len(package.cover_letter) > 0
        assert len(package.outreach_message) > 0
        assert len(package.tailored_summary) > 0
        assert isinstance(package.key_highlights, list)

    def test_fallback_on_invalid_json_response(self, sample_profile, sample_job):
        """When LLM returns non-JSON garbage, TailorAgent should fall back gracefully."""
        mock_llm = MagicMock()
        mock_llm.chat.return_value = "Sorry, I cannot help with that."

        agent = TailorAgent(llm=mock_llm)
        package = agent.tailor(sample_profile, sample_job)

        assert isinstance(package, TailoredApplicationPackage)
        assert len(package.cover_letter) > 0

    def test_partial_json_fills_defaults(self, sample_profile, sample_job):
        """When LLM returns partial JSON, missing fields should default to empty strings/lists."""
        mock_llm = MagicMock()
        mock_llm.chat.return_value = json.dumps({
            "cover_letter": "Dear team, I am applying...",
            # outreach_message, tailored_summary, key_highlights all missing
        })

        agent = TailorAgent(llm=mock_llm)
        package = agent.tailor(sample_profile, sample_job)

        assert package.cover_letter == "Dear team, I am applying..."
        assert package.outreach_message == ""
        assert package.tailored_summary == ""
        assert package.key_highlights == []

    def test_profile_skills_are_included_in_prompt(self, sample_profile, sample_job, mock_llm_response):
        """Candidate skills should appear in the user prompt sent to LLM."""
        mock_llm = MagicMock()
        mock_llm.chat.return_value = mock_llm_response

        agent = TailorAgent(llm=mock_llm)
        agent.tailor(sample_profile, sample_job)

        call_kwargs = mock_llm.chat.call_args
        user_msg = call_kwargs.kwargs.get("user") or call_kwargs.args[1]
        # At least some skills should be in the prompt
        assert any(skill in user_msg for skill in sample_profile.skills[:5])

    def test_uses_default_llm_when_none_provided(self):
        """TailorAgent should call get_default_llm() when no LLM is injected."""
        with patch("job_hunter.agents.tailor_agent.get_default_llm") as mock_get_llm:
            mock_llm = MagicMock()
            mock_llm.chat.return_value = json.dumps({
                "cover_letter": "Hi",
                "outreach_message": "Hello",
                "tailored_summary": "Summary",
                "key_highlights": ["Point A"],
            })
            mock_get_llm.return_value = mock_llm

            agent = TailorAgent()
            mock_get_llm.assert_called_once()


class TestTailoredApplicationPackageModel:

    def test_default_values(self):
        """TailoredApplicationPackage should have correct defaults."""
        pkg = TailoredApplicationPackage()
        assert pkg.cover_letter == ""
        assert pkg.outreach_message == ""
        assert pkg.tailored_summary == ""
        assert pkg.key_highlights == []

    def test_all_fields_set(self):
        """TailoredApplicationPackage should hold all four fields correctly."""
        pkg = TailoredApplicationPackage(
            cover_letter="Dear Team...",
            outreach_message="Hi Recruiter...",
            tailored_summary="Experienced engineer with LLM background.",
            key_highlights=["Bullet A", "Bullet B"],
        )
        assert pkg.cover_letter == "Dear Team..."
        assert pkg.outreach_message == "Hi Recruiter..."
        assert pkg.tailored_summary == "Experienced engineer with LLM background."
        assert pkg.key_highlights == ["Bullet A", "Bullet B"]


class TestTailorEndpointIntegration:

    def test_tailor_endpoint_missing_session(self):
        """POST /api/tailor-application should return 404 for unknown session_id."""
        from fastapi.testclient import TestClient
        from server import app
        client = TestClient(app)

        response = client.post("/api/tailor-application", data={
            "session_id": "non-existent-session-xyz",
            "job_index": 0,
        })
        assert response.status_code == 404
        assert "Session not found" in response.json()["detail"]

    def test_tailor_endpoint_no_result_yet(self):
        """POST /api/tailor-application should return 400 when session exists but result is None."""
        from fastapi.testclient import TestClient
        from server import app, SESSIONS
        client = TestClient(app)

        # Inject a fake running session
        SESSIONS["test-running-session"] = {
            "id": "test-running-session",
            "status": "running",
            "result": None,
            "log_queue": __import__("queue").Queue(),
            "logs": [],
            "error": None,
        }

        response = client.post("/api/tailor-application", data={
            "session_id": "test-running-session",
            "job_index": 0,
        })
        assert response.status_code == 400
        assert "not completed" in response.json()["detail"].lower()
