"""ReflectionAgent: implements the Observe-Reason-Act (ReAct) loop.

After each search round, this agent observes the collected job listings,
reasons about their quality and relevance, and acts by either:
  - "done"   → results are good enough, proceed to vetting.
  - "refine" → generate better search queries and trigger another round.

This is the second genuinely agentic component. The LLM is not just
generating text; it is making a control-flow decision that affects
which code path runs next.
"""

import logging
from typing import List, Tuple

from ..llm import extract_json
from ..models import CandidateProfile, JobListing

log = logging.getLogger(__name__)

MAX_REACT_ITERATIONS = 2  # Maximum search-reflect cycles (2 for speed + quality)

REFLECT_SYSTEM = """You are a critical job search quality evaluator operating in a ReAct (Reason + Act) loop.

You will be given:
1. The candidate's profile and original search queries.
2. A sample of job listings found so far.

Your task is to evaluate whether these results are relevant and sufficient.

You MUST respond with only a valid JSON object. No prose, no explanation outside the JSON.

Decision rules:
- Return "done" if: at least 10 listings were found AND most titles are relevant to the candidate's skills.
- Return "refine" if: fewer than 10 results, OR most titles are irrelevant (wrong domain/seniority), OR results are all from only one source.
- When refining, generate 3 NEW search queries that are different from the original ones. Be more specific or try alternative terminology.

Response format:
{
  "thought": "One sentence reasoning about the quality of results.",
  "action": "done" or "refine",
  "new_queries": ["query1", "query2", "query3"]  // only required when action is "refine"
}
"""

REFLECT_USER_TEMPLATE = """Candidate Profile:
- Summary: {summary}
- Skills: {skills}
- Seniority: {seniority}
- Target Titles: {job_titles}

Original Search Queries Used:
{queries}

Search Results So Far ({count} total listings):
{sample}

Evaluate: are these results relevant and sufficient? Should we refine and search again?"""


class ReflectionAgent:
    """Implements the Observe-Reason-Act (ReAct) loop for iterative search refinement.

    The agent inspects job listing quality after each search round and
    decides autonomously whether to refine queries and re-search.
    """

    def __init__(self, llm):
        self.llm = llm

    def reflect(
        self,
        profile: CandidateProfile,
        queries: List[str],
        jobs: List[JobListing],
        iteration: int,
    ) -> Tuple[str, List[str]]:
        """Observe the current search results and decide the next action.

        Args:
            profile:   The candidate profile from the blackboard.
            queries:   The queries used in the current round.
            jobs:      All job listings collected so far.
            iteration: Current iteration index (0-based).

        Returns:
            A tuple of (action, new_queries) where:
              - action is "done" or "refine"
              - new_queries is a list of strings (empty if action == "done")
        """
        # Build a compact sample of up to 15 job titles and companies
        sample_lines = [
            f"  - [{j.source}] {j.title} @ {j.company or 'Unknown'} ({j.location or 'N/A'})"
            for j in jobs[:15]
        ]
        sample_text = "\n".join(sample_lines) if sample_lines else "  (no listings found)"

        user_msg = REFLECT_USER_TEMPLATE.format(
            summary=profile.summary,
            skills=", ".join(profile.skills[:12]),
            seniority=profile.seniority,
            job_titles=", ".join(profile.job_titles),
            queries="\n".join(f"  - {q}" for q in queries),
            count=len(jobs),
            sample=sample_text,
        )

        # Build multi-turn message history for the ReAct loop
        messages = [
            {"role": "system", "content": REFLECT_SYSTEM},
            {"role": "user", "content": user_msg},
        ]

        try:
            response = self.llm.chat_multi_turn(messages, temperature=0.1)
            data = extract_json(response)
        except Exception as exc:
            log.warning("ReflectionAgent failed to parse LLM response: %s. Defaulting to 'done'.", exc)
            return "done", []

        action = str(data.get("action", "done")).strip().lower()
        thought = str(data.get("thought", ""))
        new_queries = [str(q).strip() for q in data.get("new_queries", [])][:3]

        log.info(
            "ReAct iteration %d → action=%s | thought: %s",
            iteration + 1, action, thought,
        )

        if action == "refine" and new_queries:
            log.info("ReAct refinement queries: %s", new_queries)
            return "refine", new_queries

        return "done", []
