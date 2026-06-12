"""Schema sanity tests for the executive intelligence pipeline."""
from payer_intel.schema import (
    EXECUTIVE_EXCEL_COLUMNS,
    EXECUTIVE_ROLE_COLUMNS,
    EXECUTIVE_TITLE_MAP,
    ConfidenceScore,
    ExecutivePayerRecord,
    ExecutiveProfile,
    ExecutiveRole,
)


def test_executive_role_enum_has_five_personas():
    assert len(ExecutiveRole) == 5
    assert {r.name for r in ExecutiveRole} == {
        "CEO", "CIO", "CMO", "CHIEF_MEDICAL", "VP_EXPERIENCE",
    }


def test_title_map_covers_every_role():
    assert set(EXECUTIVE_TITLE_MAP.keys()) == set(ExecutiveRole)
    for role, titles in EXECUTIVE_TITLE_MAP.items():
        assert titles, f"{role} has no titles"
        assert all(isinstance(t, str) and t.strip() for t in titles)


def test_excel_columns_have_16_in_spec_order():
    assert len(EXECUTIVE_EXCEL_COLUMNS) == 16
    assert EXECUTIVE_EXCEL_COLUMNS[0] == "Payer Name"
    assert EXECUTIVE_EXCEL_COLUMNS[-1] == "BD Notes"
    assert EXECUTIVE_EXCEL_COLUMNS[14] == "Confidence Score"
    assert EXECUTIVE_EXCEL_COLUMNS[12] == "Past Firms"


def test_role_columns_map_to_excel_columns():
    cols = set(EXECUTIVE_EXCEL_COLUMNS)
    for _role, name_col, link_col in EXECUTIVE_ROLE_COLUMNS:
        assert name_col in cols
        assert link_col in cols


def test_executive_profile_defaults():
    p = ExecutiveProfile()
    assert p.name is None
    assert p.past_firms == []
    assert p.confidence == ConfidenceScore.LOW


def test_aggregated_past_firms_dedupes_case_insensitively():
    rec = ExecutivePayerRecord(payer_name="Humana Inc.")
    rec.executives[ExecutiveRole.CEO] = ExecutiveProfile(
        name="Jane Doe", past_firms=["Anthem", "UnitedHealth Group"]
    )
    rec.executives[ExecutiveRole.CIO] = ExecutiveProfile(
        name="John Smith", past_firms=["anthem", "Accenture"]  # case-insensitive dupe
    )
    out = rec.aggregated_past_firms
    assert len(out) == 3
    # Order preserved by first occurrence, casing of first occurrence
    assert out[0] == "Anthem"
    assert "UnitedHealth Group" in out
    assert "Accenture" in out


def test_aggregated_past_firms_empty_when_no_execs():
    rec = ExecutivePayerRecord(payer_name="Test")
    assert rec.aggregated_past_firms == []
