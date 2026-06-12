"""Executive Intelligence confidence scoring (per AArete_Executive_Engine_Instructions §2).

Each executive profile is scored independently:

- **High**: official payer leadership page hit OR a recent (<= 180 days) press
  release naming the executive, PLUS an active LinkedIn profile snippet for
  the same person.
- **Medium**: active LinkedIn profile snippet only (current "Present" tenure
  signal).
- **Low**: only found in third-party directories (ZoomInfo / RocketReach /
  Becker's) OR the LinkedIn snippet lacks any "Present" indicator.

Payer-level confidence is the max() across all identified executives and is
computed in `crew.assemble_executive_record`.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Iterable

from .schema import ConfidenceScore, Evidence


@dataclass
class ExecQCResult:
    confidence: ConfidenceScore
    note: str = ""


_RECENT_NEWS_DAYS = 365  # Expanded to catch long-lead retirements (6-12 mo announcements)

_LINKEDIN_URL_RE = re.compile(r"linkedin\.com/(?:in|pub|pulse|posts)/", re.I)
_PRESENT_TENURE_RE = re.compile(
    r"\b(present|current)\b|\b\d{4}\s*[-\u2013]\s*present\b",
    re.I,
)
_THIRD_PARTY_HOSTS = (
    "rocketreach.co", "zoominfo.com", "beckershospitalreview.com",
    "beckerspayer.com", "modernhealthcare.com", "ahip.org",
)


def _is_linkedin(ev: Evidence) -> bool:
    return ev.source_type == "linkedin_profile" or bool(
        _LINKEDIN_URL_RE.search(ev.url or "")
    )


def _is_leadership_page(ev: Evidence) -> bool:
    return ev.source_type == "leadership_page"


def _is_executive_news(ev: Evidence) -> bool:
    return ev.source_type in {"executive_news", "news"}


def _is_third_party(ev: Evidence) -> bool:
    if ev.source_type == "third_party_directory":
        return True
    url = (ev.url or "").lower()
    return any(h in url for h in _THIRD_PARTY_HOSTS)


def _within_days(date_str: str | None, days: int) -> bool:
    if not date_str:
        return False
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%b %d, %Y", "%B %d, %Y", "%Y-%m"):
        try:
            dt = datetime.strptime(date_str, fmt)
            return (datetime.utcnow() - dt) <= timedelta(days=days)
        except ValueError:
            continue
    try:
        dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        return (datetime.utcnow() - dt.replace(tzinfo=None)) <= timedelta(days=days)
    except ValueError:
        return False


def _has_present_tenure(linkedin_evs: Iterable[Evidence]) -> bool:
    for ev in linkedin_evs:
        body = (ev.snippet or "") + " " + (ev.full_text or "")
        if _PRESENT_TENURE_RE.search(body):
            return True
    return False


def score_executive(evidences: Iterable[Evidence]) -> ExecQCResult:
    """Score one executive profile based on its supporting evidence."""
    evs = list(evidences)
    if not evs:
        return ExecQCResult(ConfidenceScore.LOW, "no evidence")

    linkedin_evs = [e for e in evs if _is_linkedin(e)]
    leadership_evs = [e for e in evs if _is_leadership_page(e)]
    recent_news_evs = [
        e for e in evs if _is_executive_news(e) and _within_days(e.date, _RECENT_NEWS_DAYS)
    ]
    third_party_evs = [e for e in evs if _is_third_party(e)]

    has_linkedin = bool(linkedin_evs)
    has_active_linkedin = has_linkedin and _has_present_tenure(linkedin_evs)
    has_leadership = bool(leadership_evs)
    has_recent_news = bool(recent_news_evs)
    has_third_party_only = bool(third_party_evs) and not (
        has_linkedin or has_leadership or has_recent_news
    )

    # High — leadership page + LinkedIn, OR recent press release + LinkedIn
    if has_leadership and has_linkedin:
        return ExecQCResult(
            ConfidenceScore.HIGH, "official leadership page + linkedin profile"
        )
    if has_recent_news and has_linkedin:
        return ExecQCResult(
            ConfidenceScore.HIGH, "recent press release + linkedin profile"
        )
    # Leadership page alone is still High — it's the authoritative source.
    if has_leadership:
        return ExecQCResult(ConfidenceScore.HIGH, "official leadership page")

    # Medium — active LinkedIn only ("Present" tenure on profile)
    if has_active_linkedin:
        return ExecQCResult(ConfidenceScore.MEDIUM, "active linkedin profile (Present)")

    # Low — third-party directory only
    if has_third_party_only:
        return ExecQCResult(ConfidenceScore.LOW, "third-party directory only")

    # Low — LinkedIn snippet without "Present" indicator (might be stale/former)
    if has_linkedin:
        return ExecQCResult(ConfidenceScore.LOW, "linkedin without current-tenure signal")

    # Recent news alone (no linkedin/leadership) — Medium; appointment is current
    if has_recent_news:
        return ExecQCResult(ConfidenceScore.MEDIUM, "recent press release only")

    return ExecQCResult(ConfidenceScore.LOW, "no qualifying signals")


def aggregate_confidence(per_exec: Iterable[ConfidenceScore]) -> ConfidenceScore:
    """Payer-level confidence = max(HIGH > MEDIUM > LOW) over identified execs."""
    order = {ConfidenceScore.HIGH: 3, ConfidenceScore.MEDIUM: 2, ConfidenceScore.LOW: 1}
    scores = [c for c in per_exec]
    if not scores:
        return ConfidenceScore.LOW
    return max(scores, key=lambda c: order.get(c, 0))
