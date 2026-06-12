from types import SimpleNamespace
from unittest.mock import patch

from payer_intel.crew import _MAX_BODY_CHARS, _enrich_with_page_bodies
from payer_intel.schema import Evidence


def _ev(url):
    return Evidence(source_type="case_study", url=url, snippet="teaser")


def test_non_whitelisted_domain_not_fetched():
    ev = _ev("https://linkedin.com/jobs/123")
    with patch("payer_intel.tools.fetcher.fetch") as mock_fetch:
        _enrich_with_page_bodies([ev])
        mock_fetch.assert_not_called()
    assert ev.full_text is None


def test_whitelisted_domain_fetched_and_body_stored():
    ev = _ev("https://www.salesforce.com/customer-success-stories/united-healthcare/")
    html = (
        "<html><body><p>UnitedHealthcare uses Service Cloud and Health Cloud "
        "and Marketing Cloud to serve members.</p></body></html>"
    )
    with patch(
        "payer_intel.tools.fetcher.fetch",
        return_value=SimpleNamespace(text=html, status_code=200),
    ):
        _enrich_with_page_bodies([ev])
    assert ev.full_text is not None
    assert "Service Cloud" in ev.full_text
    assert "Health Cloud" in ev.full_text
    assert "Marketing Cloud" in ev.full_text


def test_fetch_failure_is_silently_skipped():
    ev = _ev("https://www.salesforce.com/customer-success-stories/x/")
    with patch("payer_intel.tools.fetcher.fetch", return_value=None):
        _enrich_with_page_bodies([ev])
    assert ev.full_text is None


def test_full_text_truncated_to_max_chars():
    ev = _ev("https://news.blueshieldca.com/press/release-123")
    html = f"<html><body>{'x' * 10000}</body></html>"
    with patch(
        "payer_intel.tools.fetcher.fetch",
        return_value=SimpleNamespace(text=html, status_code=200),
    ):
        _enrich_with_page_bodies([ev])
    assert ev.full_text is not None
    assert len(ev.full_text) <= _MAX_BODY_CHARS
