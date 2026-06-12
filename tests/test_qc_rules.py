from datetime import datetime, timedelta

from payer_intel.qc import score
from payer_intel.schema import ConfidenceScore, Evidence, SalesforceProduct, UsageVerdict


def _d(days_ago: int) -> str:
    return (datetime.utcnow() - timedelta(days=days_ago)).strftime("%Y-%m-%d")


def test_case_study_is_high_yes():
    evs = [Evidence(source_type="case_study", url="https://salesforce.com/x", date=_d(900))]
    r = score(SalesforceProduct.HEALTH_CLOUD, evs)
    assert r.verdict == UsageVerdict.YES
    assert r.confidence == ConfidenceScore.HIGH


def test_recent_job_plus_tech_is_high_yes():
    evs = [
        Evidence(source_type="job_posting", url="https://j", date=_d(60)),
        Evidence(source_type="technographic", url="https://t"),
    ]
    r = score(SalesforceProduct.SERVICE_CLOUD, evs)
    assert r.confidence == ConfidenceScore.HIGH
    assert r.verdict == UsageVerdict.YES


def test_recent_job_only_is_medium_likely():
    evs = [Evidence(source_type="job_posting", url="https://j", date=_d(120))]
    r = score(SalesforceProduct.SALES_CLOUD, evs)
    assert r.confidence == ConfidenceScore.MEDIUM
    assert r.verdict == UsageVerdict.LIKELY


def test_stale_only_is_low_unknown():
    evs = [Evidence(source_type="review", url="https://r", date=_d(750))]
    r = score(SalesforceProduct.MARKETING_CLOUD, evs)
    assert r.confidence == ConfidenceScore.LOW


def test_no_evidence_unknown():
    r = score(SalesforceProduct.DATA_CLOUD, [])
    assert r.verdict == UsageVerdict.UNKNOWN
    assert r.confidence == ConfidenceScore.LOW


def test_tech_only_is_medium_likely():
    evs = [Evidence(source_type="technographic", url="https://t")]
    r = score(SalesforceProduct.EXPERIENCE_CLOUD, evs)
    assert r.confidence == ConfidenceScore.MEDIUM
    assert r.verdict == UsageVerdict.LIKELY


def test_recent_job_plus_recent_review_is_high_yes():
    evs = [
        Evidence(source_type="job_posting", url="https://j", date=_d(30)),
        Evidence(source_type="review", url="https://r", date=_d(200)),
    ]
    r = score(SalesforceProduct.HEALTH_CLOUD, evs)
    assert r.verdict == UsageVerdict.YES
    assert r.confidence == ConfidenceScore.HIGH
    assert "recent job" in r.note.lower()


def test_recent_job_plus_recent_news_is_high_yes():
    evs = [
        Evidence(source_type="job_posting", url="https://j", date=_d(45)),
        Evidence(source_type="news", url="https://n", date=_d(90)),
    ]
    r = score(SalesforceProduct.SERVICE_CLOUD, evs)
    assert r.verdict == UsageVerdict.YES
    assert r.confidence == ConfidenceScore.HIGH


def test_recent_news_only_is_medium_likely():
    evs = [Evidence(source_type="news", url="https://n", date=_d(100))]
    r = score(SalesforceProduct.MARKETING_CLOUD, evs)
    assert r.verdict == UsageVerdict.LIKELY
    assert r.confidence == ConfidenceScore.MEDIUM


def test_stale_signals_only_flagged_in_note():
    evs = [
        Evidence(source_type="job_posting", url="https://j", date=_d(900)),
        Evidence(source_type="news", url="https://n", date=_d(900)),
    ]
    r = score(SalesforceProduct.SALES_CLOUD, evs)
    assert r.verdict == UsageVerdict.UNKNOWN
    assert r.confidence == ConfidenceScore.LOW
    assert "stale" in r.note.lower()


