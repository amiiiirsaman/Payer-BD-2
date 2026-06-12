import json
from datetime import datetime, timedelta

import httpx
import pytest
import respx

from payer_intel.tools.search_api import (
    SearchApiClient,
    SearchQuotaExceeded,
    _resolve_relative_date,
)


@pytest.fixture
def mock_search():
    with respx.mock(assert_all_called=False) as m:
        yield m


def test_google_normalizes(mock_search):
    mock_search.get("https://www.searchapi.io/api/v1/search").mock(
        return_value=httpx.Response(
            200,
            json={
                "organic_results": [
                    {"title": "T1", "link": "https://a", "snippet": "s1", "date": "2025-01-02"},
                    {"title": "T2", "url": "https://b", "snippet": "s2"},
                ]
            },
        )
    )
    c = SearchApiClient(max_calls=5)
    out = c.google("humana salesforce")
    assert len(out) == 2
    assert out[0] == {"title": "T1", "link": "https://a", "snippet": "s1", "date": "2025-01-02"}
    assert out[1]["link"] == "https://b"


def test_google_jobs_normalizes(mock_search):
    mock_search.get("https://www.searchapi.io/api/v1/search").mock(
        return_value=httpx.Response(
            200,
            json={
                "jobs": [
                    {
                        "title": "Salesforce Admin",
                        "company_name": "Humana",
                        "location": "Remote",
                        "description": "Build on Service Cloud and Health Cloud",
                        "apply_links": [{"link": "https://jobs.example/1"}],
                        "detected_extensions": {"posted_at": "3 days ago"},
                    }
                ]
            },
        )
    )
    c = SearchApiClient(max_calls=5)
    out = c.google_jobs('"Humana" Salesforce')
    assert out[0]["title"] == "Salesforce Admin"
    assert out[0]["link"] == "https://jobs.example/1"
    assert "Service Cloud" in out[0]["snippet"]


def test_quota_cap(mock_search):
    mock_search.get("https://www.searchapi.io/api/v1/search").mock(
        return_value=httpx.Response(200, json={"organic_results": []})
    )
    c = SearchApiClient(max_calls=1)
    c.google("q1")
    with pytest.raises(SearchQuotaExceeded):
        c.google("q2")


def test_retries_then_succeeds(mock_search):
    route = mock_search.get("https://www.searchapi.io/api/v1/search")
    route.side_effect = [
        httpx.ConnectError("boom"),
        httpx.Response(200, json={"organic_results": [{"title": "ok", "link": "https://x"}]}),
    ]
    c = SearchApiClient(max_calls=5)
    out = c.google("q")
    assert out[0]["title"] == "ok"


def _today():
    return datetime.utcnow().date()


def _parse(s):
    return datetime.strptime(s, "%Y-%m-%d").date()


def test_none_returns_none():
    assert _resolve_relative_date(None) is None


def test_empty_returns_none():
    assert _resolve_relative_date("") is None
    assert _resolve_relative_date("   ") is None


def test_iso_date_passthrough():
    assert _resolve_relative_date("2025-03-15") == "2025-03-15"


def test_today():
    assert _parse(_resolve_relative_date("today")) == _today()
    assert _parse(_resolve_relative_date("just now")) == _today()


def test_1_day_ago():
    assert _parse(_resolve_relative_date("1 day ago")) == _today() - timedelta(days=1)
    assert _parse(_resolve_relative_date("yesterday")) == _today() - timedelta(days=1)


def test_3_days_ago():
    assert _parse(_resolve_relative_date("3 days ago")) == _today() - timedelta(days=3)


def test_2_weeks_ago():
    assert _parse(_resolve_relative_date("2 weeks ago")) == _today() - timedelta(days=14)


def test_1_month_ago():
    assert _parse(_resolve_relative_date("1 month ago")) == _today() - timedelta(days=30)


def test_hours_ago_is_today():
    assert _parse(_resolve_relative_date("3 hours ago")) == _today()
    assert _parse(_resolve_relative_date("45 minutes ago")) == _today()


def test_unrecognized_returns_none():
    assert _resolve_relative_date("sometime last year") is None
