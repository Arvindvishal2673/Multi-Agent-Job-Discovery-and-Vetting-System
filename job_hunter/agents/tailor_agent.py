"""TailorAgent: Generates tailored cover letters, recruiter outreach messages, and ATS resume pitches.

Uses NVIDIA Nemotron 120B to craft personalized application assets tailored
to a specific candidate profile and job description.
"""

import logging
from typing import Optional

from ..llm import extract_json, get_default_llm
from ..models import CandidateProfile, JobListing, TailoredApplicationPackage

log = logging.getLogger(__name__)

TAILOR_SYSTEM = """You are an elite executive career coach and senior technical recruiter.

Given a candidate's profile and a specific job posting, your task is to generate a highly tailored, compelling application package.

Rules:
- Be specific, professional, and highlight the candidate's exact matching skills and achievements.
- Address any key job requirements using the candidate's background.
- Keep the recruiter outreach message concise (under 150 words), persuasive, and ready to send via LinkedIn/Email.
- Make the cover letter well-structured with opening pitch, core alignment, and call-to-action closing.
- You MUST respond with ONLY a valid JSON object containing no outer markdown backticks.

Response Format:
{
  "cover_letter": "Full professional cover letter text...",
  "outreach_message": "Concise LinkedIn/Email cold message for recruiters...",
  "tailored_summary": "2-3 sentence ATS-optimized resume summary...",
  "key_highlights": [
    "Bullet 1 linking candidate skill to job requirement",
    "Bullet 2 linking candidate skill to job requirement",
    "Bullet 3 linking candidate skill to job requirement"
  ]
}
"""

TAILOR_USER_TEMPLATE = """Candidate Profile:
- Seniority: {seniority}
- Summary: {summary}
- Skills: {skills}
- Job Titles: {job_titles}
- Resume Raw Excerpt: {raw_excerpt}

Target Job Listing:
- Title: {title}
- Company: {company}
- Location: {location}
- Match Fit Score: {fit_score}% ({fit_decision})
- Match Reasons: {fit_reasons}
- Identified Gaps: {gaps}
- Job Description: {description}

Generate a tailored cover letter, recruiter outreach message, ATS summary, and key bullet highlights for this application."""


class TailorAgent:
    """Agent responsible for crafting personalized application packages for target jobs."""

    def __init__(self, llm=None):
        self.llm = llm or get_default_llm()

    def tailor(self, profile: CandidateProfile, job: JobListing) -> TailoredApplicationPackage:
        """Generate a tailored application package for the given candidate and job listing."""
        raw_excerpt = profile.raw_text[:1000] if profile.raw_text else profile.summary

        user_msg = TAILOR_USER_TEMPLATE.format(
            seniority=profile.seniority,
            summary=profile.summary,
            skills=", ".join(profile.skills[:15]),
            job_titles=", ".join(profile.job_titles),
            raw_excerpt=raw_excerpt,
            title=job.title,
            company=job.company or "Hiring Team",
            location=job.location or "N/A",
            fit_score=job.fit_score,
            fit_decision=job.fit_decision or "Strong Fit",
            fit_reasons=", ".join(job.fit_reasons) if job.fit_reasons else "Strong technical match",
            gaps=", ".join(job.gaps_identified) if job.gaps_identified else "None",
            description=job.description[:1500] if job.description else job.title,
        )

        try:
            raw_response = self.llm.chat(system=TAILOR_SYSTEM, user=user_msg, temperature=0.3)
            data = extract_json(raw_response)

            return TailoredApplicationPackage(
                cover_letter=str(data.get("cover_letter", "")).strip(),
                outreach_message=str(data.get("outreach_message", "")).strip(),
                tailored_summary=str(data.get("tailored_summary", "")).strip(),
                key_highlights=[str(h).strip() for h in data.get("key_highlights", []) if h],
            )
        except Exception as exc:
            log.warning("TailorAgent failed to generate package via LLM: %s. Using fallback package.", exc)
            company_str = job.company or "the hiring team"
            return TailoredApplicationPackage(
                cover_letter=f"Dear Hiring Manager at {company_str},\n\nI am writing to express my strong interest in the {job.title} position. With my background in {', '.join(profile.skills[:5])}, I am confident in my ability to deliver immediate value to your engineering team.\n\nBest regards,\nCandidate",
                outreach_message=f"Hi Hiring Team,\n\nI saw the opening for {job.title} at {company_str} and wanted to reach out directly. My background aligns strongly with your requirements in {', '.join(profile.skills[:3])}. Would love to connect!\n\nBest,",
                tailored_summary=f"Experienced {profile.seniority} engineer proficient in {', '.join(profile.skills[:5])}, seeking to contribute technical expertise to the {job.title} role at {company_str}.",
                key_highlights=[f"Strong match for {job.title} role requirements", f"Proficient in {', '.join(profile.skills[:4])}"],
            )
