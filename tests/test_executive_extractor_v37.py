"""v3.7 unit tests for new title patterns and retry query expansions.

These tests are ADDITIVE — they do not modify any existing tests in
test_executive_extractor.py. All v3.5.1 and v3.6 guards are preserved.

Run with:
    pytest tests/test_executive_extractor_v37.py -v
"""
import pytest
from payer_intel.crew import (
    _RETRY_PERSONA_QUERY,
    _title_passes_persona_filter,
    _title_to_role,
)
from payer_intel.schema import ExecutiveRole, EXECUTIVE_TITLE_MAP


# ─────────────────────────────────────────────────────────────────────────────
# v3.7 Fix 1: Expanded CIO title recognition
# ─────────────────────────────────────────────────────────────────────────────

class TestCIOTitleExpansion:
    """v3.7: CIDO and CDIO must map to the CIO persona."""

    def test_cido_maps_to_cio(self):
        """Chief Digital and Information Officer must resolve to CIO."""
        assert _title_to_role("Chief Digital and Information Officer") == ExecutiveRole.CIO

    def test_cdio_maps_to_cio(self):
        """CDIO abbreviation must resolve to CIO."""
        assert _title_to_role("CDIO") == ExecutiveRole.CIO

    def test_cio_title_map_includes_cido(self):
        """EXECUTIVE_TITLE_MAP[CIO] must include CIDO for LinkedIn search queries."""
        cio_titles = EXECUTIVE_TITLE_MAP[ExecutiveRole.CIO]
        assert any("CIDO" in t or "Chief Digital and Information" in t for t in cio_titles), (
            f"CIO title map missing CIDO/CDIO. Current: {cio_titles}"
        )

    def test_existing_cio_titles_preserved(self):
        """Regression: existing CIO titles must still resolve correctly."""
        assert _title_to_role("Chief Information Officer") == ExecutiveRole.CIO
        assert _title_to_role("Chief Technology Officer") == ExecutiveRole.CIO
        assert _title_to_role("Chief Digital Officer") == ExecutiveRole.CIO
        assert _title_to_role("CIO") == ExecutiveRole.CIO
        assert _title_to_role("CTO") == ExecutiveRole.CIO


# ─────────────────────────────────────────────────────────────────────────────
# v3.7 Fix 2: Expanded VP Experience title recognition
# ─────────────────────────────────────────────────────────────────────────────

class TestVPExperienceTitleExpansion:
    """v3.7: VP Consumer Experience and SVP Experience must map to VP_EXPERIENCE."""

    def test_vp_consumer_experience_maps_to_vp_experience(self):
        """VP Consumer Experience is the title used by Florida Blue and others."""
        assert _title_to_role("VP Consumer Experience") == ExecutiveRole.VP_EXPERIENCE

    def test_svp_experience_maps_to_vp_experience(self):
        """SVP Experience is used by some Blues plans."""
        assert _title_to_role("SVP Experience") == ExecutiveRole.VP_EXPERIENCE

    def test_vp_digital_engagement_maps_to_vp_experience(self):
        """VP Digital Engagement is used by some regional plans."""
        assert _title_to_role("VP Digital Engagement") == ExecutiveRole.VP_EXPERIENCE

    def test_vp_experience_title_map_includes_new_titles(self):
        """EXECUTIVE_TITLE_MAP[VP_EXPERIENCE] must include expanded titles for search."""
        vpx_titles = EXECUTIVE_TITLE_MAP[ExecutiveRole.VP_EXPERIENCE]
        assert any("Consumer" in t for t in vpx_titles), (
            f"VP Experience title map missing 'Consumer' variant. Current: {vpx_titles}"
        )

    def test_existing_vp_experience_titles_preserved(self):
        """Regression: existing VP Experience titles must still resolve correctly."""
        assert _title_to_role("Chief Experience Officer") == ExecutiveRole.VP_EXPERIENCE
        assert _title_to_role("VP Member Experience") == ExecutiveRole.VP_EXPERIENCE
        assert _title_to_role("Chief Member Experience Officer") == ExecutiveRole.VP_EXPERIENCE
        assert _title_to_role("Chief Customer Experience Officer") == ExecutiveRole.VP_EXPERIENCE

    def test_operating_officer_still_rejected_for_vp_experience(self):
        """Regression: COO titles must still be rejected for VP Experience (Camille Harrison case)."""
        assert _title_passes_persona_filter(
            ExecutiveRole.VP_EXPERIENCE,
            "SVP & Chief Operating Officer, GuideWell Commercial Markets",
        ) is False

    def test_quality_vp_still_rejected_for_vp_experience(self):
        """Regression: Quality VP must still be rejected for VP Experience."""
        assert _title_passes_persona_filter(
            ExecutiveRole.VP_EXPERIENCE,
            "Vice President, Quality Performance and Population Health",
        ) is False


