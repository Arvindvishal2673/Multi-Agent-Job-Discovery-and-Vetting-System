"""Structured API agents: Remotive, RemoteOK and Arbeitnow public JSON feeds."""

import logging
from typing import List

import requests

from .. import config
from ..models import JobListing
from .base import JobSourceAgent

log = logging.getLogger(__name__)


def _matches(text: str, queries: List[str]) -> bool:
    """Cheap keyword overlap between a job blob and the search queries."""
    text = text.lower()
    for query in queries:
        tokens = [t for t in query.lower().split() if len(t) > 2]
        if tokens and any(t in text for t in tokens):
            return True
    return not queries


class RemotiveAgent(JobSourceAgent):
    name = "remotive"
    URL = "https://remotive.com/api/remote-jobs"

    def search(self, queries: List[str], max_results: int = 25) -> List[JobListing]:
        listings: List[JobListing] = []
        for query in queries or [""]:
            try:
                res = requests.get(
                    self.URL, params={"search": query}, timeout=config.REQUEST_TIMEOUT
                )
                if not res.ok:
                    log.warning("⚠️ Remotive API returned status %d: %s", res.status_code, res.text[:200])
                res.raise_for_status()
                data = res.json()
                for job in data.get("jobs", [])[:max_results]:
                    listings.append(
                        JobListing(
                            title=job.get("title", ""),
                            company=job.get("company_name", ""),
                            location=job.get("candidate_required_location", "Remote"),
                            url=job.get("url", ""),
                            source="Remotive",
                            description=(job.get("description") or "")[:2000],
                            salary=job.get("salary", ""),
                        )
                    )
            except Exception as exc:
                err_body = getattr(getattr(exc, 'response', None), 'text', str(exc))
                log.warning("❌ Remotive fetch failed for query %r: %s (Details: %s)", query, exc, err_body[:200])
        return listings


class RemoteOKAgent(JobSourceAgent):
    name = "remoteok"
    URL = "https://remoteok.com/api"

    def search(self, queries: List[str], max_results: int = 25) -> List[JobListing]:
        try:
            res = requests.get(
                self.URL,
                headers={"User-Agent": "job-hunter-agent"},
                timeout=config.REQUEST_TIMEOUT,
            )
            if not res.ok:
                log.warning("⚠️ RemoteOK API returned status %d: %s", res.status_code, res.text[:200])
            res.raise_for_status()
            data = res.json()
        except Exception as exc:
            err_body = getattr(getattr(exc, 'response', None), 'text', str(exc))
            log.warning("❌ RemoteOK fetch failed: %s (Details: %s)", exc, err_body[:200])
            return []

        listings: List[JobListing] = []
        for job in data:
            if not isinstance(job, dict) or not job.get("position"):
                continue  # first element is a legal notice, not a job
            blob = f"{job.get('position', '')} {' '.join(job.get('tags', []))}"
            if not _matches(blob, queries):
                continue
            salary = ""
            if job.get("salary_min"):
                salary = f"${job['salary_min']:,} - ${job.get('salary_max') or job['salary_min']:,}"
            listings.append(
                JobListing(
                    title=job.get("position", ""),
                    company=job.get("company", ""),
                    location=job.get("location", "Remote") or "Remote",
                    url=job.get("url", ""),
                    source="RemoteOK",
                    description=(job.get("description") or "")[:2000],
                    salary=salary,
                )
            )
            if len(listings) >= max_results:
                break
        return listings


