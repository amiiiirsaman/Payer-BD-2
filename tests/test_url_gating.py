"""Tests for the v6 URL/body gating helpers in crew.py."""
from payer_intel.crew import (
    _evidence_body_contains_exclude,
    _is_zero_evidence_url,
    _salesforce_blog_lacks_customer_verb,
    _si_partner_requires_payer_mention,
    build_excludes_set,
)


# ─── _is_zero_evidence_url ─────────────────────────────────────────────
def test_blog_category_is_zero_evidence():
    assert _is_zero_evidence_url("https://www.salesforce.com/blog/category/personalization/")


def test_blog_tag_is_zero_evidence():
    assert _is_zero_evidence_url("https://www.salesforce.com/blog/tag/health-cloud/")


def test_blog_author_is_zero_evidence():
    assert _is_zero_evidence_url("https://www.salesforce.com/blog/author/jane-doe/")


def test_blog_paginated_is_zero_evidence():
    assert _is_zero_evidence_url("https://www.salesforce.com/blog/page/39/")


def test_blog_root_is_zero_evidence():
    assert _is_zero_evidence_url("https://www.salesforce.com/blog/")
    assert _is_zero_evidence_url("https://www.salesforce.com/blog")


def test_blog_article_is_not_zero_evidence():
    assert not _is_zero_evidence_url(
        "https://www.salesforce.com/blog/future-of-cigna-personalization/"
    )


def test_empty_url_is_not_zero_evidence():
    assert not _is_zero_evidence_url("")


# ─── _si_partner_requires_payer_mention ────────────────────────────────
def test_si_partner_drop_when_payer_absent():
    assert _si_partner_requires_payer_mention(
        "https://www.accenture.com/whitepaper.pdf",
        "Generic Salesforce Service Cloud telecom industry overview.",
        {"elevance health", "anthem"},
    )


def test_si_partner_keep_when_payer_named():
    assert not _si_partner_requires_payer_mention(
        "https://www.accenture.com/case-study.pdf",
        "Elevance Health deployed Service Cloud with Accenture.",
        {"elevance health", "anthem"},
    )


def test_si_partner_keep_when_body_and_snippet_missing():
    # Both body and snippet empty → no text to evaluate; let LLM see it
    # with the FP-06 prompt guardrail.
    assert not _si_partner_requires_payer_mention(
        "https://www.deloitte.com/insights/x.html",
        None,
        {"geisinger"},
        snippet=None,
    )


def test_si_partner_drop_when_alias_only_in_url_fragment():
    # FP-06 strict: "anthem" appearing only inside a .pdf path fragment
    # is not a visible mention. Mirrors the v6.1.1 Elevance/Accenture case.
    assert _si_partner_requires_payer_mention(
        "https://www.accenture.com/brochure.pdf",
        (
            "Martha uses her agent desktop, powered by Salesforce Service "
            "Cloud case ... pdf-68/accenture-global-anthem-pov.pdf. 7 "
            "Northridge Group State of Customer ..."
        ),
        {"elevance health", "anthem"},
    )


def test_si_partner_keep_when_alias_visible_outside_urls():
    assert not _si_partner_requires_payer_mention(
        "https://www.accenture.com/brochure.pdf",
        (
            "Anthem rolled out Service Cloud across member services. See "
            "https://www.accenture.com/global-pov.pdf for the full case."
        ),
        {"elevance health", "anthem"},
    )


def test_si_partner_drop_snippet_only_without_payer():
    # Body unavailable (e.g. PDF could not be fetched). Snippet does not
    # name the payer → drop per FP-06 strict.
    assert _si_partner_requires_payer_mention(
        "https://www.accenture.com/brochure.pdf",
        None,
        {"elevance health", "anthem"},
        snippet="Accenture Salesforce Service Cloud telecommunications overview.",
    )


def test_si_partner_keep_snippet_only_when_payer_named():
    assert not _si_partner_requires_payer_mention(
        "https://www.accenture.com/brochure.pdf",
        None,
        {"elevance health", "anthem"},
        snippet="Elevance Health partnered with Accenture on Service Cloud.",
    )


def test_si_partner_drop_when_snippet_payer_only_in_url():
    assert _si_partner_requires_payer_mention(
        "https://www.accenture.com/brochure.pdf",
        None,
        {"elevance health", "anthem"},
        snippet="See accenture-anthem-pov.pdf for details.",
    )


def test_non_si_host_never_dropped():
    assert not _si_partner_requires_payer_mention(
        "https://www.salesforce.com/blog/x/",
        "No payer name here.",
        {"cigna"},
    )


# ─── _salesforce_blog_lacks_customer_verb ──────────────────────────────
def test_sf_blog_drops_when_no_verb_near_payer():
    assert _salesforce_blog_lacks_customer_verb(
        "https://www.salesforce.com/blog/improve-member-engagement/",
        "Cigna is one of many payers exploring new ideas in member outreach.",
        {"cigna"},
    )


def test_sf_blog_keeps_when_verb_near_payer():
    assert not _salesforce_blog_lacks_customer_verb(
        "https://www.salesforce.com/blog/improve-member-engagement/",
        "Cigna deployed Health Cloud across its member-services team.",
        {"cigna"},
    )


def test_sf_blog_keeps_snippet_only_items():
    # body=None → snippet-only; let LLM handle with prompt guardrail.
    assert not _salesforce_blog_lacks_customer_verb(
        "https://www.salesforce.com/blog/x/",
        None,
        {"cigna"},
    )


def test_non_blog_sf_url_never_dropped_by_this_check():
    assert not _salesforce_blog_lacks_customer_verb(
        "https://www.salesforce.com/customer-success-stories/cigna/",
        "Cigna chose Salesforce.",
        {"cigna"},
    )


# ─── _evidence_body_contains_exclude ───────────────────────────────────
def test_exclude_drops_when_sibling_named_payer_absent():
    body = "AmeriHealth Caritas hiring a Salesforce Marketing Cloud Specialist."
    assert _evidence_body_contains_exclude(
        body,
        {"amerihealth caritas", "amerihealth new jersey"},
        {"independence blue cross", "ibx"},
    )


def test_exclude_keeps_when_both_named():
    body = (
        "Independence Blue Cross and sister entity AmeriHealth Caritas both "
        "use Salesforce platforms."
    )
    assert not _evidence_body_contains_exclude(
        body,
        {"amerihealth caritas"},
        {"independence blue cross", "ibx"},
    )


def test_exclude_no_op_when_excludes_empty():
    assert not _evidence_body_contains_exclude(
        "AmeriHealth Caritas does stuff.",
        set(),
        {"independence blue cross"},
    )


def test_exclude_no_op_when_body_missing():
    assert not _evidence_body_contains_exclude(
        None,
        {"amerihealth caritas"},
        {"independence blue cross"},
    )


# ─── build_excludes_set ────────────────────────────────────────────────
def test_build_excludes_set_parses_pipe_delimited():
    payer = {"search_excludes": "AmeriHealth Caritas|AmeriHealth NJ|AmeriHealth New Jersey"}
    out = build_excludes_set(payer)
    assert out == {"amerihealth caritas", "amerihealth nj", "amerihealth new jersey"}


def test_build_excludes_set_empty_string():
    assert build_excludes_set({"search_excludes": ""}) == set()


def test_build_excludes_set_missing_column():
    assert build_excludes_set({}) == set()


def test_build_excludes_set_trims_whitespace():
    payer = {"search_excludes": "  Foo  |  Bar  "}
    assert build_excludes_set(payer) == {"foo", "bar"}