# ─────────────────────────────────────────────────────────────────────────────
# v3.7 Fix 3: Expanded CMO title recognition
# ─────────────────────────────────────────────────────────────────────────────

class TestCMORetryQueryExpansion:
    """v3.7: CMO retry query must include VP Marketing and SVP Marketing."""

    def test_cmo_retry_query_includes_vp_marketing(self):
        """VP Marketing must appear in the CMO retry query to recover small-payer CMOs."""
        cmo_query = _RETRY_PERSONA_QUERY[ExecutiveRole.CMO]
        assert "VP Marketing" in cmo_query, (
            f"CMO retry query missing 'VP Marketing'. Current: {cmo_query!r}"
        )

    def test_cmo_retry_query_includes_svp_marketing(self):
        """SVP Marketing must appear in the CMO retry query."""
        cmo_query = _RETRY_PERSONA_QUERY[ExecutiveRole.CMO]
        assert "SVP Marketing" in cmo_query, (
            f"CMO retry query missing 'SVP Marketing'. Current: {cmo_query!r}"
        )

    def test_cmo_retry_query_includes_chief_brand(self):
        """Chief Brand must appear in the CMO retry query."""
        cmo_query = _RETRY_PERSONA_QUERY[ExecutiveRole.CMO]
        assert "Chief Brand" in cmo_query, (
            f"CMO retry query missing 'Chief Brand'. Current: {cmo_query!r}"
        )

    def test_existing_cmo_titles_preserved(self):
        """Regression: Chief Marketing Officer must still map to CMO."""
        assert _title_to_role("Chief Marketing Officer") == ExecutiveRole.CMO
        assert _title_to_role("Chief Brand Officer") == ExecutiveRole.CMO

    def test_growth_officer_still_rejected_for_cmo(self):
        """Regression: Chief Growth Officer must still be rejected for CMO (Brian Keller case)."""
        assert _title_to_role("Chief Growth Officer") is None
        assert _title_passes_persona_filter(
            ExecutiveRole.CMO, "Executive Vice President, Chief Growth Officer"
        ) is False


# ─────────────────────────────────────────────────────────────────────────────
# v3.7 Fix 4: VP Experience retry query expansion
# ─────────────────────────────────────────────────────────────────────────────

class TestVPExperienceRetryQueryExpansion:
    """v3.7: VP Experience retry query must include VP Consumer Experience."""

    def test_vpx_retry_query_includes_vp_consumer_experience(self):
        """VP Consumer Experience must appear in the VP Experience retry query."""
        vpx_query = _RETRY_PERSONA_QUERY[ExecutiveRole.VP_EXPERIENCE]
        assert "VP Consumer Experience" in vpx_query, (
            f"VP Experience retry query missing 'VP Consumer Experience'. Current: {vpx_query!r}"
        )

    def test_vpx_retry_query_includes_svp_experience(self):
        """SVP Experience must appear in the VP Experience retry query."""
        vpx_query = _RETRY_PERSONA_QUERY[ExecutiveRole.VP_EXPERIENCE]
        assert "SVP Experience" in vpx_query, (
            f"VP Experience retry query missing 'SVP Experience'. Current: {vpx_query!r}"
        )

    def test_vpx_retry_query_preserves_existing_terms(self):
        """Regression: existing VP Experience retry terms must still be present."""
        vpx_query = _RETRY_PERSONA_QUERY[ExecutiveRole.VP_EXPERIENCE]
        assert "Chief Experience Officer" in vpx_query
        assert "VP Member Experience" in vpx_query


# ─────────────────────────────────────────────────────────────────────────────
# v3.7 Fix 5: Fetcher User-Agent
# ─────────────────────────────────────────────────────────────────────────────

