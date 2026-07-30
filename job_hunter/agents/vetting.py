"""Job Match & Vetting agent: the LLM evaluation \"brain\"."""

import logging

from ..llm import extract_json
from ..models import CandidateProfile, JobListing

log = logging.getLogger(__name__)

EVAL_SYSTEM = (
    "You are a rigorous hiring manager. Assess how well a candidate fits a job "
    "posting and respond with only a valid JSON object, no prose."
)

EVAL_PROMPT = """Candidate profile:
Summary: {summary}
Seniority: {seniority}
Skills: {skills}

Job posting:
Title: {title}
Company: {company}
Location: {location}
Source: {source}
Description: {description}

Return ONLY a JSON object with these keys:
- "fit_score": number between 0 and 100
- "fit_decision": one of "Strong Fit", "Decent Fit", "Weak Fit"
- "fit_reasons": list of up to 5 short reasons the candidate matches
- "gaps_identified": list of up to 5 requirements in the posting the candidate appears to lack
"""

VALID_DECISIONS = {"Strong Fit", "Decent Fit", "Weak Fit"}
FALLBACK_DECISION = "Decent Fit"


class MatchVettingAgent:
    """Evaluates a single JobListing against the CandidateProfile in-place."""

    def __init__(self, llm):
        self.llm = llm

    def evaluate(self, profile: CandidateProfile, listing: JobListing) -> JobListing:
        import time
        time.sleep(4.0)  # Pace evaluation requests to stay strictly under 15 RPM Free Tier limit
        prompt = EVAL_PROMPT.format(
            summary=profile.summary,
            seniority=profile.seniority,
            skills=", ".join(profile.skills),
            title=listing.title,
            company=listing.company or "Unknown",
            location=listing.location or "Unknown",
            source=listing.source,
            description=listing.description[:1500],
        )
        try:
            data = extract_json(self.llm.chat(system=EVAL_SYSTEM, user=prompt))
            listing.fit_score = float(data.get("fit_score", 0.0))
            decision = str(data.get("fit_decision", FALLBACK_DECISION))
            listing.fit_decision = decision if decision in VALID_DECISIONS else FALLBACK_DECISION
            listing.fit_reasons = [str(r) for r in data.get("fit_reasons", [])][:5]
            listing.gaps_identified = [str(g) for g in data.get("gaps_identified", [])][:5]
            log.info("⭐ Match Score: %.0f/100 (%s) → %s @ %s", listing.fit_score, listing.fit_decision, listing.title, listing.company or 'N/A')
        except Exception as exc:  # JSON errors / API limits must not crash the run
            log.warning("❌ Job evaluation failed for %r: %s", listing.title, exc)
            listing.fit_score = 0.0
            listing.fit_decision = FALLBACK_DECISION
        return listing
