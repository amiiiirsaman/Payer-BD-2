from __future__ import annotations

import re
import threading
from datetime import datetime, timedelta
from typing import Any, Iterable, Optional

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from ..config import get_settings


class SearchApiError(RuntimeError):
    pass


class SearchQuotaExceeded(SearchApiError):
    pass


class SearchApiClient:
    """Thin wrapper for SearchApi.io (replaces Serper/SerpAPI from the design doc)."""

    def __init__(self, max_calls: Optional[int] = None) -> None:
        s = get_settings()
        if s.search_provider != "searchapi":
            raise SearchApiError(
                f"SEARCH_PROVIDER={s.search_provider!r} not supported; only 'searchapi' is implemented."
            )
        self._key = s.searchapi_key
        self._endpoint = s.searchapi_endpoint
        self._timeout = s.http_timeout_seconds
        self._max_calls = max_calls if max_calls is not None else s.max_calls_per_run
        self._calls = 0
        self._lock = threading.Lock()

    @property
    def call_count(self) -> int:
        return self._calls

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        retry=retry_if_exception_type((httpx.HTTPError,)),
        reraise=True,
    )
    def _request(self, params: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            if self._calls >= self._max_calls:
                raise SearchQuotaExceeded(
                    f"SearchApi.io call cap reached ({self._max_calls})"
                )
            self._calls += 1
        params = {**params, "api_key": self._key}
        with httpx.Client(timeout=self._timeout) as c:
            r = c.get(self._endpoint, params=params)
            r.raise_for_status()
            return r.json()

    def google(self, query: str, num: int = 10) -> list[dict[str, Any]]:
        data = self._request({"engine": "google", "q": query, "num": num})
        return _normalize_organic(data.get("organic_results", []))

    def google_news(
        self, query: str, time_range: str = "qdr:y", num: int = 10
    ) -> list[dict[str, Any]]:
        data = self._request(
            {"engine": "google_news", "q": query, "tbs": time_range, "num": num}
        )
        items = data.get("organic_results") or data.get("news_results") or []
        return _normalize_news(items)

    def google_jobs(
        self, query: str, location: str = "United States", num: int = 10
    ) -> list[dict[str, Any]]:
        data = self._request(
            {"engine": "google_jobs", "q": query, "location": location, "num": num}
        )
        return _normalize_jobs(data.get("jobs") or data.get("jobs_results") or [])


def _normalize_organic(items: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for it in items:
        out.append(
            {
                "title": it.get("title", ""),
                "link": it.get("link") or it.get("url", ""),
                "snippet": it.get("snippet", ""),
                "date": it.get("date"),
            }
        )
    return out


def _normalize_news(items: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for it in items:
        out.append(
            {
                "title": it.get("title", ""),
                "link": it.get("link") or it.get("url", ""),
                "snippet": it.get("snippet") or it.get("description", ""),
                "date": _resolve_relative_date(it.get("date") or it.get("published_at")),
            }
        )
    return out


_REL_PATTERNS = [
    (re.compile(r"^\s*(\d+)\s+day[s]?\s+ago\s*$", re.I), lambda n: n),
    (re.compile(r"^\s*(\d+)\s+week[s]?\s+ago\s*$", re.I), lambda n: n * 7),
    (re.compile(r"^\s*(\d+)\s+month[s]?\s+ago\s*$", re.I), lambda n: n * 30),
    (re.compile(r"^\s*(\d+)\s+hour[s]?\s+ago\s*$", re.I), lambda n: 0),
    (re.compile(r"^\s*(\d+)\s+minute[s]?\s+ago\s*$", re.I), lambda n: 0),
]


def _resolve_relative_date(raw: str | None) -> str | None:
    if not raw:
        return None
    s = raw.strip()
    if not s:
        return None
    try:
        datetime.fromisoformat(s.replace("Z", "+00:00"))
        return s
    except ValueError:
        pass
    low = s.lower()
    today = datetime.utcnow().date()
    if low in ("today", "just now"):
        return today.strftime("%Y-%m-%d")
    if low == "yesterday":
        return (today - timedelta(days=1)).strftime("%Y-%m-%d")
    for pat, to_days in _REL_PATTERNS:
        m = pat.match(s)
        if m:
            return (today - timedelta(days=to_days(int(m.group(1))))).strftime("%Y-%m-%d")
    return None


def _normalize_jobs(items: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for it in items:
        out.append(
            {
                "title": it.get("title", ""),
                "link": it.get("apply_link")
                or (it.get("apply_links") or [{}])[0].get("link", "")
                or it.get("share_link", ""),
                "snippet": it.get("description", "")[:2000],
                "date": _resolve_relative_date(
                    it.get("posted_at") or it.get("detected_extensions", {}).get("posted_at")
                ),
                "company": it.get("company_name", ""),
                "location": it.get("location", ""),
            }
        )
    return out
