import logging
from typing import List
import requests
from ..models import JobListing
from .base import JobSourceAgent
from .. import config

log = logging.getLogger(__name__)

# List of Greenhouse and Lever company tokens in India that are verified working
GREENHOUSE_COMPANIES = [
    ("groww", "Groww"),
    ("credave", "CredAvenue / Yubi"),
    ("postman", "Postman"),
    ("browserstack", "BrowserStack"),
    ("chargebee", "Chargebee"),
    ("delhivery", "Delhivery"),
    ("innovaccer", "Innovaccer"),
    ("unacademy", "Unacademy"),
]

LEVER_COMPANIES = [
    ("cred", "CRED"),
    ("pocketfm", "PocketFM"),
]


class DirectATSAgent(JobSourceAgent):
    name = "direct_ats"

    def search(self, queries: List[str], max_results: int = 25, posted_within_days: int = 1) -> List[JobListing]:
        import time
        from datetime import datetime, timezone
        listings: List[JobListing] = []
        
        # Helper to do cheap overlap check
        def _matches(text: str, search_queries: List[str]) -> bool:
            text = text.lower()
            for query in search_queries:
                tokens = [t for t in query.lower().split() if len(t) > 2]
                if tokens and any(t in text for t in tokens):
                    return True
            return not search_queries

        import html
        import re

        def _clean_html(raw_text: str) -> str:
            if not raw_text:
                return ""
            clean = re.sub(r"<[^>]*>", " ", raw_text)
            clean = html.unescape(clean)
            return re.sub(r"\s+", " ", clean).strip()

        cutoff_ts = (time.time() - (posted_within_days * 86400)) if (posted_within_days and posted_within_days > 0) else 0.0

        # 1. Query Greenhouse boards
        for company_id, company_name in GREENHOUSE_COMPANIES:
            url = f"https://boards-api.greenhouse.io/v1/boards/{company_id}/jobs?content=true"
            try:
                res = requests.get(url, timeout=config.REQUEST_TIMEOUT)
                if res.status_code == 200:
                    jobs = res.json().get("jobs", [])
                    for job in jobs:
                        title = job.get("title", "")
                        raw_content = job.get("content", "")
                        clean_desc = _clean_html(raw_content)
                        # Quick match check
                        blob = f"{title} {clean_desc}"
                        if not _matches(blob, queries):
                            continue
                        
                        updated_at = job.get("updated_at")
                        posted_ts = 0.0
                        posted_at = ""
                        if updated_at:
                            try:
                                dt = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
                                posted_ts = dt.timestamp()
                                posted_at = dt.strftime("%Y-%m-%d")
                            except Exception:
                                pass

                        if cutoff_ts > 0 and posted_ts > 0 and posted_ts < cutoff_ts:
                            continue

                        listings.append(
                            JobListing(
                                title=title,
                                company=company_name,
                                location=job.get("location", {}).get("name", "India"),
                                url=job.get("absolute_url", ""),
                                source="Greenhouse",
                                description=clean_desc[:2000],
                                posted_at=posted_at,
                                posted_timestamp=posted_ts,
                            )
                        )
            except Exception as exc:
                err_body = getattr(getattr(exc, 'response', None), 'text', str(exc))
                log.warning("❌ Greenhouse direct fetch failed for %s: %s (Details: %s)", company_name, exc, err_body[:200])

        # 2. Query Lever boards
        for company_id, company_name in LEVER_COMPANIES:
            url = f"https://api.lever.co/v0/postings/{company_id}"
            try:
                res = requests.get(url, timeout=config.REQUEST_TIMEOUT)
                if res.status_code == 200:
                    jobs = res.json()
                    for job in jobs:
                        title = job.get("title", "")
                        desc = _clean_html(job.get("description", ""))
                        lists = job.get("lists", [])
                        list_text = " ".join([_clean_html(item.get("content", "")) for sublist in lists for item in sublist.get("items", [])])
                        blob = f"{title} {desc} {list_text}"
                        if not _matches(blob, queries):
                            continue
                        
                        created_at_ms = job.get("createdAt")
                        posted_ts = 0.0
                        posted_at = ""
                        if created_at_ms:
                            try:
                                dt = datetime.fromtimestamp(created_at_ms / 1000.0, tz=timezone.utc)
                                posted_ts = dt.timestamp()
                                posted_at = dt.strftime("%Y-%m-%d")
                            except Exception:
                                pass

                        if cutoff_ts > 0 and posted_ts > 0 and posted_ts < cutoff_ts:
                            continue

                        loc_dict = job.get("categories", {})
                        location = loc_dict.get("location", "India")
                        
                        listings.append(
                            JobListing(
                                title=title,
                                company=company_name,
                                location=location,
                                url=job.get("applyUrl", ""),
                                source="Lever",
                                description=desc[:2000],
                                posted_at=posted_at,
                                posted_timestamp=posted_ts,
                            )
                        )
                else:
                    log.warning("⚠️ Lever API returned status %d for %s: %s", res.status_code, company_name, res.text[:200])
            except Exception as exc:
                err_body = getattr(getattr(exc, 'response', None), 'text', str(exc))
                log.warning("❌ Lever direct fetch failed for %s: %s (Details: %s)", company_name, exc, err_body[:200])

        return listings[:max_results]