# ── LinkedIn promotion rules (v6.1 / Aarete diagnosis) ──────────────────────


def test_single_linkedin_profile_is_medium_likely():
    evs = [
        Evidence(
            source_type="review",
            url="https://www.linkedin.com/in/jane-doe-12345/",
            snippet="Spearheaded implementation of Marketing Cloud at Florida Blue.",
        )
    ]
    r = score(SalesforceProduct.MARKETING_CLOUD, evs)
    assert r.verdict == UsageVerdict.LIKELY
    assert r.confidence == ConfidenceScore.MEDIUM
    assert "linkedin" in r.note.lower()


def test_two_linkedin_profiles_promote_to_high_yes():
    evs = [
        Evidence(
            source_type="review",
            url="https://www.linkedin.com/in/jane-doe/",
            snippet="Salesforce Marketing Cloud Developer at Florida Blue.",
        ),
        Evidence(
            source_type="review",
            url="https://www.linkedin.com/in/john-roe/",
            snippet="Marketing Cloud admin on the digital team at Florida Blue.",
        ),
    ]
    r = score(SalesforceProduct.MARKETING_CLOUD, evs)
    assert r.verdict == UsageVerdict.YES
    assert r.confidence == ConfidenceScore.HIGH
    assert "linkedin" in r.note.lower()


def test_linkedin_plus_technographic_is_high_yes():
    evs = [
        Evidence(
            source_type="review",
            url="https://www.linkedin.com/in/jane-doe/",
            snippet="Health Cloud admin at Cigna.",
        ),
        Evidence(source_type="technographic", url="https://cigna.com/contact-us"),
    ]
    r = score(SalesforceProduct.HEALTH_CLOUD, evs)
    assert r.verdict == UsageVerdict.YES
    assert r.confidence == ConfidenceScore.HIGH


def test_linkedin_plus_recent_job_is_high_yes():
    evs = [
        Evidence(
            source_type="review",
            url="https://www.linkedin.com/in/jane-doe/",
            snippet="Salesforce architect at Cigna.",
        ),
        Evidence(source_type="job_posting", url="https://j", date=_d(60)),
    ]
    r = score(SalesforceProduct.SALES_CLOUD, evs)
    assert r.verdict == UsageVerdict.YES
    assert r.confidence == ConfidenceScore.HIGH


def test_linkedin_pulse_article_counts_as_linkedin():
    evs = [
        Evidence(
            source_type="review",
            url="https://www.linkedin.com/pulse/our-salesforce-journey-jane-doe/",
            snippet="Authored by Cigna's VP of Engineering.",
        )
    ]
    r = score(SalesforceProduct.MARKETING_CLOUD, evs)
    assert r.verdict == UsageVerdict.LIKELY
    assert r.confidence == ConfidenceScore.MEDIUM


def test_two_linkedin_urls_dedup_by_url():
    """Same URL twice should still count as 1 distinct employee."""
    evs = [
        Evidence(
            source_type="review",
            url="https://www.linkedin.com/in/jane-doe/",
            snippet="A",
        ),
        Evidence(
            source_type="review",
            url="https://www.linkedin.com/in/jane-doe/",
            snippet="B",
        ),
    ]
    r = score(SalesforceProduct.HEALTH_CLOUD, evs)
    assert r.verdict == UsageVerdict.LIKELY
    assert r.confidence == ConfidenceScore.MEDIUM


def test_non_linkedin_review_url_does_not_trigger_promotion():
    """A G2 review URL is still a 'review' source_type but is not a LinkedIn
    profile; it must fall through to the original stale/recent paths."""
    evs = [
        Evidence(
            source_type="review",
            url="https://www.g2.com/some-payer",
            snippet="generic mention",
            date=_d(900),
        )
    ]
    r = score(SalesforceProduct.SALES_CLOUD, evs)
    assert r.verdict == UsageVerdict.UNKNOWN
    assert r.confidence == ConfidenceScore.LOW
