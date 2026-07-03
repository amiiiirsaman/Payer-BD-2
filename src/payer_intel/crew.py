from __future__ import annotations

import csv
import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Iterable

from crewai import Crew, Process, Task

from urllib.parse import urlparse

from bs4 import BeautifulSoup

from .agents import (
    case_study_agent,
    classifier_agent,
    export_agent,
    jobs_agent,
    news_agent,
    orchestrator_agent,
    qc_agent,
    recency_agent,
    reviews_agent,
    target_identification_agent,
    technographic_agent,
)
from .export import write_excel, write_excel_executive
from .qc import score as qc_score
from .qc_exec import (
    aggregate_confidence as exec_aggregate_confidence,
    score_executive as exec_score,
    _within_days,
)
from .schema import (
    ConfidenceScore,
    Evidence,
    EXCEL_COLUMNS,
    EXECUTIVE_EXCEL_COLUMNS,
    EXECUTIVE_TITLE_MAP,
    ExecutivePayerRecord,
    ExecutiveProfile,
    ExecutiveRole,
    PastJob,
    PRODUCT_COLUMNS,
    PayerRecord,
    SalesforceProduct,
    UsageVerdict,
)
from .tools.search_api import SearchApiClient, SearchQuotaExceeded
from .tools.tech_fingerprint import fingerprint_domain

log = logging.getLogger(__name__)


# Canonical case-study URLs that Google Search routinely ranks outside the top
# 20; injected directly so the body enricher always fetches them. Only true
# deployment case studies — not aspirational blog posts — should appear here,
# because qc rule 1 auto-promotes any case_study evidence to Yes/High.
_KNOWN_CASE_STUDIES: dict[str, str] = {
    "UnitedHealthcare": "https://www.salesforce.com/customer-success-stories/united-healthcare/",
    "Humana Inc.": "https://www.salesforce.com/customer-success-stories/humana/",
}


# ─────────────────────────────────────────────────────────────────────────────
# Seed loading (Agent 2: Target Identification)
# ─────────────────────────────────────────────────────────────────────────────
def load_seed(path: Path) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            rows.append({k.strip(): (v or "").strip() for k, v in row.items()})
    return rows


# ─────────────────────────────────────────────────────────────────────────────
# Deterministic sourcing (Agents 3–7) — fast, low-cost, deterministic
# ─────────────────────────────────────────────────────────────────────────────
_REVIEW_SITES = "site:g2.com OR site:capterra.com OR site:trustradius.com"
_PARTNER_SITES = (
    "site:salesforce.com/customer-success-stories "
    "OR site:salesforce.com/news/stories "
    "OR site:salesforce.com/blog "
    "OR site:salesforce.com/resources/customer-stories "
    "OR site:silverlinecrm.com OR site:penrod.co "
    "OR site:slalom.com OR site:deloitte.com OR site:accenture.com "
    "OR site:cognizant.com OR site:ibm.com"
)
_COMMUNITY_SITES = "site:trailhead.salesforce.com OR site:appexchange.salesforce.com"
# CIO / executive interview & trade press sources. Surfaced the Geisinger /
# Salesforce Marketing+Health Cloud quote that v5 missed (Aarete MS-01).
_CIO_INTERVIEW_SITES = (
    "site:deloitte.wsj.com OR site:deloitte.com/insights OR site:hbr.org "
    "OR site:healthcareitnews.com OR site:modernhealthcare.com "
    "OR site:healthtechmagazine.net"
)
# LinkedIn posts + member profiles + Pulse articles. Snippet-only evidence
# (LinkedIn blocks unauthenticated httpx, so no _FETCH_DOMAINS entry).
_LINKEDIN_SITES = (
    "site:linkedin.com/posts/ OR site:linkedin.com/in/ OR site:linkedin.com/pulse/"
)
_LINKEDIN_TITLE_TERMS = (
    '"Salesforce Marketing Cloud Specialist" OR "Health Cloud Administrator" '
    'OR "Salesforce Developer" OR "Agentforce Developer" OR "Vlocity"'
)
_JOB_PRODUCT_TERMS = (
    'Salesforce OR "Sales Cloud" OR "Service Cloud" OR "Health Cloud" '
    'OR "Marketing Cloud" OR "Experience Cloud" OR "Data Cloud" '
    'OR Pardot OR ExactTarget OR "CRM Analytics" OR Agentforce OR Vlocity'
)
_NEWS_PRODUCT_TERMS = (
    'Salesforce OR "Health Cloud" OR "Data Cloud" OR "Marketing Cloud" '
    'OR Agentforce'
)


def build_name_clause(name: str, aliases_raw: str | None) -> str:
    names = [name] + [a.strip() for a in (aliases_raw or "").split("|") if a.strip()]
    seen: set[str] = set()
    deduped: list[str] = []
    for n in names:
        if n.lower() in seen:
            continue
        seen.add(n.lower())
        deduped.append(n)
    if len(deduped) == 1:
        return f'"{deduped[0]}"'
    return "(" + " OR ".join(f'"{n}"' for n in deduped) + ")"


def build_excludes_set(payer: dict[str, str]) -> set[str]:
    """Lowercased set of sibling-entity names to reject during attribution.

    Mirrors build_name_clause but for the optional `search_excludes` CSV
    column. Independence Blue Cross excludes "AmeriHealth Caritas" so its
    sibling entity's job postings don't get cross-attributed (Aarete MS-05).
    """
    raw = payer.get("search_excludes") or ""
    return {x.strip().lower() for x in raw.split("|") if x.strip()}


def _safe_search(fn, *args, **kwargs) -> list[dict]:
    try:
        return fn(*args, **kwargs)
    except SearchQuotaExceeded:
        log.warning("SearchApi quota exceeded; skipping further calls.")
        return []
    except Exception as e:  # noqa: BLE001  – best-effort sourcing
        log.warning("Search call failed: %s", e)
        return []


def gather_evidence(payer: dict[str, str], client: SearchApiClient) -> list[Evidence]:
    name = payer["payer_name"]
    domain = payer.get("domain", "")
    name_clause = build_name_clause(name, payer.get("search_aliases"))
    evidence: list[Evidence] = []

    if name in _KNOWN_CASE_STUDIES:
        evidence.append(
            Evidence(
                source_type="case_study",
                url=_KNOWN_CASE_STUDIES[name],
                snippet="Official Salesforce case study.",
                date=None,
            )
        )

    # Agent 3 — Jobs (broadened: explicit cloud names catch postings where
    # 'Salesforce' is not adjacent to the product name)
    for r in _safe_search(client.google_jobs, f"{name_clause} ({_JOB_PRODUCT_TERMS})", num=20):
        evidence.append(
            Evidence(
                source_type="job_posting",
                url=r.get("link", "") or "",
                snippet=(r.get("snippet") or "")[:1500],
                date=r.get("date"),
            )
        )

    # Agent 4 — News (broadened with product terms)
    for r in _safe_search(client.google_news, f"{name_clause} ({_NEWS_PRODUCT_TERMS})", num=20):
        evidence.append(
            Evidence(
                source_type="news",
                url=r.get("link", "") or "",
                snippet=r.get("snippet", ""),
                date=r.get("date"),
            )
        )

    # Agent 4b — Dreamforce / Agentforce / Einstein Copilot sessions on salesforce.com
    # (not indexed as news; eligible for v6 page-body fetch since salesforce.com is whitelisted)
    for r in _safe_search(
        client.google,
        f'site:salesforce.com {name_clause} ("Agentforce" OR "Dreamforce" OR "Einstein Copilot")',
        num=10,
    ):
        evidence.append(
            Evidence(
                source_type="case_study",
                url=r.get("link", "") or "",
                snippet=(r.get("snippet") or "")[:1200],
                date=r.get("date"),
            )
        )

    # Agent 5 — Reviews
    for r in _safe_search(client.google, f"{_REVIEW_SITES} {name_clause} Salesforce", num=20):
        evidence.append(
            Evidence(
                source_type="review",
                url=r.get("link", "") or "",
                snippet=r.get("snippet", ""),
                date=r.get("date"),
            )
        )

    # Agent 6 — Case studies / partners
    for r in _safe_search(client.google, f"{_PARTNER_SITES} {name_clause} Salesforce case study", num=20):
        evidence.append(
            Evidence(
                source_type="case_study",
                url=r.get("link", "") or "",
                snippet=r.get("snippet", ""),
                date=r.get("date"),
            )
        )

    # Agent 6.5 — Trailblazer Community / AppExchange (high-signal source for
    # CVS Sales Cloud and BCBSM Experience Cloud in prior runs). Mapped to
    # 'review' so existing QC recency rules apply.
    for r in _safe_search(client.google, f"{_COMMUNITY_SITES} {name_clause}", num=20):
        evidence.append(
            Evidence(
                source_type="review",
                url=r.get("link", "") or "",
                snippet=r.get("snippet", ""),
                date=r.get("date"),
            )
        )

    # Agent 6.6 — CIO / executive interviews & healthcare trade press.
    # The Geisinger Marketing+Health Cloud quote ran on deloitte.wsj.com which
    # was not previously in scope (Aarete MS-01).
    for r in _safe_search(
        client.google,
        f"{_CIO_INTERVIEW_SITES} {name_clause} Salesforce",
        num=10,
    ):
        evidence.append(
            Evidence(
                source_type="case_study",
                url=r.get("link", "") or "",
                snippet=(r.get("snippet") or "")[:1200],
                date=r.get("date"),
            )
        )

    # Agent 6.7 — LinkedIn posts/profiles/pulse. Surfaces first-person
    # platform-usage statements (Sanford intern, IBX developer) and
    # product-specific employee titles (Aarete MS-02, MS-05, MS-06). Snippet-
    # only — LinkedIn blocks unauthenticated httpx. Mapped to 'review' so QC's
    # recent_review path applies.
    for r in _safe_search(
        client.google,
        f'{_LINKEDIN_SITES} {name_clause} '
        f'(Salesforce OR "Marketing Cloud" OR "Health Cloud" OR Vlocity OR Agentforce)',
        num=15,
    ):
        evidence.append(
            Evidence(
                source_type="review",
                url=r.get("link", "") or "",
                snippet=r.get("snippet", ""),
                date=r.get("date"),
            )
        )

    # Agent 6.8 — LinkedIn employee-title pass. A named employee with a
    # product-specific title ("Salesforce Marketing Cloud Specialist") is
    # Tier-1 evidence per Aarete Part 3.
    for r in _safe_search(
        client.google,
        f'site:linkedin.com/in/ {name_clause} ({_LINKEDIN_TITLE_TERMS})',
        num=10,
    ):
        evidence.append(
            Evidence(
                source_type="review",
                url=r.get("link", "") or "",
                snippet=r.get("snippet", ""),
                date=r.get("date"),
            )
        )

    # Agent 7 — Technographic fingerprint
    for h in fingerprint_domain(domain):
        evidence.append(
            Evidence(
                source_type="technographic",
                url=h.url,
                snippet=f"matched marker '{h.matched}'",
                matched_product=h.product,
            )
        )

    # drop empties / dedupe by (source_type, url)
    seen: set[tuple[str, str]] = set()
    out: list[Evidence] = []
    for e in evidence:
        if not e.url:
            continue
        key = (e.source_type, e.url)
        if key in seen:
            continue
        seen.add(key)
        out.append(e)
    out = _enrich_with_page_bodies(out)
    return out


# Agent 8 — Page Body Enricher: domains whose pages reliably contain named
# Salesforce product evidence beyond the search snippet teaser.
_FETCH_DOMAINS: frozenset[str] = frozenset({
    # Salesforce-owned
    "salesforce.com",
    "trailhead.salesforce.com",
    # Payer-owned newsrooms / IR
    "news.blueshieldca.com",
    "newsroom.humana.com",
    "newsroom.cigna.com",
    "newsroom.elevancehealth.com",
    "ir.molinahealthcare.com",
    "newsroom.kaiserpermanente.org",
    "newsroom.highmark.com",
    # Wire services
    "businesswire.com",
    "prnewswire.com",
    "globenewswire.com",
    # Trade press
    "fiercehealthcare.com",
    "healthcaredive.com",
    "mobihealthnews.com",
    "medcitynews.com",
    "beckershospitalreview.com",
    "beckerspayer.com",
    "ahip.org",
    # CIO / executive interview sources (Aarete MS-01)
    "deloitte.wsj.com",
    "deloitte.com",
    "hbr.org",
    "healthcareitnews.com",
    "modernhealthcare.com",
    "healthtechmagazine.net",
    # Executive intelligence pipeline (--mode executive): third-party
    # directories for cross-referencing tenure & past firms. LinkedIn is
    # intentionally NOT included — its pages are auth-walled and httpx
    # returns a login redirect; we rely on SearchApi snippets only.
    "zoominfo.com",
    "rocketreach.co",
})
_MAX_BODY_CHARS = 4000
_WS_RE = re.compile(r"\s+")


