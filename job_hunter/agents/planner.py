"""PlannerAgent: uses Chain-of-Thought (CoT) LLM reasoning to select job sources.

Instead of jumping blindly to a single tool call, the LLM evaluates the candidate's
profile against EVERY available job source step-by-step, writes an evaluation,
and activates ALL matching sources (minimum 2 sources).
"""

import json
import logging
from typing import List

from ..llm import extract_json
from ..models import CandidateProfile
from .base import JobSourceAgent

log = logging.getLogger(__name__)

# Chain-of-Thought System Prompt
PLANNER_SYSTEM = """You are an expert job search planning agent operating with Chain-of-Thought reasoning.

Your task:
Evaluate the candidate's profile against EVERY available job source API, write a brief evaluation for each, and activate ALL matching job sources.

AVAILABLE JOB SOURCES:
1. ApifyLinkedInAgent: Essential for AI, ML, Data Science, Software Engineering, India tech, and global roles.
2. DirectATSAgent: Essential for corporate career pages (Greenhouse, Lever), product startups, and entry/junior engineering roles.
3. GoogleATSDorkAgent: Essential for Google Dork searches across direct ATS portals (Greenhouse, Lever, Workday, iCIMS, Ashby).
4. AdzunaAgent: Excellent for India tech positions, mid-level engineering, and data roles.
5. RemotiveAgent: Excellent for global remote-first software & AI roles.
6. RemoteOKAgent: Excellent supplement for remote developer & ML positions.
7. ArbeitnowAgent: Good for European tech roles and relocation opportunities.

SELECTION RULES:
- You MUST evaluate EVERY available source in the "evaluations" object.
- You MUST activate EVERY source that has non-zero matching potential for the candidate.
- You MUST activate AT LEAST 2 sources whenever 2 or more sources are available.
- You MUST respond with ONLY a valid JSON object matching the format below.

RESPONSE FORMAT (JSON ONLY):
{
  "evaluations": {
    "ApifyLinkedInAgent": "Explain why this matches or does not match...",
    "DirectATSAgent": "Explain why this matches or does not match...",
    "AdzunaAgent": "Explain why this matches or does not match..."
  },
  "activated_sources": [
    "ApifyLinkedInAgent",
    "DirectATSAgent"
  ]
}
"""


class PlannerAgent:
    """Phase 2a: uses Chain-of-Thought LLM reasoning to select job source agents."""

    def __init__(self, llm):
        self.llm = llm

    def select_sources(
        self,
        profile: CandidateProfile,
        all_sources: List[JobSourceAgent],
        target_india_only: bool = False,
    ) -> List[JobSourceAgent]:
        """Ask the LLM to evaluate all available sources step-by-step and select matching ones."""
        location_hint = "India only" if target_india_only else "global / remote"
        source_details = []
        for s in all_sources:
            cls_name = type(s).__name__
            name = getattr(s, "name", cls_name)
            source_details.append(f"  - {cls_name} (key: {name})")

        sources_str = "\n".join(source_details)

        user_msg = f"""Candidate Profile for Evaluation:
- Summary: {profile.summary}
- Skills: {', '.join(profile.skills[:15])}
- Seniority: {profile.seniority}
- Target Job Titles: {', '.join(profile.job_titles)}
- Search Queries: {', '.join(profile.search_queries)}
- Location Preference: {location_hint}

Available Job Sources to Evaluate:
{sources_str}

Evaluate EACH of these available job sources step-by-step, write an evaluation for each, and list all matching sources in "activated_sources"."""

        try:
            raw_response = self.llm.chat(system=PLANNER_SYSTEM, user=user_msg, temperature=0.1)
            data = extract_json(raw_response)
            
            # Log Chain-of-Thought reasoning for transparency
            evaluations = data.get("evaluations", {})
            if isinstance(evaluations, dict) and evaluations:
                log.info("🧠 [CoT Source Reasoning] LLM evaluated %d sources:", len(evaluations))
                for src_name, reasoning in evaluations.items():
                    log.info("   • %s: %s", src_name, reasoning)

            selected_class_names = set(data.get("activated_sources", []))
            log.info("📋 LLM initial source picks: %s", sorted(selected_class_names))

        except Exception as exc:
            log.warning("PlannerAgent Chain-of-Thought parsing failed: %s. Using all sources.", exc)
            return all_sources

        # Match class names back to source instances
        selected = [s for s in all_sources if type(s).__name__ in selected_class_names]

        # Enforce minimum diversity rule: at least 2 sources if available
        if len(selected) < 2 and len(all_sources) >= 2:
            log.info("⚡ Enforcing minimum 2 sources for search diversity. Adding fallback source.")
            for s in all_sources:
                if s not in selected:
                    selected.append(s)
                    if len(selected) >= 2:
                        break

        final_names = [type(s).__name__ for s in selected]
        log.info("✅ Final activated job sources (%d): %s", len(selected), final_names)
        return selected if selected else all_sources
