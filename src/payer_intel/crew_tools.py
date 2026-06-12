from __future__ import annotations

import json
from typing import Type

from crewai.tools import BaseTool
from pydantic import BaseModel, Field

from .tools.search_api import SearchApiClient
from .tools.tech_fingerprint import fingerprint_domain

_search = SearchApiClient()


class _QueryInput(BaseModel):
    query: str = Field(..., description="Search query string")


class GoogleSearchTool(BaseTool):
    name: str = "google_search"
    description: str = (
        "General Google web search via SearchApi.io. Use for case-study, partner, "
        "and review pages. Input: a single search query string. "
        "Returns a JSON list of {title, link, snippet, date}."
    )
    args_schema: Type[BaseModel] = _QueryInput

    def _run(self, query: str) -> str:
        return json.dumps(_search.google(query, num=10))


class GoogleNewsTool(BaseTool):
    name: str = "google_news_search"
    description: str = (
        "Google News search via SearchApi.io, last 12 months. Use for press releases "
        "and implementation announcements. Returns JSON list of {title, link, snippet, date}."
    )
    args_schema: Type[BaseModel] = _QueryInput

    def _run(self, query: str) -> str:
        return json.dumps(_search.google_news(query, time_range="qdr:y", num=10))


class GoogleJobsTool(BaseTool):
    name: str = "google_jobs_search"
    description: str = (
        "Google Jobs search via SearchApi.io. Use to find job postings that mention "
        "Salesforce products at a specific payer. Returns JSON list with description snippets."
    )
    args_schema: Type[BaseModel] = _QueryInput

    def _run(self, query: str) -> str:
        return json.dumps(_search.google_jobs(query))


class _DomainInput(BaseModel):
    domain: str = Field(..., description="Bare payer domain, e.g. 'humana.com'")


class TechFingerprintTool(BaseTool):
    name: str = "tech_fingerprint"
    description: str = (
        "Fetches the payer's website and scans for Salesforce technographic markers "
        "(force.com, my.salesforce.com, my.site.com, pardot.com, marketingcloud.com, etc.). "
        "Returns JSON list of {product, url, matched}."
    )
    args_schema: Type[BaseModel] = _DomainInput

    def _run(self, domain: str) -> str:
        hits = fingerprint_domain(domain)
        return json.dumps(
            [{"product": h.product.value, "url": h.url, "matched": h.matched} for h in hits]
        )


# ─────────────────────────────────────────────────────────────────────────────
# Executive Intelligence pipeline tools (--mode executive)
# ─────────────────────────────────────────────────────────────────────────────
class ExecLinkedInSearchTool(BaseTool):
    name: str = "executive_linkedin_search"
    description: str = (
        "Search LinkedIn (snippet-only) for an executive at a target payer. "
        "Input: a query string like '\"Humana\" \"Chief Information Officer\"'. "
        "Returns JSON list of {title, link, snippet, date} — LinkedIn pages "
        "themselves are auth-walled, so rely on snippets for current title, "
        "tenure ('Present'), and past firms."
    )
    args_schema: Type[BaseModel] = _QueryInput

    def _run(self, query: str) -> str:
        scoped = (
            f"(site:linkedin.com/in/ OR site:linkedin.com/pub/) {query}"
        )
        return json.dumps(_search.google(scoped, num=10))


class ExecLeadershipPageTool(BaseTool):
    name: str = "executive_leadership_page_search"
    description: str = (
        "Search the payer's own domain for its leadership / executive-team page. "
        "Input: a query like '\"humana.com\" (leadership OR executive team OR our team)'. "
        "Returns JSON list of {title, link, snippet, date}. The page-body "
        "enricher will fetch any payer-domain URLs returned."
    )
    args_schema: Type[BaseModel] = _QueryInput

    def _run(self, query: str) -> str:
        return json.dumps(_search.google(query, num=10))


class ExecThirdPartyDirectoryTool(BaseTool):
    name: str = "executive_third_party_directory_search"
    description: str = (
        "Cross-reference an executive against third-party directories (ZoomInfo, "
        "RocketReach, Becker's Hospital Review) for tenure and past-firm validation. "
        "Input: '\"<payer>\" \"<title or name>\"'. Returns JSON list."
    )
    args_schema: Type[BaseModel] = _QueryInput

    def _run(self, query: str) -> str:
        scoped = (
            f"(site:rocketreach.co OR site:zoominfo.com OR site:beckershospitalreview.com) {query}"
        )
        return json.dumps(_search.google(scoped, num=10))