def _enrich_with_page_bodies(evidence: list[Evidence]) -> list[Evidence]:
    from .tools.fetcher import fetch

    for ev in evidence:
        if not ev.url:
            continue
        host = (urlparse(ev.url).hostname or "").lower()
        if not any(host == d or host.endswith("." + d) for d in _FETCH_DOMAINS):
            continue
        resp = fetch(ev.url, timeout=15.0)
        if resp is None:
            continue
        try:
            soup = BeautifulSoup(resp.text, "html.parser")
            for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
                tag.decompose()
            text = soup.get_text(separator=" ", strip=True)
            cleaned = _WS_RE.sub(" ", text).strip()
            # AppExchange pages are JS-rendered; httpx returns a near-empty shell.
            # Keep the search snippet rather than overwriting with empty body.
            if host.endswith("appexchange.salesforce.com") and len(cleaned) < 200:
                continue
            ev.full_text = cleaned[:_MAX_BODY_CHARS]
        except Exception:  # noqa: BLE001 — best-effort enrichment
            continue
    return evidence


# ─────────────────────────────────────────────────────────────────────────────
# Agent 8b — Deterministic Product Extractor (Layer 1)
# Runs on fetched page bodies only. Python regex, no LLM, no API call.
# Only fires when a payer alias appears in the body (false-positive guard).
# ──────────────────────────────────────────────────────────────────────────
# Keys MUST equal SalesforceProduct.value exactly (no regex escaping in keys).
# Values are regex patterns; escape special characters only inside patterns.
_PRODUCT_PATTERNS: dict[str, list[str]] = {
    "Marketing Cloud Account Engagement (Pardot)": [
        r"Pardot",
        r"Account Engagement",
    ],
    "Agentforce for Healthcare": [
        r"Agentforce for Healthcare",
        r"Agentforce for Health",
        r"Einstein Copilot for Health",
        r"Agentforce",
    ],
    "Health Cloud": [
        r"Health Cloud",
        r"Care Connect",
        r"prior authori[sz]ation",
        r"Vlocity Health",
        r"Vlocity Insurance",
        r"OmniStudio",
        r"Salesforce Industries",
        r"Health Cloud Industry Edition",
    ],
    "Life Sciences Cloud": [r"Life Sciences Cloud"],
    "Financial Services Cloud": [r"Financial Services Cloud"],
    "Revenue Cloud (CPQ)": [
        r"Revenue Cloud",
        r"\bCPQ\b",
        r"SteelBrick",
    ],
    "Data Cloud": [
        r"Data Cloud",
        r"CRM Analytics",
        r"Tableau CRM",
        r"Einstein Analytics",
    ],
    "Marketing Cloud": [
        r"\bMarketing Cloud\b",
        r"Marketing Platform",
        r"\bSFMC\b",
        r"ExactTarget",
        r"Email Studio",
        r"\bet\.com\b",
    ],
    "Experience Cloud": [
        r"\bExperience Cloud\b",
        r"Community Cloud",
        r"my\.site\.com",
        r"Digital Experience Cloud",
    ],
    "Service Cloud": [
        r"\bService Cloud\b",
        r"Field Service Lightning",
        r"\bFSL\b",
        r"Service Console",
        r"Omni.Channel",
    ],
    "Sales Cloud": [
        r"\bSales Cloud\b",
        r"CareIQ",
        r"Care IQ",
    ],
}

# Maximum distance (chars) between a payer-alias mention and a product-pattern
# match for the match to count. Prevents pages that mention the payer once in a
# header and discuss unrelated Salesforce products elsewhere from producing
# false-positive Layer 1 hits (e.g. Devoted Health / Alameda Alliance picking
# up Agentforce from distant boilerplate).
_PROXIMITY_WINDOW = 600

# "Agentforce" appears on nearly every Salesforce marketing page, Trailhead
# tutorial and blog post. Standard proximity is not strong enough — require
# both a payer alias AND a deployment-signal word inside a tighter window.
_AGENTFORCE_PROXIMITY = 300
_AGENTFORCE_DEPLOYMENT_INDICATORS = {
    "deploy", "implement", "launch", "partner", "customer story",
    "use case", "solution", "contract", "agreement", "pilot",
    "rollout", "go live", "go-live", "production", "signed",
}

# Phrases in the LLM narrative indicating it itself found no real evidence —
# used by _classify_with_llm to clear any spurious product mappings post-hoc.
# Phrases must signal a GLOBAL no-evidence verdict for the payer, not a
# per-product disclaimer (e.g. "no evidence for Marketing Cloud" is fine —
# the payer may still have Service Cloud).
_NO_EVIDENCE_PHRASES: tuple[str, ...] = (
    "no credible evidence of salesforce",
    "no credible evidence of any salesforce",
    "no credible evidence was found",
    "no credible evidence of product deployment",
    "no salesforce deployment evidence",
    "no salesforce product deployment",
    "does not appear to use salesforce",
    "does not appear to deploy salesforce",
    "no evidence of salesforce deployment",
    "no evidence of any salesforce",
)


# URLs to drop from BD output when the payer has no positive verdicts —
# generic Salesforce marketing/tutorial pages and payer's own-domain pages.
_NON_EVIDENCE_URL_PATTERNS: tuple[str, ...] = (
    "salesforce.com/eu/",
    "salesforce.com/nl/",
    "salesforce.com/es/",
    "salesforce.com/de/",
    "trailhead.salesforce.com/content/learn/",
)


def _alias_in_text(alias_lower: str, text_lower: str) -> bool:
    # Word-boundary alias match prevents short aliases (e.g. "Blue Shield"
    # for BCBS Louisiana) from spuriously matching unrelated contexts.
    if not alias_lower:
        return False
    return bool(re.search(r"\b" + re.escape(alias_lower) + r"\b", text_lower))


# Generic payer-industry terms that should NOT be used as the sole basis for
# tying an extracted current_employer back to a target payer.
_EMPLOYER_MATCH_STOPWORDS: frozenset[str] = frozenset({
    "of", "and", "the", "for", "a", "an", "to", "in", "&",
    "blue", "cross", "shield", "health", "care", "insurance",
    "plan", "plans", "company", "companies", "inc", "llc", "corp",
    "corporation", "group", "holdings", "holding", "services", "service",
    "system", "systems", "mutual", "healthcare", "medical",
})


def _is_valid_persona_match(extracted_title: str, persona: str) -> bool:
    """Ensure the extracted title matches the clinical requirements of the persona."""
    if not extracted_title:
        return True

    title_lower = extracted_title.lower()

    if persona in ["Chief Medical", "CMO"]:
        # Must have clinical keywords
        if not any(k in title_lower for k in ["medical", "health", "clinical", "physician", "md", "do"]):
            # Reject pure operations/growth titles
            if any(k in title_lower for k in ["medicaid", "government", "growth", "marketing", "operations"]):
                return False
    return True


def _employer_matches_payer(employer_lower: str, payer_aliases_lower: set[str]) -> bool:
    """Decide whether an LLM-extracted current employer plausibly belongs to a payer.

    Two-tier match: (1) bidirectional alias substring (handles "Humana" ↔ "Humana Inc."
    and "Blue KC" ↔ "Blue Cross and Blue Shield of Kansas City"); (2) distinctive-token
    overlap that tolerates parent-company / brand-variant references such as
    "Independence Health Group" → Independence Blue Cross or "Louisiana Blue" → BCBSLA,
    while still rejecting unrelated employers like "Curry Health Network" for CareOregon.
    """
    if not employer_lower:
        return True
    if any(a in employer_lower or employer_lower in a for a in payer_aliases_lower if a):
        return True
    employer_tokens = set(re.findall(r"[a-z0-9]+", employer_lower))
    if not employer_tokens:
        return False
    for alias in payer_aliases_lower:
        if not alias:
            continue
        for tok in re.findall(r"[a-z0-9]+", alias):
            if len(tok) >= 4 and tok not in _EMPLOYER_MATCH_STOPWORDS and tok in employer_tokens:
                return True
    return False


_DECEASED_SIGNALS: frozenset[str] = frozenset({
    "passed away", "has died", "have died", "died ", "death of",
    "obituary", "in memoriam", "rest in peace", "is survived by",
    "funeral", "memorial service", "posthumously",
})


def _is_known_deceased(name: str, evidence: list[Evidence]) -> bool:
    """Drop a candidate only when a deceased signal appears within 80 chars
    AFTER the candidate's full name AND no succession-context keyword
    appears between the name and the signal.

    v3.5.1: name-anchored forward-only window + succession-context guard.
    Catches direct obit phrasing where the name is the subject of the
    deceased verb ("Dr. Sam Ho, longtime CMO, passed away"). Rejects
    succession sentences where the deceased verb refers to a DIFFERENT
    person being replaced ("Tim Noel succeeds Brian Thompson, who passed
    away"). The succession guard checks whether words like "succeed",
    "replace", "after the death", etc. appear between the name and the
    signal \u2014 if so, the signal is about someone else.
    """
    name_lc = name.strip().lower()
    if len(name_lc) < 5:
        return False
    forward_window = 80
    name_len = len(name_lc)
    for ev in evidence:
        text_lc = ((ev.full_text or "") + " " + (ev.snippet or "")).lower()
        if name_lc not in text_lc:
            continue
        start = 0
        while True:
            pos = text_lc.find(name_lc, start)
            if pos == -1:
                break
            after_name = text_lc[pos + name_len : pos + name_len + forward_window]
            for sig in _DECEASED_SIGNALS:
                sig_pos = after_name.find(sig)
                if sig_pos == -1:
                    continue
                between = after_name[:sig_pos]
                if any(kw in between for kw in _SUCCESSION_CONTEXT_KEYWORDS):
                    # Signal is about a different person being replaced.
                    continue
                return True
            start = pos + 1
    return False


# v3.5.1: succession-context guard. If any of these words appear between
# the candidate's name and a deceased signal, the signal is about a
# DIFFERENT person (the one being replaced), not the candidate.
_SUCCESSION_CONTEXT_KEYWORDS: frozenset[str] = frozenset({
    # explicit succession verbs
    "succeed", "succeeding", "succeeds", "succession",
    "replace", "replacing", "replaces",
    # temporal / causal phrasings that mark a successor announcement
    "following", "after the", "in the wake", "wake of",
    "tragic", "untimely",
    # explicit "after/following the death" phrasings
    "after the death", "following the death", "following the passing",
    # memorials / honors
    "in memory of", "in honor of",
    # appointment-phrasing
    "appointed to replace", "named to replace", "named to succeed",
    "predecessor", "former ceo", "former president", "previous ceo",
})


_ALREADY_DEPARTED_SIGNALS: frozenset[str] = frozenset({
    "has departed", "have departed", "left the company", "no longer with",
    "stepped down", "has left", "have left", "departed in", "departed from",
    "is no longer", "has since left", "recently departed",
})


def _is_already_departed(departure_note: str | None) -> bool:
    """Return True when the departure note describes a past event (the exec
    is already gone), as opposed to an announced future retirement.

    Used to blank the slot in `assemble_executive_record` so BCBSLA's
    Tina Bourgeois ("departed in March 2026") shows '-' rather than her name,
    while a future-tense retirement ("retiring end of 2026") keeps the exec
    in their seat.
    """
    if not departure_note:
        return False
    note_lc = departure_note.lower()
    return any(sig in note_lc for sig in _ALREADY_DEPARTED_SIGNALS)


# Salesforce blog category/tag/author listings and paginated index pages
# aggregate teasers from unrelated articles. A payer name appearing on
# such a page is not evidence (Aarete FP-02, FP-07).
_ZERO_EVIDENCE_URL_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"/blog/category/", re.I),
    re.compile(r"/blog/tag/", re.I),
    re.compile(r"/blog/author/", re.I),
    re.compile(r"/blog/page/\d+/", re.I),
    re.compile(r"/blog/?$", re.I),
)


def _is_zero_evidence_url(url: str) -> bool:
    if not url:
        return False
    return any(p.search(url) for p in _ZERO_EVIDENCE_URL_PATTERNS)


# SI partner hosts whose brochures/whitepapers are only valid evidence when
# the target payer is literally named in the body (Aarete FP-06).
_SI_PARTNER_HOSTS: frozenset[str] = frozenset({
    "accenture.com", "deloitte.com", "ibm.com", "cognizant.com",
    "slalom.com", "silverlinecrm.com", "penrod.co",
})

# URL-like tokens stripped before checking for visible payer mentions, so
# a sibling-file reference such as "accenture-global-anthem-pov.pdf" does
# not satisfy FP-06 for an Anthem/Elevance payer alias.
_URL_LIKE_RE = re.compile(
    r"https?://\S+"
    r"|www\.\S+"
    r"|\S*\.(?:pdf|html?|aspx|docx?|php|jsp|xml)\b\S*",
    re.I,
)