class ArbeitnowAgent(JobSourceAgent):
    name = "arbeitnow"
    URL = "https://www.arbeitnow.com/api/job-board-api"

    def search(self, queries: List[str], max_results: int = 25) -> List[JobListing]:
        try:
            res = requests.get(self.URL, timeout=config.REQUEST_TIMEOUT)
            if not res.ok:
                log.warning("⚠️ Arbeitnow API returned status %d: %s", res.status_code, res.text[:200])
            res.raise_for_status()
            data = res.json()
        except Exception as exc:
            err_body = getattr(getattr(exc, 'response', None), 'text', str(exc))
            log.warning("❌ Arbeitnow fetch failed: %s (Details: %s)", exc, err_body[:200])
            return []

        listings: List[JobListing] = []
        for job in data.get("data", []):
            blob = f"{job.get('title', '')} {' '.join(job.get('tags', []))}"
            if not _matches(blob, queries):
                continue
            listings.append(
                JobListing(
                    title=job.get("title", ""),
                    company=job.get("company_name", ""),
                    location=job.get("location", ""),
                    url=job.get("url", ""),
                    source="Arbeitnow",
                    description=(job.get("description") or "")[:2000],
                )
            )
            if len(listings) >= max_results:
                break
        return listings


class AdzunaAgent(JobSourceAgent):
    name = "adzuna"
    BASE_URL = "https://api.adzuna.com/v1/api/jobs/in/search"

    def search(self, queries: List[str], max_results: int = 25, posted_within_days: int = 1) -> List[JobListing]:
        import re
        from datetime import datetime
        app_id = config.ADZUNA_APP_ID
        app_key = config.ADZUNA_APP_KEY
        if not app_id or not app_key:
            log.info("Adzuna credentials not set; skipping Adzuna search.")
            return []

        listings: List[JobListing] = []
        for raw_query in queries or [""]:
            # Try original query first, and if long (>3 words), create fallback variants
            query_candidates = [raw_query]
            words = raw_query.split()
            if len(words) > 3:
                query_candidates.append(" ".join(words[:3]))
                query_candidates.append(" ".join(words[:2]))

            query_results = []
            for query in query_candidates:
                try:
                    url = f"{self.BASE_URL}/1"
                    params = {
                        "app_id": app_id,
                        "app_key": app_key,
                        "results_per_page": min(max_results, 50),
                        "what": query,
                    }
                    if posted_within_days and posted_within_days > 0:
                        params["max_days_old"] = posted_within_days

                    res = requests.get(url, params=params, timeout=config.REQUEST_TIMEOUT)
                    if not res.ok:
                        log.warning("⚠️ Adzuna API returned status %d for query %r: %s", res.status_code, query, res.text[:200])
                    res.raise_for_status()
                    data = res.json()
                    results = data.get("results", [])
                    if results:
                        for job in results[:max_results]:
                            title = job.get("title", "")
                            title = re.sub(r"<[^>]*>", "", title).strip()
                            
                            company = job.get("company", {}).get("display_name", "")
                            
                            locations = job.get("location", {}).get("area", [])
                            location = ", ".join(str(loc) for loc in locations) if locations else "India"
                            
                            desc = job.get("description", "")
                            desc = re.sub(r"<[^>]*>", "", desc).strip()
                            
                            created_raw = job.get("created", "")
                            posted_at = created_raw[:10] if created_raw else ""
                            posted_ts = 0.0
                            if created_raw:
                                try:
                                    posted_ts = datetime.fromisoformat(created_raw.replace("Z", "+00:00")).timestamp()
                                except Exception:
                                    pass

                            salary_min = job.get("salary_min")
                            salary_max = job.get("salary_max")
                            salary = ""
                            if salary_min or salary_max:
                                if salary_min and salary_max:
                                    salary = f"INR {salary_min:,} - {salary_max:,}"
                                elif salary_min:
                                    salary = f"INR {salary_min:,}+"
                                else:
                                    salary = f"INR {salary_max:,}"
                            
                            query_results.append(
                                JobListing(
                                    title=title,
                                    company=company,
                                    location=location,
                                    url=job.get("redirect_url", ""),
                                    source="Adzuna",
                                    description=desc[:2000],
                                    salary=salary,
                                    posted_at=posted_at,
                                    posted_timestamp=posted_ts,
                                )
                            )
                        # If query yielded results, break out of fallback loop for this raw_query
                        break
                except Exception as exc:
                    err_body = getattr(getattr(exc, 'response', None), 'text', str(exc))
                    log.warning("❌ Adzuna search failed for query %r: %s (Details: %s)", query, exc, err_body[:200])
            listings.extend(query_results)
        return listings[:max_results]
