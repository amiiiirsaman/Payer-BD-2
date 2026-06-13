"""Tests for the deterministic executive regex extractor (Layer 1)."""
from payer_intel.crew import (
    _extract_executives_deterministic,
    _is_education_firm,
    _title_passes_persona_filter,
    _title_to_role,
    _validate_linkedin_url,
)
from payer_intel.schema import Evidence, ExecutiveRole


def test_title_to_role_disambiguates_chief_medical_from_marketing():
    assert _title_to_role("Chief Medical Officer") == ExecutiveRole.CHIEF_MEDICAL
    assert _title_to_role("Chief Marketing Officer") == ExecutiveRole.CMO
    assert _title_to_role("Chief Information Officer") == ExecutiveRole.CIO
    assert _title_to_role("Chief Executive Officer") == ExecutiveRole.CEO
    assert _title_to_role("Chief Experience Officer") == ExecutiveRole.VP_EXPERIENCE


def test_title_to_role_handles_short_forms():
    assert _title_to_role("CIO") == ExecutiveRole.CIO
    assert _title_to_role("CEO") == ExecutiveRole.CEO


def test_title_to_role_returns_none_for_unrecognized():
    assert _title_to_role("Senior Director of Operations") is None


def test_title_to_role_rejects_growth_officer():
    """v3.3: Chief Growth Officer must NOT auto-map to CMO (Brian Keller / BCBSLA).
    The LLM prompt now treats CGO as REJECT for the CMO persona."""
    assert _title_to_role("Chief Growth Officer") is None
    assert _title_to_role("EVP, Chief Growth Officer") is None
    assert _title_to_role("VP Sales and Marketing") is None


def test_title_passes_persona_filter_rejects_growth_officer():
    """v3.4: hard Python-level reject for CMO/VPX titles that the LLM keeps
    sneaking past the prompt REJECT lists."""
    # CMO: Chief Growth Officer rejected, true CMO accepted
    assert _title_passes_persona_filter(
        ExecutiveRole.CMO, "Executive Vice President, Chief Growth Officer"
    ) is False
    assert _title_passes_persona_filter(
        ExecutiveRole.CMO, "Senior Vice President and Chief Marketing Officer"
    ) is True
    assert _title_passes_persona_filter(
        ExecutiveRole.CMO, "Chief Brand Officer"
    ) is True
    # VP Experience: Quality VP rejected, true CXO accepted
    assert _title_passes_persona_filter(
        ExecutiveRole.VP_EXPERIENCE,
        "Vice President, Quality Performance and Population Health",
    ) is False
    assert _title_passes_persona_filter(
        ExecutiveRole.VP_EXPERIENCE, "Chief Experience Officer"
    ) is True
    # Non-gated personas (CEO, CIO, Chief Medical) always pass
    assert _title_passes_persona_filter(ExecutiveRole.CEO, "President and CEO") is True
    assert _title_passes_persona_filter(ExecutiveRole.CIO, "") is True
    assert _title_passes_persona_filter(ExecutiveRole.CIO, None) is True


def test_title_passes_persona_filter_rejects_top_of_org_for_non_ceo():
    """v3.5: universal reject — President / CEO / COO / CFO / GC must NOT
    be placed in a non-CEO persona slot (Jenny Housley "President" case)."""
    # President-and-CEO compound → rejected for CMO, accepted for CEO
    assert _title_passes_persona_filter(
        ExecutiveRole.CMO, "President and CEO"
    ) is False
    assert _title_passes_persona_filter(
        ExecutiveRole.CIO, "Chief Executive Officer"
    ) is False
    assert _title_passes_persona_filter(
        ExecutiveRole.CHIEF_MEDICAL, "Chief Operating Officer"
    ) is False
    assert _title_passes_persona_filter(
        ExecutiveRole.VP_EXPERIENCE, "General Counsel"
    ) is False
    # CEO persona accepts top-of-org titles
    assert _title_passes_persona_filter(
        ExecutiveRole.CEO, "President and CEO"
    ) is True
    # Vice President titles must STILL pass for VP Experience (no bare
    # "president" substring trap).
    assert _title_passes_persona_filter(
        ExecutiveRole.VP_EXPERIENCE, "Vice President of Member Experience"
    ) is True


