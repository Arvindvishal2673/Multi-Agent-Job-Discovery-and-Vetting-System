"""Central Orchestrator implementing the full agentic pipeline.

Pipeline phases:
  1.   Resume → CandidateProfile  (ResumeAnalyzer)
  1.5  Profile → Search Queries   (SearchStrategyAgent)
  2a.  Profile → Source Selection (PlannerAgent — LLM tool-calling)
  2.   Parallel job ingestion from LLM-selected sources
  2b.  ReAct loop: Observe results → Reason → Act (refine or done)
  3.   Dedupe → Pre-filter → Parallel LLM vetting (MatchVettingAgent)
  4.   Styled Excel export
"""

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List

from . import config
from .agents.api_agents import AdzunaAgent, ArbeitnowAgent, RemoteOKAgent, RemotiveAgent
from .agents.apify_agent import ApifyLinkedInAgent
from .agents.ats_agent import DirectATSAgent
from .agents.google_dork_agent import GoogleATSDorkAgent
from .agents.planner import PlannerAgent
from .agents.ranker import HybridRanker
from .agents.reflector import ReflectionAgent, MAX_REACT_ITERATIONS
from .agents.resume_analyzer import ResumeAnalyzer
from .agents.search_strategy import SearchStrategyAgent
from .agents.vetting import MatchVettingAgent
from .models import CandidateProfile, JobListing, JobSearchCriteria
from .writer import write_excel

log = logging.getLogger(__name__)