def _strip_url_like(text: str) -> str:
    return _URL_LIKE_RE.sub(" ", text)


def _si_partner_requires_payer_mention(
    url: str,
    body: str | None,
    payer_aliases_lower: set[str],
    snippet: str | None = None,
) -> bool:
    """Return True ('drop this evidence') for an SI-partner page that never
    names the payer in its visible body or snippet text. URL paths and
    sibling-file references inside the text are stripped before the alias
    check (Aarete FP-06): a Salesforce Service Cloud Accenture brochure
    that only references the payer via a link like ``anthem-pov.pdf`` is
    not evidence for Anthem/Elevance Health.

    When both body and snippet are empty the item is kept; the LLM will
    see the URL-only item with the FP-06 guardrail already in its prompt.
    """
    if not url:
        return False
    host = (urlparse(url).hostname or "").lower()
    if not any(host == h or host.endswith("." + h) for h in _SI_PARTNER_HOSTS):
        return False
    visible = _strip_url_like(((body or "") + " " + (snippet or "")).lower())
    if not visible.strip():
        return False
    return not any(_alias_in_text(a, visible) for a in payer_aliases_lower)


# Customer-verb proximity check for salesforce.com /blog/ articles. Without
# a deployment verb near a payer mention, the payer is just a backdrop in
# an industry thought-leadership post and the article is not evidence
# (Aarete FP-01).
_CUSTOMER_VERB_RE = re.compile(
    r"\b(implement(?:ed|s|ing)?|deployed|deploys?|deploying|uses?|using|"
    r"selected|chose|migrated\s+to|customer\s+of|partnered\s+with|"
    r"is\s+using|adopted)\b",
    re.I,
)
_CUSTOMER_VERB_WINDOW = 400


def _salesforce_blog_lacks_customer_verb(
    url: str, body: str | None, payer_aliases_lower: set[str]
) -> bool:
    """Return True ('drop this evidence') for a salesforce.com /blog/ URL
    that surfaced via search but never pairs a payer mention with a
    customer/deployment verb within ±_CUSTOMER_VERB_WINDOW chars."""
    if not url:
        return False
    host = (urlparse(url).hostname or "").lower()
    if not (host == "salesforce.com" or host == "www.salesforce.com"):
        return False
    if "/blog/" not in url.lower():
        return False
    if not body:
        # Snippet-only \u2014 keep; LLM sees the FP-01 guardrail in the prompt.
        return False
    body_lower = body.lower()
    for alias in payer_aliases_lower:
        for m in re.finditer(r"\b" + re.escape(alias) + r"\b", body_lower):
            start = max(0, m.start() - _CUSTOMER_VERB_WINDOW)
            end = m.end() + _CUSTOMER_VERB_WINDOW
            if _CUSTOMER_VERB_RE.search(body_lower[start:end]):
                return False
    return True


def _evidence_body_contains_exclude(
    body: str | None, excludes_lower: set[str], payer_aliases_lower: set[str]
) -> bool:
    """Return True ('drop this evidence') if the body names a sibling-entity
    exclude term but never names the primary payer within ±_CUSTOMER_VERB_WINDOW
    chars of any alias. AmeriHealth Caritas job postings should not be
    cross-attributed to Independence Blue Cross (Aarete MS-05)."""
    if not body or not excludes_lower:
        return False
    body_lower = body.lower()
    hit_exclude = any(_alias_in_text(x, body_lower) for x in excludes_lower)
    if not hit_exclude:
        return False
    # Body mentions a sibling. Keep only if it ALSO mentions the primary
    # payer with no co-mention of the sibling in the same window.
    return not any(_alias_in_text(a, body_lower) for a in payer_aliases_lower)


def _should_drop_evidence(
    ev: Evidence, payer_aliases_lower: set[str], excludes_lower: set[str]
) -> bool:
    """Composite URL/body gate used by both the deterministic extractor and
    the LLM evidence_blob builder. Centralises FP-01/FP-02/FP-06/FP-07 and
    MS-05 rejection logic so the two layers stay consistent."""
    if _is_zero_evidence_url(ev.url):
        return True
    body = ev.full_text
    if _si_partner_requires_payer_mention(
        ev.url, body, payer_aliases_lower, snippet=ev.snippet
    ):
        return True
    if _salesforce_blog_lacks_customer_verb(ev.url, body, payer_aliases_lower):
        return True
    if _evidence_body_contains_exclude(body, excludes_lower, payer_aliases_lower):
        return True
    return False


# Single-word aliases that are common English words and produce cross-payer
# contamination when used for proximity matching (e.g. "devoted" appearing
# near "Health Cloud" in an NYU Langone case study triggers a false positive
# for Devoted Health). Filtered out of the proximity guard but still usable
# by Layer 2 (LLM classifier) which has full context.
_WEAK_ALIASES: frozenset[str] = frozenset({
    "health", "care", "blue", "cross", "plan", "group", "first",
    "community", "devoted", "oscar", "alliance", "essence", "kaiser",
    "sanford", "emblem", "horizon", "independent", "priority", "partnership",
    "point32health", "medstar", "geisinger", "excellus", "fallon",
    "cigna", "aetna", "humana", "anthem", "optum",
})


def _is_strong_alias(alias: str) -> bool:
    # Aliases used for proximity matching must be either multi-word,
    # an acronym (all uppercase, length ≥ 3), or a distinctive long token
    # not in the common-English-word stoplist. Common single words are
    # rejected to prevent cross-payer contamination.
    a = alias.strip()
    if not a:
        return False
    if " " in a:
        return True
    if a.isupper() and len(a) >= 3:
        return True
    return len(a) > 6 and a.lower() not in _WEAK_ALIASES


def _extract_products_from_body(
    ev: Evidence,
    payer_aliases: set[str],
    excludes: set[str] | None = None,
) -> set[str]:
    """Layer 1 deterministic extractor.

    Returns the set of SalesforceProduct.value strings literally present in
    ev.full_text within ±_PROXIMITY_WINDOW chars of a payer-alias mention.
    Agentforce additionally requires a deployment-indicator word in the same
    tight window (±_AGENTFORCE_PROXIMITY). Returns empty set when full_text
    is None (snippet-only items stay on the LLM path), when no alias
    mentions appear in the body, or when URL/body gating rejects the item.
    """
    if not ev.full_text:
        return set()
    payer_aliases_lower = {a.lower() for a in payer_aliases if a}
    excludes_lower = excludes or set()
    if _should_drop_evidence(ev, payer_aliases_lower, excludes_lower):
        return set()
    body = ev.full_text
    body_lower = body.lower()
    # Strict alias set: multi-word, acronym, or distinctive long token.
    # Falls back to the full payer name if every alias is filtered out.
    strong = {a for a in payer_aliases if _is_strong_alias(a)}
    if not strong:
        strong = set(payer_aliases)
    strong_aliases_lower = {a.lower() for a in strong if a}
    alias_positions: list[int] = []
    for needle in strong_aliases_lower:
        for m in re.finditer(r"\b" + re.escape(needle) + r"\b", body_lower):
            alias_positions.append(m.start())
    if not alias_positions:
        return set()
    found: set[str] = set()

    # Agentforce: tighter check — needs payer alias AND deployment indicator
    # in same ±_AGENTFORCE_PROXIMITY window. Generic nav/tutorial pages fail.
    for m in re.finditer(r"agentforce", body_lower):
        win_start = max(0, m.start() - _AGENTFORCE_PROXIMITY)
        win_end = m.end() + _AGENTFORCE_PROXIMITY
        window = body_lower[win_start:win_end]
        if any(_alias_in_text(a, window) for a in strong_aliases_lower) and any(
            ind in window for ind in _AGENTFORCE_DEPLOYMENT_INDICATORS
        ):
            found.add("Agentforce for Healthcare")
            break

    for product, patterns in _PRODUCT_PATTERNS.items():
        if product == "Agentforce for Healthcare":
            continue  # handled above with stricter check
        combined = "|".join(f"(?:{p})" for p in patterns)
        for m in re.finditer(combined, body, re.IGNORECASE):
            mid = (m.start() + m.end()) // 2
            if any(abs(mid - p) <= _PROXIMITY_WINDOW for p in alias_positions):
                found.add(product)
                break
    return found


