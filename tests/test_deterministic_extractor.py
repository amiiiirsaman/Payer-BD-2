"""Tests for the deterministic Layer 1 product extractor."""
from payer_intel.crew import _extract_products_from_body
from payer_intel.schema import Evidence


def _ev(full_text: str | None, snippet: str = "teaser") -> Evidence:
    ev = Evidence(source_type="case_study", url="https://salesforce.com/x", snippet=snippet)
    ev.full_text = full_text
    return ev


UHC_ALIASES = {"unitedhealthcare", "unitedhealth group", "uhg", "optum"}
BSC_ALIASES = {"blue shield of california", "blue shield"}
CENTENE_ALIASES = {"centene corporation", "centene", "fidelis care", "wellcare health plans"}


def test_products_used_section_extracts_list():
    ev = _ev(
        "UnitedHealthcare customer since 2010. "
        "PRODUCTS USED Service Cloud Marketing Cloud see all salesforce products"
    )
    result = _extract_products_from_body(ev, UHC_ALIASES)
    assert "Service Cloud" in result
    assert "Marketing Cloud" in result


def test_marketing_platform_synonym():
    ev = _ev("UnitedHealthcare uses Marketing Platform to drive member engagement.")
    result = _extract_products_from_body(ev, UHC_ALIASES)
    assert "Marketing Cloud" in result


def test_health_cloud_via_care_connect():
    ev = _ev("Blue Shield of California built Care Connect on Salesforce to coordinate care.")
    result = _extract_products_from_body(ev, BSC_ALIASES)
    assert "Health Cloud" in result


def test_prior_auth_maps_to_health_cloud():
    ev = _ev("Blue Shield uses Salesforce for prior authorization workflow automation.")
    result = _extract_products_from_body(ev, BSC_ALIASES)
    assert "Health Cloud" in result


def test_no_full_text_returns_empty_set():
    ev = Evidence(
        source_type="job_posting",
        url="https://linkedin.com/jobs/123",
        snippet="Salesforce administrator required",
    )
    result = _extract_products_from_body(ev, UHC_ALIASES)
    assert result == set()


def test_multiple_products_detected():
    ev = _ev(
        "UnitedHealthcare uses Service Cloud for member services, "
        "Marketing Cloud for personalised campaigns, "
        "and Health Cloud for care management programmes."
    )
    result = _extract_products_from_body(ev, UHC_ALIASES)
    assert result == {"Service Cloud", "Marketing Cloud", "Health Cloud"}


def test_generic_page_without_payer_name_returns_empty():
    generic_body = (
        "See how Agentforce Health is cutting paperwork. "
        "Health Cloud puts patients at the heart of every decision. "
        "Marketing Cloud drives action with intelligent marketing. "
        "Service Cloud builds loyalty while lowering cost of healthcare."
    )
    ev = _ev(generic_body)
    result = _extract_products_from_body(ev, CENTENE_ALIASES)
    assert result == set(), f"False positive: generic page returned {result} for Centene"


def test_proximity_guard_rejects_distant_product():
    """Payer mentioned at top, Agentforce mentioned >600 chars later — must NOT extract."""
    payer_mention = "Devoted Health was founded in 2017 as a Medicare Advantage plan. "
    filler = "Salesforce held its annual Dreamforce conference in San Francisco. " * 20
    distant_product = (
        "Across the broader Salesforce customer base, Agentforce for Healthcare "
        "is being adopted by leading providers and payers nationwide."
    )
    body = payer_mention + filler + distant_product
    ev = _ev(body)
    result = _extract_products_from_body(ev, {"Devoted Health", "Devoted"})
    assert "Agentforce for Healthcare" not in result, (
        f"Proximity guard failed: Agentforce matched at distance > {600} chars. "
        f"Body length: {len(body)}, result: {result}"
    )


def test_agentforce_rejected_without_deployment_indicator():
    """Payer near Agentforce but no deployment word — must NOT extract."""
    body = (
        "Devoted Health is a Medicare Advantage plan based in Waltham, MA. "
        "Agentforce for Healthcare is a new Salesforce product available on "
        "Trailhead with various learning modules and educational tutorials "
        "for developers who want to learn about AI agents."
    )
    ev = _ev(body)
    result = _extract_products_from_body(ev, {"Devoted Health", "Devoted"})
    assert "Agentforce for Healthcare" not in result, (
        f"Agentforce guard failed: matched without deployment indicator. result: {result}"
    )


def test_agentforce_accepted_with_payer_and_deployment_word():
    """Payer + Agentforce + deployment indicator in same window — must extract."""
    body = (
        "Devoted Health signed a contract to deploy Agentforce for Healthcare "
        "across its member-services team to automate prior-authorization triage."
    )
    ev = _ev(body)
    result = _extract_products_from_body(ev, {"Devoted Health", "Devoted"})
    assert "Agentforce for Healthcare" in result, (
        f"Agentforce should have matched (payer + deploy/contract in window). result: {result}"
    )


# ─── v6 fixes: Vlocity / OmniStudio synonyms (Aarete MS-04) ─────────────────
EMBLEM_ALIASES = {"emblemhealth", "emblem health", "ghi", "hip health plan"}