class ResumeJobOrchestrator:
    """Coordinates all agents via the blackboard state-passing pattern."""

    def __init__(self, llm=None):
        if llm is None:
            from .llm import get_default_llm
            llm = get_default_llm()
        self.llm = llm
        self.blackboard = {"criteria": None, "profile": None, "jobs": [], "metrics": {}}
        self.analyzer = ResumeAnalyzer(self.llm)
        self.strategy_agent = SearchStrategyAgent(self.llm)
        self.planner = PlannerAgent(self.llm)
        self.reflector = ReflectionAgent(self.llm)
        self.vetter = MatchVettingAgent(self.llm)
        self.ranker = HybridRanker()  # Zero-dependency BM25+TF-IDF+RRF hybrid ranker

    def _build_all_sources(self, target_india_only: bool) -> list:
        """Construct the full pool of source agents the planner can choose from."""
        if target_india_only:
            return [
                ApifyLinkedInAgent(target_india_only=True),
                AdzunaAgent(),
                DirectATSAgent(),
                GoogleATSDorkAgent(),
            ]
        return [
            ApifyLinkedInAgent(target_india_only=False),
            RemotiveAgent(),
            RemoteOKAgent(),
            ArbeitnowAgent(),
            AdzunaAgent(),
            DirectATSAgent(),
            GoogleATSDorkAgent(),
        ]

    def _run_sources_parallel(
        self,
        sources: list,
        queries: List[str],
        max_results: int,
        posted_within_days: int = 1,
    ) -> List[JobListing]:
        """Run a set of source agents in parallel and collect all results."""
        jobs: List[JobListing] = []
        def _call_search(src):
            try:
                import inspect
                sig = inspect.signature(src.search)
                if "posted_within_days" in sig.parameters:
                    return src.search(queries, max_results=max_results, posted_within_days=posted_within_days)
                return src.search(queries, max_results=max_results)
            except Exception:
                return src.search(queries, max_results=max_results)

        with ThreadPoolExecutor(max_workers=max(len(sources), 1)) as pool:
            futures = {
                pool.submit(_call_search, source): source
                for source in sources
            }
            for future in as_completed(futures):
                source = futures[future]
                try:
                    found = future.result()
                    log.info("%s returned %d listings", source.name, len(found))
                    jobs.extend(found)
                except Exception as exc:
                    log.warning("Source %s failed: %s", source.name, exc)
        return jobs

    def run(
        self,
        resume_path: str,
        criteria: JobSearchCriteria = None,
        max_evals: int = config.MAX_EVALS_DEFAULT,
        output_path: str = "outputs/job_matches.xlsx",
    ) -> dict:
        started = time.time()
        criteria = criteria or JobSearchCriteria()
        self.blackboard["criteria"] = criteria

        # ── Phase 1: Resume → CandidateProfile ───────────────────────────────
        log.info("📄 Reading resume and extracting candidate profile...")
        profile: CandidateProfile = self.analyzer.analyze(resume_path)
        log.info("👤 Candidate Profile: %s Seniority | Skills: %s", profile.seniority, ", ".join(profile.skills[:8]))

        # ── Phase 1.5: Profile → Search Queries ──────────────────────────────
        log.info("🎯 Formulating optimal job search queries...")
        profile.search_queries = self.strategy_agent.generate_queries(profile)
        self.blackboard["profile"] = profile
        queries = profile.search_queries or profile.job_titles or criteria.keywords
        log.info("💡 Target Search Queries: %s", queries)

        # ── Phase 2a: PlannerAgent selects which sources to activate ─────────
        log.info("🗺️ Selecting best job platforms to search...")
        all_sources = self._build_all_sources(criteria.target_india_only)
        active_sources = self.planner.select_sources(
            profile, all_sources, criteria.target_india_only
        )
        source_names = [getattr(s, 'name', type(s).__name__).replace('_', ' ').title() for s in active_sources]
        profile.activated_sources = [type(s).__name__ for s in active_sources]
        log.info(
            "✅ Activated %d job platforms: %s",
            len(active_sources), source_names,
        )

        # ── Phase 2: Parallel ingestion with ReAct refinement loop ───────────
        log.info("⚡ Searching job platforms in parallel (Posted within %d days)...", criteria.posted_within_days or 1)
        jobs: List[JobListing] = self._run_sources_parallel(
            active_sources, queries, criteria.max_results_per_source, posted_within_days=criteria.posted_within_days
        )
        log.info("📊 Ingested %d initial job listings.", len(jobs))

        # ── Phase 2b: ReAct Observe → Reason → Act loop ──────────────────────
        for iteration in range(MAX_REACT_ITERATIONS):
            log.info("🔄 Checking search result quality (Round %d/%d)...", iteration + 1, MAX_REACT_ITERATIONS)
            action, new_queries = self.reflector.reflect(
                profile, queries, jobs, iteration
            )
            profile.react_iterations = iteration + 1

            if action == "done":
                log.info("✅ Result quality verified. Proceeding to evaluation.")
                break

            # action == "refine": update queries and search again
            log.info(
                "💡 Refining search keywords for better matches: %s", new_queries
            )
            queries = new_queries
            profile.search_queries = new_queries
            new_jobs = self._run_sources_parallel(
                active_sources, queries, criteria.max_results_per_source, posted_within_days=criteria.posted_within_days
            )
            jobs.extend(new_jobs)
            log.info(
                "📈 Collected %d additional listings (%d total so far).",
                len(new_jobs), len(jobs),
            )
        else:
            log.info("🛑 Completed maximum search refinement cycles (%d).", MAX_REACT_ITERATIONS)

        # Sort aggregated job listings by posting timestamp (newest first)
        jobs.sort(key=lambda j: j.posted_timestamp or 0.0, reverse=True)

        # ── Phase 3: Dedupe → Hybrid Rank → Deep LLM Vetting ────────────────
        jobs = self.deduplicate(jobs)
        max_evals_int = int(max_evals)
        # Pass 1 + 2: Hard Guardrails → BM25 + TF-IDF Cosine + RRF ranking (returns top_k × 1.5 buffer)
        candidates_to_vet = self.ranker.rank(jobs, profile, criteria, top_k=max_evals_int)
        # Pass 3: Deep LLM vetting on Top-K × buffer, then slice top max_evals by fit_score
        log.info(
            "🔬 Hybrid Ranker selected Top-%d from %d unique listings for deep LLM vetting...",
            len(candidates_to_vet), len(jobs),
        )
        with ThreadPoolExecutor(max_workers=1) as pool:
            list(pool.map(lambda job: self.vetter.evaluate(profile, job), candidates_to_vet))
        
        # Sort by LLM fit_score descending and select the top max_evals requested
        candidates_to_vet.sort(key=lambda job: job.fit_score, reverse=True)
        final_candidates = candidates_to_vet[:max_evals_int]
        self.blackboard["jobs"] = final_candidates

        # ── Phase 4: Styled Excel export ─────────────────────────────────────
        log.info("📊 Generating Excel report...")
        try:
            path = write_excel(profile, final_candidates, output_path)
            log.info("✅ Report saved successfully to %s", path)
        except Exception as exc:
            log.warning("Could not write Excel report: %s. Continuing without saving.", exc)
            path = ""

        metrics = {
            "total_found": len(jobs),
            "evaluated": len(candidates_to_vet),
            "strong_fits": sum(1 for j in final_candidates if j.fit_decision == "Strong Fit"),
            "elapsed_seconds": round(time.time() - started, 1),
            "output_path": path,
            "activated_sources": profile.activated_sources,
            "react_iterations": profile.react_iterations,
        }
        self.blackboard["metrics"] = metrics
        log.info("🎉 Job Search Complete! Found: %d jobs | Evaluated: %d | Strong Fits: %d | Time: %.1fs", metrics["total_found"], metrics["evaluated"], metrics["strong_fits"], metrics["elapsed_seconds"])
        self.blackboard["metrics"] = metrics
        return {"profile": profile, "jobs": final_candidates, "metrics": metrics, "output_path": path}

    @staticmethod
    def deduplicate(jobs: List[JobListing]) -> List[JobListing]:
        """Drop listings that share the same URL (or title+company when URL is missing)."""
        seen, unique = set(), []
        for job in jobs:
            key = job.url.rstrip("/").lower() or (job.title.lower(), job.company.lower())
            if key in seen:
                continue
            seen.add(key)
            unique.append(job)
        return unique