# ──────────────────────────────────────────────────────────────────────────
# Agent 8 — Classifier (Bedrock-backed CrewAI task; Layer 2)
# ──────────────────────────────────────────────────────────────────────────
def _classify_with_llm(
    payer: dict[str, str], evidence: list[Evidence]
) -> tuple[dict[str, list[Evidence]], str]:
    """Return (product_name -> list[Evidence], key_evidence_summary)."""
    payer_name: str = payer["payer_name"]
    aliases_raw: str = payer.get("search_aliases") or ""
    payer_aliases: set[str] = {payer_name} | {
        a.strip() for a in aliases_raw.split("|") if a.strip()
    }
    payer_aliases_lower: set[str] = {a.lower() for a in payer_aliases}
    excludes_lower: set[str] = build_excludes_set(payer)
    if not evidence:
        return {}, ""
    # Drop URL/body-gated items before the LLM sees them. Keeps prompt focused
    # and prevents the LLM from being tempted to map rejected content.
    filtered_evidence: list[Evidence] = [
        e for e in evidence
        if not _should_drop_evidence(e, payer_aliases_lower, excludes_lower)
    ]
    dropped = len(evidence) - len(filtered_evidence)
    if dropped:
        log.info("Pre-classifier gate dropped %d evidence item(s) for %s", dropped, payer_name)
    if not filtered_evidence:
        return {}, ""
    products_list = "\n".join(f"- {p.value}" for p in SalesforceProduct)
    evidence_blob = json.dumps(
        [
            {
                "i": i,
                "source_type": e.source_type,
                "url": e.url,
                "text": (e.full_text or e.snippet)[:3000],
                "date": e.date,
                "fingerprint_product": e.matched_product.value if e.matched_product else None,
                "regex_products": sorted(
                    _extract_products_from_body(e, payer_aliases, excludes_lower)
                ),
            }
            for i, e in enumerate(filtered_evidence)
        ],
        ensure_ascii=False,
    )
    description = f"""
You are mapping evidence about the US health plan **{payer_name}** to specific Salesforce products,
and writing a short narrative summary suitable for a business-development analyst.

ALLOWED PRODUCTS (use these exact strings as JSON keys):
{products_list}

Rules:
- Only assign an evidence item to a product if the text (or its fingerprint_product) explicitly names that product
  or uses a clearly equivalent term.
- 'Service Cloud' requires the source to explicitly name "Service Cloud", "contact center",
  "case management", or "omni-channel service". A generic Salesforce case study that describes
  member journeys, marketing, or data capabilities maps to Marketing Cloud or Data Cloud —
  NEVER Service Cloud.
- Whitepapers/brochures from Accenture, Deloitte, IBM, Cognizant, or Slalom are only valid evidence
  when the payer is literally named in the body. If the payer name is absent, skip the item.
- A named employee with a product-specific job title in their LinkedIn profile is Tier-1 evidence.
  Titles like "Salesforce Marketing Cloud Specialist", "Health Cloud Administrator",
  "Agentforce Developer", or "Vlocity Manager" at the target payer count as direct deployment
  signals for the named product. CIO/VP-level executive interviews that name a Salesforce product
  are also Tier-1.
- A named employee LinkedIn profile (linkedin.com/in/, /pulse/, /posts/) that contains a direct
  implementation statement for a specific Salesforce product at the target payer — phrases like
  "implemented", "managed the implementation of", "spearheaded the implementation of",
  "deployed", "administered", "lead developer for", "architected" — is Tier-2 evidence and MUST
  be included in `mappings` for that product. Do not skip such items because they are LinkedIn-
  only; the deterministic QC layer will combine multiple LinkedIn employees or pair them with
  technographic / job-posting signals to set the final verdict.
- HOWEVER, a LinkedIn profile that describes a FORMER employee (e.g. "worked at X from 2013 to 2016",
  "previously at X", "ex-X", or any employment with an explicit end date in the past and no
  current role at the payer) is NOT current evidence. Do NOT include former-employee profiles in
  `mappings`. Mention them in the narrative as historical context if relevant, but they do not
  support a current-deployment verdict.
- Map specific technical terms / legacy product names to their parent clouds:
    "Pardot"                                          ⇒ 'Marketing Cloud Account Engagement (Pardot)'
    "SFMC", "ExactTarget", "Email Studio", "et.com", "Marketing Platform"   ⇒ 'Marketing Cloud'
    "Community Cloud", "Digital Experience", "my.site.com", "force.com/s/" ⇒ 'Experience Cloud'
    "Service Console", "Field Service Lightning", "FSL", "Omni-Channel" ⇒ 'Service Cloud'
    "CRM Analytics", "Tableau CRM", "Einstein Analytics" ⇒ 'Data Cloud'
      (use 'Sales Cloud' ONLY when the text explicitly mentions sales pipeline, opportunities, or leads;
       for healthcare/payer contexts — member analytics, claims data, population health — always map to 'Data Cloud')
    "CPQ", "SteelBrick", "Revenue Cloud"               ⇒ 'Revenue Cloud (CPQ)'
    "Vlocity Health", "Vlocity Insurance", "OmniStudio", "Salesforce Industries",
      "Health Cloud Industry Edition", "Health Cloud"   ⇒ 'Health Cloud'
    "Agentforce", "Einstein Copilot for Health"        ⇒ 'Agentforce for Healthcare'
    "CareIQ", "Care IQ"                                ⇒ 'Sales Cloud' (Cigna's CareIQ platform per published case study)
    "Care Connect"                                     ⇒ 'Health Cloud' (Blue Shield of California's Care Connect per Sep 2023 press release)
    "prior authorization", "prior auth"                ⇒ 'Health Cloud' (in payer context, prior-auth automation is built on Health Cloud)
- A generic 'Salesforce' mention with no product hint does NOT map to anything — skip it.
- One evidence item MAY map to multiple products if it clearly names multiple.
- The `key_evidence_summary` is a 2-3 sentence plain-English narrative for a BD analyst:
  what Salesforce products the payer appears to use, what the strongest evidence is (cite source
  type and recency, e.g. "a January 2025 Health Cloud admin job posting"), and any caveats.
  If there is no credible evidence, say so plainly. Do NOT invent details that are not in the evidence.
- REGEX PRE-EXTRACTION: Each evidence item includes a `regex_products` list. These products
  were identified deterministically by Python regex in the full fetched page body — the product
  name is literally written on the page. You MUST include every product in `regex_products` in
  your `mappings` output for that evidence item. You may add additional products if the text
  clearly supports them, but you may NOT omit any product that appears in `regex_products`.
- CONSISTENCY RULE: If you mention a Salesforce product by name in the `key_evidence_summary`, you MUST
  include it in the `mappings` dict with at least one supporting evidence index. The narrative and the
  mappings must agree. Do not write about a product in the summary that has no entry in `mappings`,
  and do not omit from `mappings` any product you reference in the summary.
- Output STRICT JSON only — no prose outside the JSON, no markdown fences. Schema:
  {{"mappings": {{"<Product Name>": [<evidence index>, ...], ...}},
    "key_evidence_summary": "<2-3 sentence narrative>"}}
- Omit products with zero supporting evidence.

EVIDENCE (JSON array):
{evidence_blob}
""".strip()

    task = Task(
        description=description,
        expected_output=(
            'Strict JSON: {"mappings": {"<Product>": [<idx>, ...]}, '
            '"key_evidence_summary": "<2-3 sentences>"}'
        ),
        agent=classifier_agent(),
    )
    crew = Crew(
        agents=[task.agent],
        tasks=[task],
        process=Process.sequential,
        verbose=False,
    )
    result = crew.kickoff()
    text = str(result).strip()
    # Strip accidental code fences
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.MULTILINE)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        log.warning("Classifier returned non-JSON; raw=%r", text[:300])
        return {}, ""
    mappings: dict[str, list[int]] = data.get("mappings", {})
    summary: str = (data.get("key_evidence_summary") or "").strip()
    valid_products = {p.value for p in SalesforceProduct}
    out: dict[str, list[Evidence]] = {}
    for product, idxs in mappings.items():
        if product not in valid_products:
            continue
        out[product] = [filtered_evidence[i] for i in idxs if 0 <= i < len(filtered_evidence)]

    # ── Post-processing safety net (Layer 1 enforcement) ────────────────────
    # If the LLM missed a product that regex found explicitly in the page
    # body, add it here in Python. Deterministic, cannot be overridden by
    # LLM instruction-following failures.
    for i, ev in enumerate(filtered_evidence):
        regex_hits = _extract_products_from_body(ev, payer_aliases, excludes_lower)
        for product in regex_hits:
            if product not in valid_products:
                continue
            if product not in out:
                out[product] = [ev]
                log.info(
                    "Post-processing: added %s via regex from evidence[%d] url=%s",
                    product, i, ev.url,
                )
            else:
                existing_ids = {id(e) for e in out[product]}
                if id(ev) not in existing_ids:
                    out[product].append(ev)

    # ── Narrative override ──────────────────────────────────────────────────
    # If the LLM's own summary explicitly says there is no real evidence,
    # clear any products the LLM or post-processing added. The narrative is
    # the authoritative signal for these edge cases; mismatches show up as
    # Yes verdicts paired with "no credible evidence" summaries.
    summary_lower = summary.lower()
    if any(phrase in summary_lower for phrase in _NO_EVIDENCE_PHRASES):
        if out:
            log.info(
                "Narrative override: clearing %d product(s) for %s — summary indicates no evidence",
                len(out), payer_name,
            )
            out.clear()
            summary = (
                summary
                + " [All verdicts cleared by narrative override — no credible evidence detected.]"
            )
    return out, summary


# ─────────────────────────────────────────────────────────────────────────────
# Agents 9 + 10 — Recency & QC (deterministic per §5)
# ─────────────────────────────────────────────────────────────────────────────
def assemble_record(
    payer: dict[str, str], product_evidence: dict[str, list[Evidence]], all_evidence: list[Evidence]
) -> PayerRecord:
    rec = PayerRecord(
        payer_name=payer["payer_name"],
        payer_type=payer.get("payer_type", ""),
        domain=payer.get("domain", ""),
    )

    confidences: list[ConfidenceScore] = []
    for product in SalesforceProduct:
        evs = product_evidence.get(product.value, [])
        result = qc_score(product, evs)
        rec.verdicts[product.value] = result.verdict.value
        if result.verdict != UsageVerdict.UNKNOWN:
            confidences.append(result.confidence)

    # Source URLs: prioritize evidence that drove Yes/Likely verdicts, then
    # append any other evidence so payers with only Unknown verdicts still
    # surface job-posting / news / community URLs for BD verification.
    urls: list[str] = []
    for product, evs in product_evidence.items():
        if rec.verdicts.get(product) in {"Yes", "Likely"}:
            urls.extend(e.url for e in evs if e.url)
    for e in all_evidence:
        if e.url and e.url not in urls:
            urls.append(e.url)
    rec.source_urls = list(dict.fromkeys(urls))[:5]

    # Most recent evidence date
    rec.date_identified = _most_recent_date(all_evidence) or ""

    # Overall confidence = max(High > Medium > Low) across positive verdicts
    order = {ConfidenceScore.HIGH: 3, ConfidenceScore.MEDIUM: 2, ConfidenceScore.LOW: 1}
    if confidences:
        rec.confidence = max(confidences, key=lambda c: order[c])
    else:
        rec.confidence = ConfidenceScore.LOW

    # Low-confidence payers: drop generic marketing/tutorial pages and the
    # payer's own-domain pages so BD doesn't mistake them for evidence.
    if rec.confidence == ConfidenceScore.LOW:
        payer_domain = (payer.get("domain") or "").lower().strip()
        filtered: list[str] = []
        for u in rec.source_urls:
            ul = u.lower()
            if any(p in ul for p in _NON_EVIDENCE_URL_PATTERNS):
                continue
            if payer_domain and payer_domain in ul:
                continue
            filtered.append(u)
        rec.source_urls = filtered or ["No Salesforce-specific evidence found"]

    if rec.confidence == ConfidenceScore.HIGH:
        rec.bd_notes = "Confirmed deployment \u2014 reference in BD outreach."
    elif rec.confidence == ConfidenceScore.MEDIUM:
        rec.bd_notes = "Likely deployment \u2014 validate via direct outreach before referencing."
    else:
        rec.bd_notes = "No confirmed deployment \u2014 potential greenfield opportunity."

    return rec


_DATE_FORMATS = ["%Y-%m-%d", "%Y/%m/%d", "%b %d, %Y", "%B %d, %Y", "%Y-%m"]


