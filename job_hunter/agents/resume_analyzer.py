"""Resume Analyzer agent: extracts resume text and builds a CandidateProfile."""

import logging
from pathlib import Path

from ..llm import extract_json
from ..models import CandidateProfile

log = logging.getLogger(__name__)

PROFILE_SYSTEM = (
    "You are an expert technical recruiter. You read resumes and respond with "
    "only a valid JSON object, no prose."
)

PROFILE_PROMPT = """Analyze the resume below and extract high-quality recruiting insights. Your goal is to build a rich candidate profile that enables downstream search and vetting agents to find and grade highly relevant jobs.

Return ONLY a JSON object with these keys:
- "summary": A highly descriptive, dense 3-4 sentence professional summary. Highlight:
  1. Core engineering specializations (e.g., Generative AI/LLMs, RAG systems, Computer Vision, Python Backend).
  2. Prominent project accomplishments, model architectures built, or research experience.
  3. Key strengths and academic/professional background (e.g. IIT, research-driven).
- "skills": A comprehensive list of technical skills, libraries, frameworks, programming languages, and tools found on the resume (e.g., Python, PyTorch, TensorFlow, OpenCV, YOLO, FastAPI, LangChain, LangGraph, Git, Docker, Hugging Face). Extract every significant technical term to aid keyword-matching.
- "seniority": One of "Junior" (for freshers, interns, <2 yrs exp), "Mid-level", "Senior", "Staff/Principal".
- "job_titles": 4-5 specific target job titles, ordered by match strength, representing roles the candidate is highly qualified to apply for (e.g., "AI Engineer", "LLM Developer", "Computer Vision Engineer", "ML Engineer", "FastAPI Developer").

Resume:
\"\"\"
{resume}
\"\"\"
"""


class ResumeAnalyzer:
    """Phase 1 (Ingest): reads the resume file and writes the profile to the blackboard."""

    def __init__(self, llm):
        self.llm = llm

    def extract_text(self, path) -> str:
        p = Path(path)
        suffix = p.suffix.lower()
        if suffix == ".pdf":
            try:
                from pypdf import PdfReader
            except ImportError:
                raise RuntimeError(
                    "pypdf is required to read PDF resumes. Run `pip install pypdf` "
                    "or provide a .txt/.md resume instead."
                )
            reader = PdfReader(str(p))
            return "\n".join(page.extract_text() or "" for page in reader.pages)
        if suffix in (".txt", ".md"):
            return p.read_text(encoding="utf-8", errors="ignore")
        raise ValueError(f"Unsupported resume format: {suffix or p.name}")

    def analyze(self, path) -> CandidateProfile:
        raw = self.extract_text(path)
        log.info("📄 Extracted %d characters from resume.", len(raw))
        log.debug("Resume Content:\n%s", raw)
        response = self.llm.chat(
            system=PROFILE_SYSTEM,
            user=PROFILE_PROMPT.format(resume=raw[:50000]),
        )
        data = extract_json(response)
        summary_raw = data.get("summary", "")
        if isinstance(summary_raw, dict):
            summary_str = "\n".join(f"• {k.replace('_', ' ').title()}: {', '.join(v) if isinstance(v, list) else v}" for k, v in summary_raw.items())
        elif isinstance(summary_raw, list):
            summary_str = " ".join(str(s) for s in summary_raw)
        else:
            summary_str = str(summary_raw or "")

        return CandidateProfile(
            summary=summary_str,
            skills=[str(s).strip() for s in data.get("skills", [])],
            seniority=str(data.get("seniority", "Unknown")),
            job_titles=[str(t) for t in data.get("job_titles", [])],
            raw_text=raw,
        )