class TestFetcherUserAgent:
    """v3.7: The fetcher User-Agent must not contain the AarateBDBot bot tag."""

    def test_ua_does_not_contain_bot_tag(self):
        """AarateBDBot/1.0 suffix must be removed to prevent WAF 403 blocks."""
        from payer_intel.tools.fetcher import _UA
        assert "AarateBDBot" not in _UA, (
            f"Fetcher UA still contains bot tag: {_UA!r}"
        )

    def test_ua_contains_chrome_string(self):
        """UA must still look like a real Chrome browser to pass WAF checks."""
        from payer_intel.tools.fetcher import _UA
        assert "Chrome" in _UA, f"Fetcher UA missing Chrome string: {_UA!r}"

    def test_ua_contains_mozilla_prefix(self):
        """UA must start with Mozilla/5.0 for broad compatibility."""
        from payer_intel.tools.fetcher import _UA
        assert _UA.startswith("Mozilla/5.0"), f"UA does not start with Mozilla/5.0: {_UA!r}"


# ─────────────────────────────────────────────────────────────────────────────
# v3.7 Regression: All v3.5.1 and v3.6 guards still intact
# ─────────────────────────────────────────────────────────────────────────────

class TestV35V36GuardsPreserved:
    """Regression suite: all v3.5.1 and v3.6 guards must still pass."""

    def test_deceased_guard_still_present(self):
        """_is_known_deceased must still exist and be callable."""
        from payer_intel.crew import _is_known_deceased
        assert callable(_is_known_deceased)

    def test_dedup_personas_still_present(self):
        """_deduplicate_personas must still exist and be callable."""
        from payer_intel.crew import _deduplicate_personas
        assert callable(_deduplicate_personas)

    def test_persona_priority_order_unchanged(self):
        """_PERSONA_PRIORITY order must be CEO > Chief Medical > CIO > CMO > VP Experience."""
        from payer_intel.crew import _PERSONA_PRIORITY
        assert _PERSONA_PRIORITY == (
            ExecutiveRole.CEO,
            ExecutiveRole.CHIEF_MEDICAL,
            ExecutiveRole.CIO,
            ExecutiveRole.CMO,
            ExecutiveRole.VP_EXPERIENCE,
        )

    def test_linkedin_url_gate_still_rejects_non_linkedin(self):
        """v3.5: LinkedIn URL gate must still reject YouTube and other non-LinkedIn URLs."""
        from payer_intel.crew import _validate_linkedin_url
        assert _validate_linkedin_url("https://www.youtube.com/watch?v=lGo2AxM3okc") is None
        assert _validate_linkedin_url("https://www.linkedin.com/in/jane-doe/") is not None

    def test_education_firm_filter_still_rejects_universities(self):
        """v3.5: Education firm filter must still reject universities from past_jobs."""
        from payer_intel.crew import _is_education_firm
        assert _is_education_firm("University of Rochester") is True
        assert _is_education_firm("Anthem") is False

    def test_universal_reject_still_blocks_coo_in_cio_slot(self):
        """v3.5: COO title must still be rejected for the CIO persona slot."""
        assert _title_passes_persona_filter(
            ExecutiveRole.CIO, "Chief Operating Officer"
        ) is False

    def test_universal_reject_still_blocks_ceo_in_cmo_slot(self):
        """v3.5: CEO/President title must still be rejected for the CMO persona slot."""
        assert _title_passes_persona_filter(
            ExecutiveRole.CMO, "President and CEO"
        ) is False

    def test_chief_medical_still_beats_chief_marketing_in_pattern_order(self):
        """v3.3: Chief Medical Officer must still resolve to CHIEF_MEDICAL, not CMO."""
        assert _title_to_role("Chief Medical Officer") == ExecutiveRole.CHIEF_MEDICAL
        assert _title_to_role("Chief Marketing Officer") == ExecutiveRole.CMO

    def test_15_column_schema_unchanged(self):
        """The 15-column EXECUTIVE_EXCEL_COLUMNS schema must not be altered."""
        from payer_intel.schema import EXECUTIVE_EXCEL_COLUMNS
        assert len(EXECUTIVE_EXCEL_COLUMNS) == 15
        assert EXECUTIVE_EXCEL_COLUMNS[0] == "Payer Name"
        assert EXECUTIVE_EXCEL_COLUMNS[2] == "Persona"
        assert EXECUTIVE_EXCEL_COLUMNS[3] == "Executive Name"
        assert EXECUTIVE_EXCEL_COLUMNS[-1] == "BD Notes"