def test_validate_linkedin_url_rejects_non_linkedin():
    """v3.5: catches Christopher Donovan's YouTube URL leaking into the
    LinkedIn column."""
    assert _validate_linkedin_url(
        "https://www.youtube.com/watch?v=lGo2AxM3okc"
    ) is None
    assert _validate_linkedin_url("https://example.com/foo") is None
    assert _validate_linkedin_url("") is None
    assert _validate_linkedin_url(None) is None
    # Valid LinkedIn profile URLs (both /in/ and /pub/)
    assert _validate_linkedin_url(
        "https://www.linkedin.com/in/jane-doe-humana/"
    ) == "https://www.linkedin.com/in/jane-doe-humana/"
    assert _validate_linkedin_url(
        "https://linkedin.com/in/jane-doe"
    ) == "https://linkedin.com/in/jane-doe"
    assert _validate_linkedin_url(
        "https://www.linkedin.com/pub/john-smith/12/345/678"
    ) == "https://www.linkedin.com/pub/john-smith/12/345/678"


def test_is_education_firm_filters_universities():
    """v3.5: catches CareOregon's CIO past_jobs["University of Rochester"]."""
    assert _is_education_firm("University of Rochester") is True
    assert _is_education_firm("Stanford University") is True
    assert _is_education_firm("Harvard Business School") is True
    assert _is_education_firm("MIT (education/prior role)") is True
    assert _is_education_firm("Wharton") is False
    assert _is_education_firm("Anthem") is False
    assert _is_education_firm("") is False
    assert _is_education_firm(None) is False


def test_extractor_pulls_title_name_from_leadership_body():
    body = (
        "Our leadership team includes Chief Executive Officer Jane Doe, "
        "Chief Information Officer John Smith, and Chief Medical Officer "
        "Maria Garcia."
    )
    ev = Evidence(
        source_type="leadership_page",
        url="https://humana.com/about/leadership",
        full_text=body,
    )
    out = _extract_executives_deterministic([ev], payer_aliases_lower={"humana"})
    ceo_names = [c["name"] for c in out[ExecutiveRole.CEO]]
    cio_names = [c["name"] for c in out[ExecutiveRole.CIO]]
    medical_names = [c["name"] for c in out[ExecutiveRole.CHIEF_MEDICAL]]
    assert "Jane Doe" in ceo_names
    assert "John Smith" in cio_names
    assert "Maria Garcia" in medical_names


def test_extractor_skips_payer_self_reference():
    body = "Humana Foundation announces Chief Executive Officer Humana Inc."
    ev = Evidence(source_type="leadership_page", url="https://x", full_text=body)
    out = _extract_executives_deterministic(
        [ev], payer_aliases_lower={"humana inc.", "humana inc"}
    )
    # Payer-name token should not be captured as an executive name
    assert all(c["name"].lower() != "humana inc." for c in out[ExecutiveRole.CEO])


def test_extractor_handles_name_comma_title_pattern():
    body = "Jane Doe, Chief Information Officer, joined the team in 2022."
    ev = Evidence(source_type="leadership_page", url="https://x", full_text=body)
    out = _extract_executives_deterministic([ev], payer_aliases_lower=set())
    assert any(c["name"] == "Jane Doe" for c in out[ExecutiveRole.CIO])


def test_extractor_captures_past_firms():
    body = (
        "Chief Information Officer Jane Doe joined Humana in 2023. "
        "Previously, she served as VP of Technology at Anthem."
    )
    ev = Evidence(source_type="leadership_page", url="https://x", full_text=body)
    out = _extract_executives_deterministic([ev], payer_aliases_lower={"humana"})
    cio_candidates = out[ExecutiveRole.CIO]
    assert cio_candidates
    past_firms = cio_candidates[0]["past_firms"]
    assert any("Anthem" in f for f in past_firms)


def test_extractor_returns_empty_for_empty_evidence():
    out = _extract_executives_deterministic([], payer_aliases_lower=set())
    assert all(out[r] == [] for r in ExecutiveRole)
