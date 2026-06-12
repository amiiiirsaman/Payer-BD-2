"""Tests for the executive confidence-scoring rules (qc_exec.py)."""
from datetime import datetime, timedelta

from payer_intel.qc_exec import (
    aggregate_confidence,
    score_executive,
)
from payer_intel.schema import ConfidenceScore, Evidence


def _d(days_ago: int) -> str:
    return (datetime.utcnow() - timedelta(days=days_ago)).strftime("%Y-%m-%d")


def test_no_evidence_is_low():
    assert score_executive([]).confidence == ConfidenceScore.LOW


def test_leadership_page_alone_is_high():
    evs = [Evidence(source_type="leadership_page", url="https://humana.com/about/leadership")]
    r = score_executive(evs)
    assert r.confidence == ConfidenceScore.HIGH
    assert "leadership page" in r.note


def test_leadership_page_plus_linkedin_is_high():
    evs = [
        Evidence(source_type="leadership_page", url="https://humana.com/leadership"),
        Evidence(
            source_type="linkedin_profile",
            url="https://www.linkedin.com/in/jane-doe/",
            snippet="Chief Information Officer at Humana 2021 - Present",
        ),
    ]
    r = score_executive(evs)
    assert r.confidence == ConfidenceScore.HIGH


def test_recent_news_plus_linkedin_is_high():
    evs = [
        Evidence(
            source_type="executive_news",
            url="https://businesswire.com/x",
            snippet="Humana appoints Jane Doe as CIO",
            date=_d(60),
        ),
        Evidence(
            source_type="linkedin_profile",
            url="https://www.linkedin.com/in/jane-doe/",
            snippet="Chief Information Officer · Present",
        ),
    ]
    assert score_executive(evs).confidence == ConfidenceScore.HIGH


def test_linkedin_with_present_tenure_is_medium():
    evs = [
        Evidence(
            source_type="linkedin_profile",
            url="https://www.linkedin.com/in/jane-doe/",
            snippet="Chief Marketing Officer · 2023 - Present",
        ),
    ]
    r = score_executive(evs)
    assert r.confidence == ConfidenceScore.MEDIUM
    assert "active linkedin" in r.note.lower()


def test_linkedin_without_present_is_low():
    evs = [
        Evidence(
            source_type="linkedin_profile",
            url="https://www.linkedin.com/in/john-smith/",
            snippet="Former CIO at Humana 2018 - 2022",
        ),
    ]
    r = score_executive(evs)
    assert r.confidence == ConfidenceScore.LOW


def test_third_party_only_is_low():
    evs = [
        Evidence(
            source_type="third_party_directory",
            url="https://rocketreach.co/jane-doe",
            snippet="Chief Information Officer at Humana",
        ),
    ]
    r = score_executive(evs)
    assert r.confidence == ConfidenceScore.LOW
    assert "third-party" in r.note


def test_recent_news_alone_is_medium():
    evs = [
        Evidence(
            source_type="executive_news",
            url="https://prnewswire.com/x",
            snippet="Humana appoints new CIO",
            date=_d(30),
        ),
    ]
    assert score_executive(evs).confidence == ConfidenceScore.MEDIUM


def test_stale_news_alone_is_low():
    evs = [
        Evidence(
            source_type="executive_news",
            url="https://prnewswire.com/x",
            snippet="Humana appoints CIO",
            date=_d(400),
        ),
    ]
    assert score_executive(evs).confidence == ConfidenceScore.LOW


def test_aggregate_empty_is_low():
    assert aggregate_confidence([]) == ConfidenceScore.LOW


def test_aggregate_takes_max():
    out = aggregate_confidence([
        ConfidenceScore.LOW,
        ConfidenceScore.HIGH,
        ConfidenceScore.MEDIUM,
    ])
    assert out == ConfidenceScore.HIGH


def test_linkedin_url_detection_recognizes_legacy_pub_path():
    evs = [
        Evidence(
            source_type="executive_news",  # source_type wrong on purpose
            url="https://www.linkedin.com/pub/jane-doe/12/345/678",
            snippet="Chief Marketing Officer · Present",
        ),
    ]
    # URL-based detection still treats this as LinkedIn evidence -> Medium
    assert score_executive(evs).confidence == ConfidenceScore.MEDIUM