def test_vlocity_health_maps_to_health_cloud():
    ev = _ev(
        "EmblemHealth Inc. CRM Program Analyst supports digital transformation "
        "through Sales Cloud, Service Cloud, and Vlocity Health portals."
    )
    result = _extract_products_from_body(ev, EMBLEM_ALIASES)
    assert "Health Cloud" in result


def test_omnistudio_maps_to_health_cloud():
    ev = _ev(
        "EmblemHealth migrated its provider portal to OmniStudio on Salesforce "
        "Industries to streamline prior authorization workflows."
    )
    result = _extract_products_from_body(ev, EMBLEM_ALIASES)
    assert "Health Cloud" in result


# ─── v6 fixes: URL-gating rejection (Aarete FP-01, FP-02, FP-06, FP-07) ─────
def _ev_at(url: str, full_text: str) -> Evidence:
    ev = Evidence(source_type="case_study", url=url, snippet="teaser")
    ev.full_text = full_text
    return ev


def test_blog_category_page_returns_empty():
    """Aarete FP-02: BCBSM appearing in a /blog/category/ index → no evidence."""
    body = (
        "Blue Cross Blue Shield of Michigan stories. "
        "Marketing Cloud personalizes member outreach. Data Cloud unifies data."
    )
    ev = _ev_at(
        "https://www.salesforce.com/blog/category/personalization/",
        body,
    )
    result = _extract_products_from_body(
        ev, {"Blue Cross Blue Shield of Michigan", "BCBSM"}
    )
    assert result == set(), f"FP-02 regression: category page extracted {result}"


def test_blog_author_page_returns_empty():
    """Aarete FP-07: payer-name on author listing page → no evidence."""
    body = (
        "Stories by Stephanie Buscemi. UnitedHealthcare is one of many customers "
        "exploring Marketing Cloud and Health Cloud personalization."
    )
    ev = _ev_at(
        "https://www.salesforce.com/blog/author/stephanie-buscemi/",
        body,
    )
    result = _extract_products_from_body(
        ev, {"unitedhealthcare", "unitedhealth group"}
    )
    assert result == set(), f"FP-07 regression: author page extracted {result}"


def test_salesforce_blog_without_customer_verb_returns_empty():
    """Aarete FP-01: Cigna mentioned in a generic /blog/ post with no
    deployment verb → not evidence."""
    body = (
        "Improve member engagement strategies. Many payers including Cigna "
        "are exploring new approaches. Health Cloud enables proactive care "
        "for members across the healthcare ecosystem."
    )
    ev = _ev_at(
        "https://www.salesforce.com/blog/improve-member-engagement-strategies/",
        body,
    )
    result = _extract_products_from_body(ev, {"cigna", "cigna group"})
    assert result == set(), f"FP-01 regression: blog without verb extracted {result}"


def test_salesforce_blog_with_customer_verb_extracts():
    """FP-01 control: same blog URL but with a deployment verb adjacent to the
    payer mention → extraction proceeds."""
    body = (
        "Cigna Corporation deployed Health Cloud across its member-services team to "
        "improve engagement and care coordination."
    )
    ev = _ev_at(
        "https://www.salesforce.com/blog/improve-member-engagement-strategies/",
        body,
    )
    result = _extract_products_from_body(
        ev, {"Cigna Corporation", "Cigna Group", "Cigna"}
    )
    assert "Health Cloud" in result


def test_si_partner_brochure_without_payer_name_returns_empty():
    """Aarete FP-06: Accenture brochure that never names Elevance → discard."""
    body = (
        "Accenture Salesforce Service Cloud telecommunications industry "
        "transformation overview. Carriers can modernize contact centers."
    )
    ev = _ev_at(
        "https://www.accenture.com/whitepaper.pdf",
        body,
    )
    result = _extract_products_from_body(
        ev, {"elevance health", "anthem", "anthem inc"}
    )
    assert result == set(), f"FP-06 regression: unaffiliated SI brochure extracted {result}"


def test_si_partner_brochure_with_payer_name_extracts():
    """FP-06 control: same Accenture URL but body names the payer."""
    body = (
        "Elevance Health partnered with Accenture to deploy Salesforce Service "
        "Cloud for member care management at scale."
    )
    ev = _ev_at(
        "https://www.accenture.com/case-study.pdf",
        body,
    )
    result = _extract_products_from_body(ev, {"elevance health", "anthem"})
    assert "Service Cloud" in result


def test_sibling_entity_body_with_excludes_returns_empty():
    """Aarete MS-05: AmeriHealth Caritas job posting body should not be
    cross-attributed to Independence Blue Cross."""
    body = (
        "AmeriHealth Caritas is hiring a Salesforce Marketing Cloud Specialist "
        "to drive member engagement campaigns across our Medicaid plans."
    )
    ev = _ev_at("https://jobs.example.com/posting", body)
    excludes = {"amerihealth caritas", "amerihealth new jersey", "amerihealth nj"}
    result = _extract_products_from_body(
        ev, {"independence blue cross", "ibx"}, excludes
    )
    assert result == set(), f"MS-05 regression: sibling body extracted {result}"


