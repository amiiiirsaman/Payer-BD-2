"""Tests for the deterministic executive regex extractor (Layer 1)."""
from payer_intel.crew import _extract_executives_deterministic, _title_to_role
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
