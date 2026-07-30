"""Google ATS Dorking Agent.

Discovers direct company career portal postings (Greenhouse, Lever, Workday, iCIMS, Ashby, SmartRecruiters)
using Google Dorking search operators and date filters.
"""

import logging
import re
import time
from typing import List
from urllib.parse import quote_plus, unquote

import requests

from .. import config
from ..models import JobListing
from .base import JobSourceAgent

log = logging.getLogger(__name__)

# Target ATS domain operators for Google Dorking
ATS_DOMAINS = [
    "site:boards.greenhouse.io",
    "site:jobs.lever.co",
    "site:myworkdayjobs.com",
    "site:icims.com",
    "site:jobs.ashbyhq.com",
    "site:smartrecruiters.com",
]


class GoogleATSDorkAgent(JobSourceAgent):
    """Searches web engines for direct ATS listings via Google Dork queries."""

    name = "google_ats_dork"

    def search(self, queries: List[str], max_results: int = 25, posted_within_days: int = 1) -> List[JobListing]:
        listings: List[JobListing] = []
        site_query = "(" + " OR ".join(ATS_DOMAINS) + ")"

        for raw_query in (queries or ["Software Engineer"])[:2]:
            dork_query = f'{site_query} "{raw_query}"'
            serper_key = config.get_secret("SERPER_API_KEY", "")

            if serper_key:
                listings.extend(self._fetch_via_serper(dork_query, serper_key, max_results, posted_within_days))
            else:
                listings.extend(self._fetch_via_ddg(dork_query, max_results))

        return listings[:max_results]

    def _fetch_via_serper(self, query: str, api_key: str, max_results: int, days: int) -> List[JobListing]:
        """Fetches real Google search results in ~300ms using Serper API."""
        time_filter = "qdr:d1" if days <= 1 else ("qdr:d3" if days <= 3 else "qdr:w1")
        url = "https://google.serper.dev/search"
        payload = {
            "q": query,
            "tbs": time_filter,
            "num": min(max_results, 50),
        }
        headers = {"X-API-KEY": api_key, "Content-Type": "application/json"}

        results = []
        try:
            res = requests.post(url, json=payload, headers=headers, timeout=config.REQUEST_TIMEOUT)
            if res.ok:
                data = res.json()
                for item in data.get("organic", []):
                    clean_url = item.get("link", "")
                    title = item.get("title", "")
                    snippet = item.get("snippet", "")

                    if not clean_url:
                        continue

                    company, platform = self._parse_metadata(clean_url, title)

                    results.append(
                        JobListing(
                            title=title,
                            company=company,
                            location="Remote / Direct ATS",
                            url=clean_url,
                            source=f"Google Dork ({platform})",
                            description=snippet or f"Direct ATS posting discovered via Google: {title}",
                            posted_at="Fresh",
                            posted_timestamp=time.time(),
                        )
                    )
        except Exception as exc:
            log.warning("❌ Serper API search failed: %s", exc)
        return results

    def _fetch_via_ddg(self, query: str, max_results: int) -> List[JobListing]:
        """Fallback scraper fetching search result links without API keys."""
        encoded_q = quote_plus(query)
        url = f"https://html.duckduckgo.com/html/?q={encoded_q}"
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            )
        }
        results = []
        try:
            res = requests.get(url, headers=headers, timeout=config.REQUEST_TIMEOUT)
            if res.ok:
                raw_html = res.text
                url_matches = re.findall(r'<a class="result__url" href="([^"]+)"', raw_html)
                title_matches = re.findall(r'<a class="result__a"[^>]*>(.*?)</a>', raw_html, re.DOTALL)

                for idx, raw_url in enumerate(url_matches[:max_results]):
                    clean_url = unquote(raw_url).strip()
                    if "uddg=" in clean_url:
                        match = re.search(r"uddg=([^&]+)", clean_url)
                        if match:
                            clean_url = unquote(match.group(1))

                    raw_title = title_matches[idx] if idx < len(title_matches) else "Job Posting"
                    clean_title = re.sub(r"<[^>]*>", "", raw_title).strip()

                    company, platform = self._parse_metadata(clean_url, clean_title)

                    results.append(
                        JobListing(
                            title=clean_title,
                            company=company,
                            location="Remote / Direct ATS",
                            url=clean_url,
                            source=f"Google Dork ({platform})",
                            description=f"Direct company ATS listing discovered via search: {clean_title}",
                            posted_at="Fresh",
                            posted_timestamp=time.time(),
                        )
                    )
        except Exception as exc:
            log.warning("❌ DuckDuckGo scraper failed: %s", exc)
        return results

    def _parse_metadata(self, url: str, title: str) -> tuple:
        """Extract ATS platform type and company name from URL & Title."""
        platform = "Direct ATS"
        if "greenhouse.io" in url:
            platform = "Greenhouse"
        elif "lever.co" in url:
            platform = "Lever"
        elif "workdayjobs.com" in url:
            platform = "Workday"
        elif "icims.com" in url:
            platform = "iCIMS"
        elif "ashbyhq.com" in url:
            platform = "Ashby"
        elif "smartrecruiters.com" in url:
            platform = "SmartRecruiters"

        company = "Direct Employer"
        url_parts = url.split("/")
        if len(url_parts) > 3 and ("greenhouse.io" in url or "lever.co" in url or "ashbyhq.com" in url):
            company = url_parts[3].replace("-", " ").title()
        elif " - " in title:
            company = title.split(" - ")[-1].strip()

        return company, platform
