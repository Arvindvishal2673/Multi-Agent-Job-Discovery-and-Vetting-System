"""Offline test suite: mocks all LLM, file and network dependencies."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from job_hunter.agents.apify_agent import ApifyLinkedInAgent
from job_hunter.agents.resume_analyzer import ResumeAnalyzer
from job_hunter.agents.search_strategy import SearchStrategyAgent
from job_hunter.agents.vetting import MatchVettingAgent
from job_hunter.llm import extract_json
from job_hunter.agents.ranker import HybridRanker
from job_hunter.models import CandidateProfile, JobListing, JobSearchCriteria
from job_hunter.orchestrator import ResumeJobOrchestrator
from job_hunter.writer import write_excel


def make_profile() -> CandidateProfile:
    return CandidateProfile(
        summary="Backend engineer with API experience.",
        skills=["Python", "Django"],
        seniority="Senior",
        job_titles=["Senior Python Developer"],
        search_queries=["senior python developer remote"],
    )


class TestApifyAgent:
    def test_apify_skips_when_token_missing(self):
        with patch("job_hunter.config.APIFY_API_TOKEN", ""):
            agent = ApifyLinkedInAgent()
            results = agent.search(["python"])
            assert results == []

    def test_apify_parses_results_successfully(self):
        mock_run_data = {
            "data": {
                "id": "mock_run_123",
                "defaultDatasetId": "mock_dataset_456"
            }
        }
        mock_status_data = {
            "data": {
                "status": "SUCCEEDED"
            }
        }
        mock_items_data = [
            {
                "positionName": "AI Engineer",
                "companyName": "DeepMind",
                "location": "London",
                "jobUrl": "https://linkedin.com/jobs/view/999",
                "description": "Python PyTorch developer"
            }
        ]

        with patch("job_hunter.config.APIFY_API_TOKEN", "mock_token"), \
             patch("requests.post") as mock_post, \
             patch("requests.get") as mock_get, \
             patch("time.sleep"):  # bypass sleep delay
             
            # Mock trigger run POST
            mock_post_res = MagicMock()
            mock_post_res.json.return_value = mock_run_data
            mock_post.return_value = mock_post_res
            
            # Mock get status GET and get dataset GET
            mock_get_status_res = MagicMock()
            mock_get_status_res.json.return_value = mock_status_data
            
            mock_get_items_res = MagicMock()
            mock_get_items_res.json.return_value = mock_items_data
            
            mock_get.side_effect = [mock_get_status_res, mock_get_items_res]
            
            agent = ApifyLinkedInAgent()
            results = agent.search(["python"])
            
            assert len(results) == 1
            job = results[0]
            assert job.title == "AI Engineer"
            assert job.company == "DeepMind"
            assert job.location == "London"
            assert job.url == "https://linkedin.com/jobs/view/999"
            assert job.source == "LinkedIn"
            assert job.description == "Python PyTorch developer"

    def test_apify_handles_trigger_failure(self):
        with patch("job_hunter.config.APIFY_API_TOKEN", "mock_token"), \
             patch("requests.post") as mock_post:
             
            mock_post.side_effect = Exception("API connection timed out")
            agent = ApifyLinkedInAgent()
            results = agent.search(["python"])
            assert results == []



class TestResumeAnalyzer:
    def test_reads_text_resume(self, tmp_path):
        resume = tmp_path / "resume.txt"
        resume.write_text("Python developer with 5 years experience.")
        analyzer = ResumeAnalyzer(llm=MagicMock())
        assert "Python developer" in analyzer.extract_text(resume)

    def test_unsupported_format_raises(self, tmp_path):
        resume = tmp_path / "resume.docx"
        resume.write_text("hi")
        with pytest.raises(ValueError):
            ResumeAnalyzer(llm=MagicMock()).extract_text(resume)

    def test_analyze_builds_profile_from_llm_json(self, tmp_path):
        resume = tmp_path / "resume.md"
        resume.write_text("# Jane Doe\nPython, Django, AWS")
        llm = MagicMock()
        llm.chat.return_value = json.dumps(
            {
                "summary": "Experienced Python engineer.",
                "skills": ["Python", "Django", "AWS"],
                "seniority": "Senior",
                "job_titles": ["Python Developer"],
            }
        )
        profile = ResumeAnalyzer(llm=llm).analyze(resume)
        assert profile.seniority == "Senior"
        assert not profile.search_queries  # empty by default, populated later
        assert "Django" in profile.skills


class TestSearchStrategyAgent:
    def test_generate_queries_builds_queries_from_llm_json(self):
        llm = MagicMock()
        llm.chat.return_value = json.dumps(
            {
                "search_queries": [
                    "python developer",
                    "django engineer",
                    "aws backend",
                    "extra ignored",
                ]
            }
        )
        profile = CandidateProfile(
            summary="Experienced Python engineer.",
            skills=["Python", "Django", "AWS"],
            seniority="Senior",
            job_titles=["Python Developer"],
        )
        queries = SearchStrategyAgent(llm=llm).generate_queries(profile)
        assert len(queries) == 3  # capped at 3 queries
        assert queries[0] == "python developer"
        assert queries[2] == "aws backend"


class TestVettingAgent:
    def test_successful_evaluation_updates_listing(self):
        llm = MagicMock()
        llm.chat.return_value = json.dumps(
            {
                "fit_score": 88,
                "fit_decision": "Strong Fit",
                "fit_reasons": ["Python and Django match"],
                "gaps_identified": ["No Kubernetes experience"],
            }
        )
        listing = JobListing(title="Python Dev")
        MatchVettingAgent(llm).evaluate(make_profile(), listing)
        assert listing.fit_score == 88.0
        assert listing.fit_decision == "Strong Fit"
        assert listing.gaps_identified == ["No Kubernetes experience"]

    def test_falls_back_on_invalid_json(self):
        llm = MagicMock()
        llm.chat.return_value = "sorry, I cannot respond in JSON today"
        listing = JobListing(title="Python Dev")
        MatchVettingAgent(llm).evaluate(make_profile(), listing)
        assert listing.fit_score == 0.0
        assert listing.fit_decision == "Decent Fit"

    def test_invalid_decision_normalized(self):
        llm = MagicMock()
        llm.chat.return_value = json.dumps({"fit_score": 50, "fit_decision": "Maybe??"})
        listing = JobListing(title="Python Dev")
        MatchVettingAgent(llm).evaluate(make_profile(), listing)
        assert listing.fit_decision == "Decent Fit"


class TestOrchestratorLogic:
    def test_deduplicates_by_normalized_url(self):
        jobs = [
            JobListing(title="A", url="https://x.com/j/1"),
            JobListing(title="B", url="https://x.com/j/1/"),
            JobListing(title="C", url="https://x.com/j/2"),
        ]
        assert len(ResumeJobOrchestrator.deduplicate(jobs)) == 2

    def test_prefilter_ranks_relevant_jobs_first(self):
        jobs = [
            JobListing(title="Bakery Assistant", description="bread and pastries"),
            JobListing(title="Senior Python Developer", description="Django REST APIs"),
        ]
        ranker = HybridRanker()
        ranked = ranker.rank(jobs, make_profile(), JobSearchCriteria(), top_k=10)
        assert ranked[0].title == "Senior Python Developer"

    def test_prefilter_remote_only_filters_offices(self):
        jobs = [
            JobListing(title="Python Developer", location="Berlin office"),
            JobListing(title="Python Developer", location="Remote"),
        ]
        ranker = HybridRanker()
        ranked = ranker.rank(
            jobs, make_profile(), JobSearchCriteria(remote_only=True), top_k=10
        )
        assert ranked
        assert all("remote" in job.location.lower() for job in ranked)

    def test_prefilter_min_salary_filters_low_salaries(self):
        jobs = [
            JobListing(title="Junior Python Dev", description="Python", salary="$40,000"),
            JobListing(title="Senior Python Dev", description="Python", salary="$120,000"),
            JobListing(title="Lead Python Dev", description="Python", salary=""),  # empty kept
        ]
        ranker = HybridRanker()
        ranked = ranker.rank(
            jobs, make_profile(), JobSearchCriteria(min_salary=80000), top_k=10
        )
        titles = [j.title for j in ranked]
        assert "Junior Python Dev" not in titles
        assert "Senior Python Dev" in titles
        assert "Lead Python Dev" in titles


class TestUtilities:
    def test_extract_json_from_noisy_llm_response(self):
        assert extract_json('Here you go:\n{"a": 1}\nThanks!')["a"] == 1

    def test_extract_json_raises_without_object(self):
        with pytest.raises(ValueError):
            extract_json("no json here")

    def test_write_excel_creates_styled_file(self, tmp_path):
        job = JobListing(
            title="Python Dev",
            company="Acme",
            url="https://x.com/1",
            fit_score=90,
            fit_decision="Strong Fit",
            fit_reasons=["Great skill match"],
            gaps_identified=["None significant"],
        )
        out = write_excel(make_profile(), [job], str(tmp_path / "out.xlsx"))
        assert Path(out).exists()

    def test_get_default_llm_selects_nvidia_when_key_present(self):
        with patch("job_hunter.config.NVIDIA_API_KEY", "mock_nvidia_key"):
            from job_hunter.llm import get_default_llm, NvidiaLLM
            llm = get_default_llm()
            assert isinstance(llm, NvidiaLLM)

    def test_nvidia_llm_chat_calls_api(self):
        with patch("job_hunter.config.NVIDIA_API_KEY", "mock_nvidia_key"):
            from job_hunter.llm import NvidiaLLM
            llm = NvidiaLLM()
            mock_chunk = MagicMock()
            mock_chunk.choices = [MagicMock()]
            mock_chunk.choices[0].delta.content = "Hello from NVIDIA NIM"
            mock_chunk.choices[0].delta.reasoning_content = None
            with patch.object(llm.client.chat.completions, "create", return_value=[mock_chunk]):
                reply = llm.chat("system", "user")
                assert reply == "Hello from NVIDIA NIM"