def _parse(d: str) -> datetime | None:
    if not d:
        return None
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(d, fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(d.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def _most_recent_date(evs: Iterable[Evidence]) -> str:
    dts = [(_parse(e.date or ""), e.date or "") for e in evs]
    dts = [(d, s) for d, s in dts if d is not None]
    if not dts:
        return ""
    return max(dts, key=lambda t: t[0])[1]


# ─────────────────────────────────────────────────────────────────────────────
# Top-level entry (Agents 1 + 11)
# ─────────────────────────────────────────────────────────────────────────────
def run(seed_path: Path, out_dir: Path) -> Path:
    payers = load_seed(seed_path)
    client = SearchApiClient()

    records: list[PayerRecord] = []
    for p in payers:
        log.info("Processing payer: %s", p["payer_name"])
        evidence = gather_evidence(p, client)
        if evidence:
            product_map, key_evidence_summary = _classify_with_llm(p, evidence)
        else:
            product_map, key_evidence_summary = {}, ""
        rec = assemble_record(p, product_map, evidence)
        rec.key_evidence = key_evidence_summary
        records.append(rec)

    return write_excel(records, out_dir)


__all__ = [
    "EXCEL_COLUMNS",
    "PRODUCT_COLUMNS",
    "EXECUTIVE_EXCEL_COLUMNS",
    "assemble_record",
    "assemble_executive_record",
    "gather_evidence",
    "gather_executive_evidence",
    "load_seed",
    "run",
    "run_executive",
]


# ═════════════════════════════════════════════════════════════════════════════
# Executive Intelligence pipeline (--mode executive)
# Mirrors the product pipeline above but extracts the 5 BD executive personas
# (CEO, CIO/CTO, CMO/Growth, Chief Medical, VP Member Experience) per the
# AArete_Executive_Engine_Instructions spec.
# ═════════════════════════════════════════════════════════════════════════════

# Leadership-page path patterns appended to the payer's own domain.
# v3.8: Leadership slug regex for anchor-discovery (see _discover_leadership_url).
# Matches href/text values that point to a real leadership/team page.
_LEADERSHIP_SLUG_RE = re.compile(
    r"(leadership|executive[\-_]?team|our[\-_]?team|about[\-_]?us"
    r"|our[\-_]?leaders|leadership[\-_]?team|meet[\-_]?the[\-_]?team)",
    re.I,
)

# Fallback hardcoded paths used only when homepage anchor-discovery fails.
_LEADERSHIP_PATHS = (
    "leadership",
    "about/leadership",
    "our-team",
    "executive-team",
    "about-us/leadership",
    "about/our-leaders",
    "leadership-team",
)


def _discover_leadership_url(domain: str) -> str | None:
    """Fetch the payer homepage once and follow the first anchor whose href
    or visible text matches a leadership-page pattern.  Returns the resolved
    absolute URL, or None if nothing is found or the fetch fails.

    Preferred over _LEADERSHIP_PATHS because real slugs vary widely (86% 404
    rate on hardcoded paths in the 15-payer probe).
    """
    from .tools.fetcher import fetch

    home = f"https://{domain}/"
    resp = fetch(home, timeout=10.0)
    if resp is None:
        return None
    try:
        soup = BeautifulSoup(resp.text, "html.parser")
    except Exception:  # noqa: BLE001
        return None

    for tag in soup.find_all("a", href=True):
        href: str = tag["href"].strip()
        text: str = tag.get_text(" ", strip=True)
        if _LEADERSHIP_SLUG_RE.search(href) or _LEADERSHIP_SLUG_RE.search(text):
            if href.startswith("http"):
                return href
            if href.startswith("/"):
                return f"https://{domain}{href}"
            return f"https://{domain}/{href}"
    return None

# Press-release title patterns that strongly indicate an executive appointment.
_APPOINTMENT_TERMS = (
    '"appointed" OR "named" OR "joins as" OR "promoted to" '
    'OR "elected" OR "announces" OR "new Chief" '
    'OR "retire" OR "retirement" OR "successor" OR "departure" OR "steps down" '
    'OR "passed away" OR "deceased" OR "in memoriam" OR "obituary"'
)


def _exec_titles_for(role: ExecutiveRole) -> str:
    """Build a Google-search 'OR' clause for all titles of one persona."""
    titles = EXECUTIVE_TITLE_MAP[role]
    return "(" + " OR ".join(f'"{t}"' for t in titles) + ")"


def gather_executive_evidence(
    payer: dict[str, str], client: SearchApiClient
) -> list[Evidence]:
    """Gather executive evidence for one payer. ~9 SearchApi calls / payer.

    - 5 LinkedIn-snippet searches (one per persona)
    - 1 payer-domain leadership-page search
    - 2 news searches (appointment press releases + general leadership news)
    - 1 third-party directory cross-reference (top execs only)
    """
    name = payer["payer_name"]
    domain = (payer.get("domain") or "").strip().lower()
    name_clause = build_name_clause(name, payer.get("search_aliases"))
    evidence: list[Evidence] = []

    # ── Per-persona LinkedIn snippets (5 calls) ────────────────────────────
    for role in ExecutiveRole:
        titles_clause = _exec_titles_for(role)
        query = (
            f"(site:linkedin.com/in/ OR site:linkedin.com/pub/) "
            f"{name_clause} (Medicaid OR \"Government Programs\" OR \"Community & State\" OR \"State Programs\") "
            f"{titles_clause}"
        )
        for r in _safe_search(client.google, query, num=10):
            evidence.append(
                Evidence(
                    source_type="linkedin_profile",
                    url=r.get("link", "") or "",
                    snippet=(r.get("snippet") or "")[:1500],
                    date=r.get("date"),
                )
            )

    # ── Payer-domain leadership page (1 call) ──────────────────────────────
    if domain:
        leadership_query = (
            f"site:{domain} (leadership OR \"executive team\" OR \"our leaders\" "
            f"OR \"our team\" OR \"leadership team\")"
        )
        for r in _safe_search(client.google, leadership_query, num=10):
            evidence.append(
                Evidence(
                    source_type="leadership_page",
                    url=r.get("link", "") or "",
                    snippet=(r.get("snippet") or "")[:1500],
                    date=r.get("date"),
                )
            )

        # v3.8: anchor-discovery — fetch homepage once, follow the real
        # leadership slug link. Falls back to hardcoded _LEADERSHIP_PATHS
        # only if homepage fetch fails (SPA shells, connection refused, etc.).
        discovered_url = _discover_leadership_url(domain)
        if discovered_url:
            evidence.append(
                Evidence(
                    source_type="leadership_page",
                    url=discovered_url,
                    snippet="",
                    date=None,
                )
            )
        else:
            # Fallback: inject hardcoded paths; 404s are silently dropped by
            # _enrich_executive_pages when fetch returns None.
            for path in _LEADERSHIP_PATHS:
                evidence.append(
                    Evidence(
                        source_type="leadership_page",
                        url=f"https://{domain}/{path}",
                        snippet="",
                        date=None,
                    )
                )

    # ── Executive-appointment news (1 call) ────────────────────────────────
    appointment_query = (
        f"{name_clause} (Medicaid OR \"Government Programs\" OR \"Community & State\") {_APPOINTMENT_TERMS} "
        f"(\"Chief Executive\" OR \"Chief Information\" OR \"Chief Technology\" "
        f"OR \"Chief Medical\" OR \"Chief Marketing\" OR \"Chief Growth\" "
        f"OR \"Chief Experience\" OR \"President\" OR \"Vice President\" OR \"SVP\")"
    )
    for r in _safe_search(
        client.google_news, appointment_query, time_range="qdr:2y", num=15
    ):
        evidence.append(
            Evidence(
                source_type="executive_news",
                url=r.get("link", "") or "",
                snippet=(r.get("snippet") or "")[:1500],
                date=r.get("date"),
            )
        )

    # ── Wire-service appointment press releases (1 call) ───────────────────
    wire_query = (
        f"(site:businesswire.com OR site:prnewswire.com OR site:globenewswire.com) "
        f"{name_clause} {_APPOINTMENT_TERMS}"
    )
    for r in _safe_search(client.google, wire_query, num=10):
        evidence.append(
            Evidence(
                source_type="executive_news",
                url=r.get("link", "") or "",
                snippet=(r.get("snippet") or "")[:1500],
                date=r.get("date"),
            )
        )

    # ── Third-party directories (1 call) ───────────────────────────────────
    third_party_query = (
        f"(site:rocketreach.co OR site:zoominfo.com OR site:beckershospitalreview.com) "
        f"{name_clause} (\"Chief\" OR \"President\" OR \"VP\")"
    )
    for r in _safe_search(client.google, third_party_query, num=10):
        evidence.append(
            Evidence(
                source_type="third_party_directory",
                url=r.get("link", "") or "",
                snippet=(r.get("snippet") or "")[:1500],
                date=r.get("date"),
            )
        )

    # ── Persona-scoped deceased check (1 call) ─────────────────────────────
    # Prevents high-profile payer news (e.g. Brian Thompson assassination)
    # from crowding out smaller obituaries that the deceased guard needs.
    deceased_query = (
        f"{name_clause} "
        f'("passed away" OR "has died" OR "obituary" OR "in memoriam")'
    )
    for r in _safe_search(
        client.google_news, deceased_query, time_range="qdr:2y", num=10
    ):
        evidence.append(
            Evidence(
                source_type="executive_news",
                url=r.get("link", "") or "",
                snippet=(r.get("snippet") or "")[:1500],
                date=r.get("date"),
            )
        )

    # ── Career-history evidence (1 call) ───────────────────────────────────
    # Lifts past-jobs fill rate by surfacing the "previously / prior to
    # joining / formerly" phrases the prompt instructs the LLM to look for.
    career_query = (
        f"{name_clause} "
        f'("previously" OR "prior to joining" OR "before joining" '
        f'OR "formerly" OR "joined from" OR "career includes")'
    )
    for r in _safe_search(client.google, career_query, num=10):
        evidence.append(
            Evidence(
                source_type="executive_news",
                url=r.get("link", "") or "",
                snippet=(r.get("snippet") or "")[:1500],
                date=r.get("date"),
            )
        )

    # Dedupe by (source_type, url)
    seen: set[tuple[str, str]] = set()
    out: list[Evidence] = []
    for e in evidence:
        if not e.url:
            continue
        key = (e.source_type, e.url)
        if key in seen:
            continue
        seen.add(key)
        out.append(e)

    # Enrich leadership pages + news bodies via the same allow-list fetcher.
    out = _enrich_executive_pages(out, domain)
    return out


def _enrich_executive_pages(evidence: list[Evidence], payer_domain: str) -> list[Evidence]:
    """Fetch page bodies for leadership pages and executive news.

    Reuses the same allow-list as `_enrich_with_page_bodies` plus the payer's
    own domain (so its /leadership page can be parsed).
    """
    from .tools.fetcher import fetch

    payer_domain = (payer_domain or "").strip().lower()

    for ev in evidence:
        if not ev.url:
            continue
        host = (urlparse(ev.url).hostname or "").lower()
        allow = any(host == d or host.endswith("." + d) for d in _FETCH_DOMAINS)
        # Always allow fetching the payer's own /leadership-style pages.
        if payer_domain and (host == payer_domain or host.endswith("." + payer_domain)):
            allow = True
        if not allow:
            continue
        resp = fetch(ev.url, timeout=15.0)
        if resp is None:
            continue
        try:
            soup = BeautifulSoup(resp.text, "html.parser")
            for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
                tag.decompose()
            text = soup.get_text(separator=" ", strip=True)
            cleaned = _WS_RE.sub(" ", text).strip()
            # Bump cap for leadership pages — they list many execs in one body.
            cap = 12000 if ev.source_type == "leadership_page" else _MAX_BODY_CHARS
            ev.full_text = cleaned[:cap]
        except Exception:  # noqa: BLE001 — best-effort enrichment
            continue
    return evidence


# Title → ExecutiveRole resolver (regex Layer 1). Order matters: most-specific
# patterns first. "Chief Medical" must beat "Chief"; "CMO" is ambiguous and
# left to the LLM unless context is clear.
_TITLE_TO_ROLE_PATTERNS: list[tuple[re.Pattern[str], ExecutiveRole]] = [
    (re.compile(r"\bChief\s+Medical\s+Officer\b", re.I), ExecutiveRole.CHIEF_MEDICAL),
    (re.compile(r"\bChief\s+Clinical\s+Officer\b", re.I), ExecutiveRole.CHIEF_MEDICAL),
    (re.compile(r"\bChief\s+Population\s+Health\s+Officer\b", re.I), ExecutiveRole.CHIEF_MEDICAL),
    (re.compile(r"\bChief\s+Health\s+Officer\b", re.I), ExecutiveRole.CHIEF_MEDICAL),
    (re.compile(r"\bChief\s+Information\s+Officer\b", re.I), ExecutiveRole.CIO),
    (re.compile(r"\bChief\s+Technology\s+Officer\b", re.I), ExecutiveRole.CIO),
    (re.compile(r"\bChief\s+Digital\s+(?:and\s+Information\s+)?Officer\b", re.I), ExecutiveRole.CIO),
    (re.compile(r"\bChief\s+Information\s+and\s+Digital\s+Officer\b", re.I), ExecutiveRole.CIO),
    (re.compile(r"\bCIO\b"), ExecutiveRole.CIO),
    (re.compile(r"\bCTO\b"), ExecutiveRole.CIO),
    (re.compile(r"\bCDIO\b"), ExecutiveRole.CIO),   # v3.8: Chief Digital and Information Officer abbrev
    (re.compile(r"\bCIDO\b"), ExecutiveRole.CIO),   # v3.8: Chief Information and Digital Officer abbrev
    (re.compile(r"\bChief\s+Marketing\s+Officer\b", re.I), ExecutiveRole.CMO),
    (re.compile(r"\bChief\s+Brand\s+Officer\b", re.I), ExecutiveRole.CMO),
    (re.compile(r"\bChief\s+Experience\s+Officer\b", re.I), ExecutiveRole.VP_EXPERIENCE),
    (re.compile(r"\bChief\s+Patient\s+Engagement\s+Officer\b", re.I), ExecutiveRole.VP_EXPERIENCE),
    (re.compile(r"\bChief\s+Member\s+Experience\s+Officer\b", re.I), ExecutiveRole.VP_EXPERIENCE),
    (re.compile(r"\bChief\s+Customer\s+Experience\s+Officer\b", re.I), ExecutiveRole.VP_EXPERIENCE),
    (re.compile(r"\bVP\s+(?:of\s+)?Member\s+Experience\b", re.I), ExecutiveRole.VP_EXPERIENCE),
    (re.compile(r"\bVP\s+(?:of\s+)?Customer\s+Experience\b", re.I), ExecutiveRole.VP_EXPERIENCE),
    (re.compile(r"\bVP\s+(?:of\s+)?Consumer\s+Experience\b", re.I), ExecutiveRole.VP_EXPERIENCE),
    (re.compile(r"\bChief\s+Consumer\s+Officer\b", re.I), ExecutiveRole.VP_EXPERIENCE),
    (re.compile(r"\bSVP\s+(?:Member|Consumer)\s+Experience\b", re.I), ExecutiveRole.VP_EXPERIENCE),
    (re.compile(r"\bSVP\s+Experience\b", re.I), ExecutiveRole.VP_EXPERIENCE),              # v3.8
    (re.compile(r"\bVP\s+Digital\s+Engagement\b", re.I), ExecutiveRole.VP_EXPERIENCE),     # v3.8
    (re.compile(r"\bVP\s+Member\s+Services\b", re.I), ExecutiveRole.VP_EXPERIENCE),
    (re.compile(r"\bChief\s+Executive\s+Officer\b", re.I), ExecutiveRole.CEO),
    (re.compile(r"\b(?:Market|Plan)\s+President\b", re.I), ExecutiveRole.CEO),
    (re.compile(r"\bPresident\s+(?:&|and)\s+CEO\b", re.I), ExecutiveRole.CEO),
    (re.compile(r"\bCEO\b"), ExecutiveRole.CEO),
    (re.compile(r"\bPresident\b", re.I), ExecutiveRole.CEO),
]

# "<Title> <Name>" or "<Name>, <Title>" patterns over leadership-page bodies.
_NAME_TOKEN = r"[A-Z][a-zA-Z'\-]+(?:\s+[A-Z]\.?)?\s+[A-Z][a-zA-Z'\-]+"
_TITLE_NAME_RE = re.compile(
    r"(?P<title>(?:Chief\s+[A-Za-z]+(?:\s+[A-Za-z]+){0,3}\s+Officer|President\s+(?:&|and)\s+CEO|Market\s+President|Plan\s+President|President|CEO|CIO|CTO|CMO|CXO))"
    r"[,:\s\-\u2014]{0,6}"
    r"(?P<name>" + _NAME_TOKEN + r")",
)
_NAME_TITLE_RE = re.compile(
    r"(?P<name>" + _NAME_TOKEN + r")"
    r"[,:\s\-\u2014]{1,6}"
    r"(?P<title>(?:Chief\s+[A-Za-z]+(?:\s+[A-Za-z]+){0,3}\s+Officer|President\s+(?:&|and)\s+CEO|Market\s+President|Plan\s+President|President|CEO|CIO|CTO|CMO|CXO))",
)
_PAST_FIRM_RE = re.compile(
    r"(?:previously|prior to|formerly|before joining|came from|earlier in (?:his|her|their) career)"
    r"[^.\n]{0,120}?(?:at|with|of)\s+(?P<firm>[A-Z][A-Za-z0-9&\.\-]+(?:\s+[A-Z][A-Za-z0-9&\.\-]+){0,4})",
    re.I,
)


def _title_to_role(title: str) -> ExecutiveRole | None:
    for pat, role in _TITLE_TO_ROLE_PATTERNS:
        if pat.search(title):
            return role
    return None


# v3.4: Python-level title rejection per persona. The LLM prompt already
# carries STRICT REJECT lists for CMO and VP Experience, but the LLM
# occasionally ignores them. This filter is the final gate.
_PERSONA_TITLE_REJECT: dict[ExecutiveRole, frozenset[str]] = {
    ExecutiveRole.CMO: frozenset({
        "growth officer", "revenue officer", "commercial officer",
        "strategy officer", "operating officer", "operations officer",
        "financial officer", "legal officer", "compliance officer",
    }),
    ExecutiveRole.VP_EXPERIENCE: frozenset({
        "quality", "population health", "clinical", "finance",
        "strategy", "growth", "revenue", "legal", "compliance",
        "operations", "supply chain",
    }),
}

# v3.5: universal reject list applied to ALL non-CEO personas. Stops a
# President / CEO / COO / CFO / GC from being placed in a CIO/CMO/CM/VPX
# slot just because the LLM found a prior CMO title on their bio.
# Compound "president" forms only — bare "president" would substring-match
# "Vice President" and break legitimate VP titles.
_PERSONA_TITLE_REJECT_UNIVERSAL: frozenset[str] = frozenset({
    "president and ceo", "president & ceo", "president-elect",
    "chief executive officer",
    "chief operating officer",
    "chief financial officer",
    "general counsel", "chief legal officer",
})


def _title_passes_persona_filter(role: ExecutiveRole, title: str | None) -> bool:
    """Return False if the title contains a hard-reject keyword for this persona.

    Stops Chief Growth Officer / Quality VP / etc. from sneaking into the
    CMO or VP Experience slots when the LLM ignores the prompt REJECT list.
    v3.5: also applies a universal reject for top-of-org titles when the
    persona is anything other than CEO (Housley "President" → CMO case).
    Personas without an entry in `_PERSONA_TITLE_REJECT` (CIO, Chief
    Medical) only run the universal gate.
    """
    title_lc = (title or "").lower()
    if role != ExecutiveRole.CEO and any(
        term in title_lc for term in _PERSONA_TITLE_REJECT_UNIVERSAL
    ):
        return False
    reject_terms = _PERSONA_TITLE_REJECT.get(role)
    if not reject_terms:
        return True
    return not any(term in title_lc for term in reject_terms)


# v3.5: LinkedIn URL format gate. Catches non-LinkedIn URLs (e.g. YouTube)
# that survive the existing evidence-presence check because the same URL
# happens to appear elsewhere in the evidence blob.
_LINKEDIN_URL_RE = re.compile(
    r"^https?://(?:www\.)?linkedin\.com/(?:in|pub)/[A-Za-z0-9_\-%./]+/?$",
    re.IGNORECASE,
)


def _validate_linkedin_url(url: str | None) -> str | None:
    """Return the URL if it looks like a real LinkedIn profile URL, else None."""
    if not url:
        return None
    url_clean = url.strip()
    if _LINKEDIN_URL_RE.match(url_clean):
        return url_clean
    log.info("Rejecting invalid LinkedIn URL: %s", url_clean)
    return None


# v3.5: education-firm filter for past_jobs. Universities / colleges /
# schools etc. are not professional employment positions.
_EDUCATION_TERMS: frozenset[str] = frozenset({
    "university", "college", "school", "institute", "academy",
    "polytechnic", "seminary", "conservatory", "education",
})


def _is_education_firm(firm: str | None) -> bool:
    if not firm:
        return False
    firm_lc = firm.lower()
    return any(term in firm_lc for term in _EDUCATION_TERMS)


def _extract_executives_deterministic(
    evidence: list[Evidence], payer_aliases_lower: set[str]
) -> dict[ExecutiveRole, list[dict]]:
    """Regex Layer 1: pull (role, name, title) candidates from fetched bodies.

    Returns role -> list of {name, title, source_url, evidence_index, past_firms}.
    Names that match the payer's own aliases are excluded (avoids
    self-references like a payer named for a person).
    """
    candidates: dict[ExecutiveRole, list[dict]] = {r: [] for r in ExecutiveRole}
    for i, ev in enumerate(evidence):
        body = ev.full_text or ev.snippet or ""
        if not body:
            continue
        # Collect past-firm hits from this evidence body (associated to nearest
        # candidate at the same evidence index).
        past_firms_in_body = [
            m.group("firm").strip()
            for m in _PAST_FIRM_RE.finditer(body)
        ]
        for pattern in (_TITLE_NAME_RE, _NAME_TITLE_RE):
            for m in pattern.finditer(body):
                name = m.group("name").strip()
                title = m.group("title").strip()
                if not name or not title:
                    continue
                if name.lower() in payer_aliases_lower:
                    continue
                role = _title_to_role(title)
                if role is None:
                    continue
                candidates[role].append(
                    {
                        "name": name,
                        "title": title,
                        "source_url": ev.url,
                        "evidence_index": i,
                        "past_firms": past_firms_in_body[:2],
                    }
                )
    return candidates


def _is_enterprise_ceo_of_national_payer(payer_name: str, payer_type: str, title: str) -> bool:
    """Reject enterprise CEOs for National/Blues payers unless the title specifies Medicaid."""
    if payer_type.lower() == "medicaid mco":
        return False  # Pure-play Medicaid MCOs can use their enterprise CEO
    title_lower = (title or "").strip().lower()

    # If the title explicitly mentions Medicaid or Government Programs, allow it
    if any(k in title_lower for k in ["medicaid", "government", "state programs", "community & state"]):
        return False

    # Block generic enterprise titles
    enterprise_titles = [
        "president and ceo", "president & ceo", "chief executive officer", "ceo",
        "president", "group president", "executive vice president", "evp"
    ]

    # If it's a generic enterprise title without Medicaid qualifiers, block it
    if any(t == title_lower for t in enterprise_titles):
        return True

    # Also block if it contains enterprise indicators but no Medicaid qualifiers
    if any(k in title_lower for k in ["enterprise", "global", "national", "group"]) and not any(
        k in title_lower for k in ["medicaid", "government"]
    ):
        return True

    return False


# ─────────────────────────────────────────────────────────────────────────────
# Executive classifier (LLM) — resolves name collisions, picks current holder
# ─────────────────────────────────────────────────────────────────────────────
def _classify_executives_with_llm(
    payer: dict[str, str], evidence: list[Evidence]
) -> tuple[dict[ExecutiveRole, dict], str, str]:
    """Return (role -> {name, title, linkedin_url, past_firms, evidence_indices}, bd_notes, summary)."""
    payer_name = payer["payer_name"]
    aliases_raw = payer.get("search_aliases") or ""
    payer_aliases = {payer_name} | {
        a.strip() for a in aliases_raw.split("|") if a.strip()
    }
    payer_aliases_lower = {a.lower() for a in payer_aliases}

    if not evidence:
        return {}, "", ""

    regex_candidates = _extract_executives_deterministic(evidence, payer_aliases_lower)

    # Build the evidence blob the LLM sees. Truncate bodies to keep prompt size sane.
    evidence_blob = json.dumps(
        [
            {
                "i": i,
                "source_type": e.source_type,
                "url": e.url,
                "text": (e.full_text or e.snippet or "")[:2500],
                "date": e.date,
            }
            for i, e in enumerate(evidence)
        ],
        ensure_ascii=False,
    )

    regex_blob = json.dumps(
        {
            role.value: cands[:8]  # cap to keep prompt focused
            for role, cands in regex_candidates.items()
            if cands
        },
        ensure_ascii=False,
    )

    roles_list = "\n".join(
        f"- {role.value}: titles include {', '.join(EXECUTIVE_TITLE_MAP[role][:5])}"
        for role in ExecutiveRole
    )

    description = f"""
You are identifying the CURRENT holders of 5 executive personas SPECIFICALLY FOR
THE MEDICAID OR GOVERNMENT PROGRAMS DIVISION at the US health plan **{payer_name}**
for a business-development outreach list.

TARGET PERSONAS (use these exact strings as JSON keys):
{roles_list}

Rules:
- CRITICAL PAYER-MATCH RULE: You MUST verify that the executive CURRENTLY works at
  **{payer_name}** (or one of its known aliases). If the payer name does not appear
  in the CURRENT role section of the evidence — i.e. the LinkedIn snippet shows
  "Present" at a DIFFERENT organization, or the press release names a different
  health plan — you MUST omit that executive entirely. Do NOT assign an executive
  from Independence Blue Cross to Aetna, or from Humana to UnitedHealth, etc.
  When in doubt, omit rather than guess.
- MEDICAID DIVISION RULE (CRITICAL): You are building a list of MEDICAID and
    GOVERNMENT PROGRAMS leaders. You MUST prioritize executives whose titles
    explicitly mention "Medicaid", "Government Programs", "State Programs",
    "Community & State", or "Medicare & Retirement".
    * For the CEO slot: Do NOT use the enterprise CEO of a national payer
        (e.g., do not use the CEO of UnitedHealth Group, Humana Inc., or Elevance
        Health). You MUST find the President or CEO of the Medicaid / Government
        Programs division (e.g., "CEO of UnitedHealthcare Community & State",
        "President of Government Business", "SVP Medicaid").
    * For non-CEO slots (CIO, CMO, Chief Medical, VP Experience): You MUST
        prioritize the executive for the Medicaid/Government Programs division.
    * If the payer is a pure-play Medicaid MCO (e.g., CareSource, Molina,
        Centene), the enterprise executives ARE the Medicaid executives, so you
        may use them.
    * If you absolutely cannot find a Medicaid-specific leader for a slot, you
        may use the enterprise leader, but you MUST state "Enterprise-level
        executive" in the first sentence of the `bd_note`.
- NATIONAL OVER STATE RULE (CRITICAL): You MUST prioritize the NATIONAL leader
    of the Medicaid/Government Programs division. Do NOT select state-level or
    regional leaders (e.g., "CEO of UHC Maryland" or "Market President, Ohio").
    If you cannot find the National Medicaid leader, leave the slot EMPTY.
    Do NOT fall back to the Enterprise CEO (e.g., the CEO of UnitedHealth Group).
    You must find the leader of the specific Medicaid subsidiary (e.g., UnitedHealthcare Community & State).
- STRICT PERSONA MATCHING RULE: You MUST ensure the executive's title matches
    the persona slot.
    * Do NOT put an operations executive (e.g., "EVP Medicaid", "President of
        Government Programs") into a clinical slot (Chief Medical, CMO).
    * Clinical slots MUST be filled by physicians (MD/DO) or executives with
        "Medical", "Health", or "Clinical" in their title.
- For EACH persona, pick the single current executive at {payer_name} based on
  the evidence. If no qualifying evidence exists, OMIT that persona from the
  output (do not invent names).
- A LinkedIn snippet that contains "Present" or "YYYY - Present" for the role
  at {payer_name} is strong current-tenure evidence. Profiles whose only
  attestation is an explicit past-tense employment (e.g. "2018 - 2022") at
  {payer_name} are FORMER employees and MUST be excluded.
- A press release dated within the last 6 months announcing an appointment
  (e.g. "{payer_name} appoints Jane Doe as Chief Information Officer") is
  authoritative — prefer it over older LinkedIn snippets with a different name.
- DATE WEIGHTING — MANDATORY: Before assigning any executive, check the `date`
  field of every evidence item that mentions them. If ANY evidence item dated
  2024-01-01 or later contains the phrases "stepped down", "departed", "left",
  "retired", "no longer", "resigned", "successor named", or "passed away" for
  that person, you MUST treat them as no longer in the role and OMIT them from
  the output entirely — even if an older LinkedIn snippet or third-party
  directory still lists them as the active holder. A 2025 or 2026 departure
  announcement always overrides a 2022 or 2023 LinkedIn profile.
- RECENCY PREFERENCE: When two candidates both have evidence, prefer the one
  whose most recent evidence item has the later `date`. A candidate with a
  2026-dated press release beats one with only a 2023 LinkedIn snippet.
- If two candidates both claim the same role, pick the one with (a) the most
  recent corroborating press release, OR (b) the LinkedIn "Present" tenure.
- Disambiguate CMO carefully: "Chief Marketing Officer" → CMO persona;
  "Chief Medical Officer" → Chief Medical persona. Never put a marketing
  executive in Chief Medical or vice versa.
- CMO PERSONA — STRICT DEFINITION: the CMO slot is the executive responsible
  for marketing, brand, and member/customer acquisition strategy.
  * ACCEPT titles containing: "Chief Marketing Officer", "VP Marketing",
    "SVP Marketing", "Chief Brand Officer".
  * REJECT titles containing: "Chief Growth Officer", "Chief Revenue Officer",
    "Chief Commercial Officer", "Chief Strategy Officer", "Chief Operating
    Officer". A Growth or Revenue Officer is NOT a CMO.
  * If no true CMO exists, OMIT the CMO persona from the JSON.
- Chief Medical: The top clinical executive for the Medicaid division
    (e.g., "Chief Medical Officer, Medicaid", "VP of Clinical Operations, Government Programs").
    MUST be a physician (MD/DO) or have a strictly clinical title.
    Do NOT use operations, growth, or strategy executives.
- VP EXPERIENCE PERSONA — STRICT DEFINITION: the VP Experience slot is the
  executive responsible for member satisfaction, customer experience, NPS,
  or digital engagement.
  * ACCEPT titles containing: "Chief Experience Officer", "VP Customer
    Experience", "VP Member Experience", "VP Consumer Experience",
    "Chief Consumer Officer", "VP Member Services", "VP Digital Experience",
    "Chief Digital Officer", "VP Engagement", "SVP Member Experience".
  * REJECT titles containing: "Quality", "Population Health", "Clinical",
    "Operations", "Finance", "Strategy", "Growth", "Revenue". A Quality
    VP or Population Health VP is NOT a VP Experience.
  * If no true VP Experience exists, OMIT the persona from the JSON.
- VP Member Experience covers: Chief Experience Officer, Chief Patient
  Engagement Officer, Chief Member/Customer Experience Officer, VP Member
  Experience. Do not put a generic Chief Operating Officer here.
- STRICT PERSONA MATCHING: Do NOT shoehorn an executive into a persona slot
  if their title does not match.
  * Do NOT put a Chief Operations Officer (COO) into the CIO slot.
  * Do NOT put a President or Chief Revenue Officer into the CMO slot just
    because they used to be a CMO.
  * If you cannot find a true CIO, CMO, Chief Medical Officer, or VP
    Experience, leave that persona completely blank (OMIT it from the JSON).
- DECEASED CHECK: If ANY evidence indicates the executive has passed away,
  died, is deceased, or appears in an obituary / "in memoriam" notice, you
  MUST omit them entirely — even if older evidence still lists them as the
  active officeholder.
- SUCCESSORS: If a CEO has announced retirement and a successor is named
  (e.g. a new President), put the CURRENT active CEO in the CEO slot.
  Mention the successor in `bd_notes` and `departure_note`, but do NOT force
  the successor into a different persona slot (like CMO) just to get them on
  the board.
- PARENT VS SUBSIDIARY: If {payer_name} is a subsidiary or division (e.g.
  "UnitedHealthcare" is the insurance subsidiary of UnitedHealth Group, and
  "Aetna" is the insurance subsidiary of CVS Health), and the evidence
  mentions BOTH the subsidiary executive (e.g. Tim Noel, CEO of
  UnitedHealthcare) AND the parent holding-company executive (e.g. Andrew
  Witty, former CEO of UnitedHealth Group), you MUST select the executive of
  the specific subsidiary/division named in {payer_name}, NOT the parent
  holding company. When in doubt, prefer the executive whose title or press
  release explicitly names the subsidiary.
- For each chosen executive, extract their 2 IMMEDIATE prior roles
  chronologically (NOT their current role at {payer_name}).
  * Prioritize their most recent previous titles WITHIN the current
    organization (e.g. if the current CEO was previously COO of the same
    company, that COO role is Past Job 1).
  * Do NOT skip over recent internal promotions to pull 10-year-old jobs
    from previous employers.
  * Search the evidence carefully for phrases like "previously", "prior
    to joining", "before joining", "formerly", "joined from", "came from",
    "background includes", "career includes".
  * If the executive has spent their entire career at {payer_name}, use
    their two most recent INTERNAL roles (different titles / departments)
    and set "years" to "internal promotion".
  * Do NOT return an empty list unless you have absolutely no career
    history evidence whatsoever.
  * Format as a list of objects:
  [{{"firm": "Anthem", "title": "VP Technology", "years": "2018-2022"}}, ...].
  If years are not mentioned, use an empty string for "years".
- DEPARTURE RISK: If any evidence mentions that the executive is retiring,
  stepping down, has announced a departure, or that a successor has been named,
  set "departure_risk": true and provide a short "departure_note" (e.g.
  "Announced retirement by end of 2026; successor Jenny Housley named President
  Apr 2026"). Otherwise set "departure_risk": false.
- The `linkedin_url` must be a real linkedin.com/in/ or linkedin.com/pub/ URL
  taken VERBATIM from one of the evidence items — do not fabricate URLs.
- TITLE FORMAT (v3.5): For any non-CEO persona (CIO, CMO, Chief Medical,
  VP Experience), NEVER write a bare title of "President", "President and
  CEO", or "Chief Executive Officer". If the only available title for a
  candidate is one of those top-of-org titles, OMIT that candidate from
  the non-CEO persona slot entirely — they belong in the CEO slot, not
  here.
- PAST_JOBS — NO SCHOOLS (v3.5): Educational institutions (universities,
  colleges, schools, institutes, academies, polytechnics, seminaries,
  conservatories) are NOT past jobs. Do not include them in `past_jobs`.
  Past_jobs must be professional employment positions only.
- PAST JOBS (STRICT ANTI-HALLUCINATION RULE):
    * You MUST extract past jobs ONLY from the provided evidence snippets.
    * NEVER guess, infer, or use outside knowledge to fill in past jobs.
    * If the evidence does not explicitly state the executive's prior employer
        and title, you MUST leave the `past_jobs` list empty.
    * Do NOT assign past jobs that belong to a different person with a similar
        name.

REGEX PRE-EXTRACTION (Layer 1 candidates from leadership-page bodies):
{regex_blob}

These are deterministically extracted (title, name) pairs from fetched pages.
Treat them as strong candidates — if a regex-extracted name appears alongside
the right title at the payer's own /leadership URL, that is High-confidence
evidence. You may still override if the press-release / LinkedIn evidence
clearly contradicts.

BD_NOTES guidance: a 1-2 sentence strategic note for the BD analyst noting
any recent leadership changes, warm-intro opportunities (e.g. "CIO recently
joined from Anthem"), or tenure signals ("CMO in role 5+ years").

PER-EXECUTIVE bd_note guidance (v3.4 — REQUIRED for every identified exec):
For each executive you place into a persona slot, also write a `bd_note`
that is SPECIFIC to that individual (NOT a copy of the payer-level
`bd_notes`). The per-exec `bd_note` must be 2-3 sentences:
  1. Sentence 1: this executive's background or tenure at {payer_name}
     (e.g. "Promoted from COO in March 2023 after 8 years internal tenure.").
  2. Sentence 2: explicitly note whether this leader's scope is
      Medicaid/Government Programs or enterprise-wide fallback.
  3. Sentence 3: include an AArete engagement angle tailored to Medicaid
      priorities (e.g., redeterminations, state RFP/procurement cycles,
      MLR optimization, or government-program cost reduction).
Forbidden: starting the per-exec `bd_note` with "The payer ...", "{payer_name}
has ...", or copying the payer-level `bd_notes` text verbatim. If the bd_note
would just restate the payer-level summary, write a shorter individual note
instead (background + scope + engagement angle).

OUTPUT — strict JSON only, no markdown, no prose outside the JSON:
{{
  "executives": {{
    "CEO": {{
      "name": "...",
      "title": "...",
      "current_employer_extracted": "...",
      "linkedin_url": "https://www.linkedin.com/in/...",
      "past_jobs": [
        {{"firm": "...", "title": "...", "years": "..."}},
        {{"firm": "...", "title": "...", "years": "..."}}
      ],
      "departure_risk": false,
      "departure_note": "",
      "evidence_indices": [0, 3],
      "bd_note": "2-3 sentence individual note: background, transition/news, AArete angle"
    }},
    "CIO": {{ ... }},
    ...
  }},
  "bd_notes": "1-2 sentence strategic note",
  "key_evidence_summary": "2-3 sentence narrative of the strongest evidence"
}}

CRITICAL: For each executive you MUST set `current_employer_extracted` to the
exact current employer string taken from the strongest piece of evidence (e.g.
"Curry Health Network" or "Independence Blue Cross"). The Python validator
will REJECT any executive whose extracted employer does not match {payer_name}
(or one of its aliases). When uncertain, leave the field as the literal
employer text rather than guessing the payer name.

EVIDENCE (JSON array):
{evidence_blob}
""".strip()

    task = Task(
        description=description,
        expected_output=(
            'Strict JSON: {"executives": {"<Role>": {"name", "title", '
            '"current_employer_extracted", "linkedin_url", "past_jobs", '
            '"departure_risk", "departure_note", "evidence_indices"}}, '
            '"bd_notes", "key_evidence_summary"}'
        ),
        agent=__import__(
            "payer_intel.agents", fromlist=["executive_classifier_agent"]
        ).executive_classifier_agent(),
    )
    crew = Crew(
        agents=[task.agent],
        tasks=[task],
        process=Process.sequential,
        verbose=False,
    )
    result = crew.kickoff()
    text = str(result).strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.MULTILINE)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        log.warning("Executive classifier returned non-JSON; raw=%r", text[:300])
        return {}, "", ""

    raw_execs = data.get("executives") or {}
    bd_notes = (data.get("bd_notes") or "").strip()
    summary = (data.get("key_evidence_summary") or "").strip()

    valid_roles = {r.value: r for r in ExecutiveRole}
    out: dict[ExecutiveRole, dict] = {}
    for role_name, profile in raw_execs.items():
        role = valid_roles.get(role_name)
        if role is None or not isinstance(profile, dict):
            continue
        name = (profile.get("name") or "").strip()
        if not name:
            continue
        # Python-level deceased guard: drop any candidate whose full name
        # appears alongside a deceased-signal phrase in the evidence.
        if _is_known_deceased(name, evidence):
            log.warning(
                "Dropping %s (%s): deceased signal found in evidence for %s",
                name, role_name, payer_name,
            )
            continue
        # Hard payer-match validation: drop any executive whose extracted
        # current employer cannot be tied back to the payer's alias set.
        employer = str(profile.get("current_employer_extracted") or "").strip().lower()
        if employer and not _employer_matches_payer(employer, payer_aliases_lower):
            log.warning(
                "Dropping %s (%s): extracted employer %r does not match %s aliases",
                name, role_name, employer, payer_name,
            )
            continue
        # v3.9: hard cross-payer contamination guard. Drop any candidate whose
        # extracted employer or title contains a string from the payer's
        # search_excludes list (e.g. an exec of Medical Mutual surfacing as
        # CMO for Horizon BCBSNJ).
        excludes_raw = payer.get("search_excludes") or ""
        excludes_lower = {x.strip().lower() for x in excludes_raw.split("|") if x.strip()}
        if excludes_lower:
            title_lower_chk = (profile.get("title") or "").strip().lower()
            if any(ex in employer for ex in excludes_lower) or any(
                ex in title_lower_chk for ex in excludes_lower
            ):
                log.warning(
                    "Dropping %s (%s): employer/title contains excluded cross-payer entity",
                    name, role_name,
                )
                continue
        # v3.4: Python-level persona title gate. Hard-rejects e.g. a Chief
        # Growth Officer in the CMO slot or a Quality VP in VP Experience.
        title_for_filter = (profile.get("title") or "").strip()
        # v2.1: hard guard against enterprise CEO leakage for national/blues
        # payers. Pure-play Medicaid MCOs are exempt.
        if role == ExecutiveRole.CEO:
            if _is_enterprise_ceo_of_national_payer(
                payer_name, payer.get("payer_type", ""), title_for_filter
            ):
                log.warning(
                    "Dropping %s (%s) for %s: enterprise CEO title %r blocked by Medicaid guard",
                    name, role_name, payer_name, title_for_filter,
                )
                continue
        # Check for persona mismatch (e.g., operations exec in clinical slot)
        if not _is_valid_persona_match(title_for_filter, role_name):
            log.warning(
                "Dropping %s (%s) for %s: title %r does not match persona requirements",
                name, role_name, payer_name, title_for_filter,
            )
            continue
        if not _title_passes_persona_filter(role, title_for_filter):
            log.warning(
                "Dropping %s (%s) for %s: title %r fails persona reject filter",
                name, role_name, payer_name, title_for_filter,
            )
            continue
        linkedin_url = (profile.get("linkedin_url") or "").strip() or None
        # Reject hallucinated LinkedIn URLs (must appear in evidence)
        if linkedin_url:
            evidence_urls = {e.url for e in evidence if e.url}
            if linkedin_url not in evidence_urls:
                # Loose match: any evidence URL that contains the same /in/<slug>
                slug_match = re.search(r"/in/([^/?#]+)", linkedin_url)
                slug = slug_match.group(1).lower() if slug_match else None
                if not slug or not any(slug in (u or "").lower() for u in evidence_urls):
                    log.info(
                        "Dropping fabricated LinkedIn URL %s for %s/%s",
                        linkedin_url, payer_name, role_name,
                    )
                    linkedin_url = None
        # v3.5: hard URL-format gate. Catches non-LinkedIn URLs (e.g.
        # YouTube) that survive the evidence-presence check because the
        # bad URL happens to appear elsewhere in the evidence blob.
        linkedin_url = _validate_linkedin_url(linkedin_url)
        past_jobs_raw = profile.get("past_jobs") or []
        past_jobs: list[dict[str, str]] = []
        # v3.5: filter education entries AND keep up to 2 valid jobs (do
        # not lose slot 2 if slot 1 was an education entry).
        for job in past_jobs_raw:
            if len(past_jobs) >= 2:
                break
            if not isinstance(job, dict):
                continue
            firm = str(job.get("firm", "")).strip()
            if not firm:
                continue
            if _is_education_firm(firm):
                continue
            past_jobs.append({
                "firm": firm,
                "title": str(job.get("title", "")).strip(),
                "years": str(job.get("years", "")).strip(),
            })
        departure_risk = bool(profile.get("departure_risk", False))
        departure_note = str(profile.get("departure_note") or "").strip() or None
        evidence_indices = [
            i for i in (profile.get("evidence_indices") or [])
            if isinstance(i, int) and 0 <= i < len(evidence)
        ]
        out[role] = {
            "name": name,
            "title": (profile.get("title") or "").strip() or None,
            "linkedin_url": linkedin_url,
            "past_jobs": past_jobs,
            "departure_risk": departure_risk,
            "departure_note": departure_note,
            "evidence_indices": evidence_indices,
            "bd_note": str(profile.get("bd_note") or "").strip(),
        }
    return out, bd_notes, summary


def assemble_executive_record(
    payer: dict[str, str],
    classified: dict[ExecutiveRole, dict],
    all_evidence: list[Evidence],
    bd_notes: str,
    key_evidence_summary: str,
) -> ExecutivePayerRecord:
    rec = ExecutivePayerRecord(
        payer_name=payer["payer_name"],
        payer_type=payer.get("payer_type", ""),
        domain=payer.get("domain", ""),
    )

    per_exec_conf: list[ConfidenceScore] = []
    for role in ExecutiveRole:
        info = classified.get(role)
        if not info:
            # Empty profile (role not identified) — still record so export
            # renders an empty cell rather than throwing.
            rec.executives[role] = ExecutiveProfile()
            continue
        evs = [all_evidence[i] for i in info.get("evidence_indices", [])]
        qc = exec_score(evs)
        raw_jobs = info.get("past_jobs", [])
        departure_risk = info.get("departure_risk", False)
        departure_note = info.get("departure_note")
        already_departed = departure_risk and _is_already_departed(departure_note)
        profile = ExecutiveProfile(
            name=None if already_departed else info.get("name"),
            title=None if already_departed else info.get("title"),
            linkedin_url=None if already_departed else info.get("linkedin_url"),
            past_jobs=[PastJob(**j) for j in raw_jobs if isinstance(j, dict)],
            departure_risk=departure_risk,
            departure_note=departure_note,
            confidence=qc.confidence,
            confidence_note=qc.note,
            evidence=evs,
            bd_note="" if already_departed else (info.get("bd_note") or ""),
        )
        rec.executives[role] = profile
        per_exec_conf.append(qc.confidence)

    rec.confidence = exec_aggregate_confidence(per_exec_conf) if per_exec_conf else ConfidenceScore.LOW

    # Source URLs — prioritize evidence that supported identified executives
    urls: list[str] = []
    for profile in rec.executives.values():
        for ev in profile.evidence:
            if ev.url and ev.url not in urls:
                urls.append(ev.url)
    # Then any other URLs (leadership pages, press releases that didn't feed a profile)
    for ev in all_evidence:
        if ev.url and ev.url not in urls:
            urls.append(ev.url)
    rec.source_urls = urls[:8]

    rec.date_verified = datetime.utcnow().strftime("%Y-%m-%d")
    rec.bd_notes = bd_notes or _default_exec_bd_notes(rec)
    rec.key_evidence = key_evidence_summary
    # v3.4: per-row fallback note for empty slots (e.g. "No public CIO
    # identified for X. Consider targeting the CEO or COO for technology
    # conversations."). Done AFTER the payer-level bd_notes is finalized so
    # the empty-slot text references the correct payer name.
    _populate_empty_slot_bd_notes(rec)
    return rec


_EMPTY_SLOT_TOPIC: dict[ExecutiveRole, str] = {
    ExecutiveRole.CIO: "technology",
    ExecutiveRole.CMO: "marketing",
    ExecutiveRole.CHIEF_MEDICAL: "clinical and quality",
    ExecutiveRole.VP_EXPERIENCE: "member experience",
}


def _populate_empty_slot_bd_notes(rec: ExecutivePayerRecord) -> None:
    """Write a per-row fallback bd_note for personas with no identified exec.

    Skips CEO (CEO empty is rare and best left to the payer-level note).
    Skips already-populated bd_notes so retried/filled slots are untouched.
    """
    for role, topic in _EMPTY_SLOT_TOPIC.items():
        profile = rec.executives.get(role)
        if profile is None:
            continue
        if profile.name or profile.bd_note:
            continue
        profile.bd_note = (
            f"No public {role.value} identified for {rec.payer_name}. "
            f"Consider targeting the CEO or COO for {topic} conversations."
        )


def _default_exec_bd_notes(rec: ExecutivePayerRecord) -> str:
    identified = [r for r, p in rec.executives.items() if p.name]
    if not identified:
        return "No executives identified — re-run with expanded search or seed leadership URL."
    vacant = [
        r for r, p in rec.executives.items()
        if p.departure_risk and not p.name
    ]
    departing = [
        r for r, p in rec.executives.items()
        if p.name and p.departure_risk
    ]
    notes = (
        f"{len(identified)}/5 executive roles identified ({rec.confidence.value} confidence). "
        "Validate via direct outreach before referencing in BD pitch."
    )
    if vacant:
        roles_str = ", ".join(r.value for r in vacant)
        notes = f"[DEPARTED — slot vacant: {roles_str}] " + notes
    if departing:
        roles_str = ", ".join(r.value for r in departing)
        notes = f"[DEPARTURE RISK/RETIRING — {roles_str}] " + notes
    all_evidence_dates = [
        e.date for p in rec.executives.values() if p.name
        for e in p.evidence if e.date
    ]
    if all_evidence_dates and not any(
        _within_days(d, 365) for d in all_evidence_dates
    ):
        notes += " [Verify — all evidence >12 months old]"
    return notes


# ─────────────────────────────────────────────────────────────────────────────
# v3.4 — Empty-slot retry for large payers
# ─────────────────────────────────────────────────────────────────────────────
# Small Medicaid MCOs frequently don't publish a named CIO/CMO/VPX, so retry
# is wasteful. Match on lowercase substring of payer name.
_SMALL_PAYERS_FOR_RETRY: frozenset[str] = frozenset({
    "alameda alliance", "calviva", "careoregon",
})

_RETRY_PERSONA_QUERY: dict[ExecutiveRole, str] = {
    ExecutiveRole.CIO: '("Chief Information Officer" OR "CIO" OR "Chief Digital") AND (Medicaid OR "Government Programs")',
    ExecutiveRole.CMO: '("Chief Marketing Officer" OR "VP Marketing" OR "SVP Marketing" OR "Chief Brand") AND (Medicaid OR "Government Programs")',
    ExecutiveRole.CHIEF_MEDICAL: '("Chief Medical Officer" OR "CMO") AND (Medicaid OR "Government Programs")',
    ExecutiveRole.VP_EXPERIENCE: (
        '("Chief Experience Officer" OR "VP Customer Experience" '
        'OR "VP Member Experience" OR "VP Consumer Experience" OR "SVP Experience") '
        'AND (Medicaid OR "Government Programs")'
    ),
}

_RETRY_TARGET_ROLES: tuple[ExecutiveRole, ...] = (
    ExecutiveRole.CIO,
    ExecutiveRole.CMO,
    ExecutiveRole.CHIEF_MEDICAL,
    ExecutiveRole.VP_EXPERIENCE,
)

_MAX_RETRIES_PER_PAYER = 3

# v3.5: cross-persona deduplication. If the LLM places the same executive
# in two slots (e.g. Mike Gerrish in BCBSK CMO AND VP Experience because
# his title is "Chief Marketing and Experience Officer"), keep only the
# higher-priority slot.
_PERSONA_PRIORITY: tuple[ExecutiveRole, ...] = (
    ExecutiveRole.CEO,
    ExecutiveRole.CHIEF_MEDICAL,
    ExecutiveRole.CIO,
    ExecutiveRole.CMO,
    ExecutiveRole.VP_EXPERIENCE,
)


def _normalize_persona_name(n: str) -> str:
    # v3.9: fuzzy dedup key — first-initial + last name so "Mike Gerrish"
    # and "Michael Gerrish" collapse to the same key.
    parts = re.findall(r"[a-z]+", n.lower())
    if len(parts) >= 2:
        return f"{parts[0][0]}_{parts[-1]}"
    return n.strip().lower()


def _deduplicate_personas(
    classified: dict[ExecutiveRole, dict],
) -> dict[ExecutiveRole, dict]:
    """Drop lower-priority duplicates when the same name appears in two slots."""
    seen: dict[str, ExecutiveRole] = {}
    for role in _PERSONA_PRIORITY:
        info = classified.get(role)
        if not info:
            continue
        name = (info.get("name") or "").strip()
        if not name:
            continue
        norm = _normalize_persona_name(name)
        if norm in seen:
            log.warning(
                "Deduplicating %s: already in %s slot, clearing %s slot",
                name, seen[norm].value, role.value,
            )
            classified.pop(role, None)
        else:
            seen[norm] = role
    return classified


def _retry_empty_slot(
    payer: dict[str, str], role: ExecutiveRole, client: SearchApiClient
) -> list[Evidence]:
    """Run one targeted Google search for a missing persona. Returns evidence
    items (may be empty). Caller passes the evidence to the standard
    classifier so all validation guards (deceased, employer-match, title
    filter) re-run on retry results.
    """
    payer_name = payer["payer_name"]
    query_term = _RETRY_PERSONA_QUERY.get(role, role.value)
    # v3.8: include the payer's own domain so /leadership and press-release
    # pages on the payer site are returned alongside third-party trade press.
    domain = (payer.get("domain") or "").strip().lower()
    domain_clause = f" OR site:{domain}" if domain else ""
    query = (
        f'"{payer_name}" {query_term} '
        f"(site:linkedin.com OR site:beckerspayer.com OR site:modernhealthcare.com{domain_clause})"
    )
    out: list[Evidence] = []
    for r in _safe_search(client.google, query, num=5):
        out.append(
            Evidence(
                source_type="executive_news",
                url=r.get("link", "") or "",
                snippet=(r.get("snippet") or "")[:1500],
                date=r.get("date"),
            )
        )
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Top-level entry — executive mode
# ─────────────────────────────────────────────────────────────────────────────
def run_executive(seed_path: Path, out_dir: Path) -> Path:
    payers = load_seed(seed_path)
    client = SearchApiClient()

    records: list[ExecutivePayerRecord] = []
    for p in payers:
        log.info("Processing payer (executive mode): %s", p["payer_name"])
        evidence = gather_executive_evidence(p, client)
        if evidence:
            classified, bd_notes, summary = _classify_executives_with_llm(p, evidence)
        else:
            classified, bd_notes, summary = {}, "", ""
        # v3.5: dedup after the initial pass so a duplicate doesn't get
        # retried (e.g. CMO=Gerrish and VPX=Gerrish → clear VPX, then
        # retry can run a fresh VPX search).
        classified = _deduplicate_personas(classified)

        # v3.7: dynamic retry trigger. Replaces the hardcoded
        # _SMALL_PAYERS_FOR_RETRY allowlist. If the initial LLM pass found
        # fewer than 3 executives, treat the payer as thin-coverage and
        # skip retries to save SearchApi quota.
        found_count = sum(1 for info in classified.values() if info.get("name"))
        if evidence and found_count >= 3:
            retries_used = 0
            for role in _RETRY_TARGET_ROLES:
                if retries_used >= _MAX_RETRIES_PER_PAYER:
                    break
                if role in classified:
                    continue
                retry_ev = _retry_empty_slot(p, role, client)
                retries_used += 1
                if not retry_ev:
                    continue
                # Append retry evidence so source_urls reflect the broader search.
                evidence.extend(retry_ev)
                retry_classified, _rbd, _rsum = _classify_executives_with_llm(p, retry_ev)
                if role in retry_classified:
                    classified[role] = retry_classified[role]
                    log.info(
                        "Recovered %s for %s via empty-slot retry",
                        role.value, p["payer_name"],
                    )
            # v3.5: dedup again after retries — a retry could have pulled
            # back the same person already claimed by a higher-priority slot.
            classified = _deduplicate_personas(classified)

        rec = assemble_executive_record(p, classified, evidence, bd_notes, summary)
        records.append(rec)

    return write_excel_executive(records, out_dir)
