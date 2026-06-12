"""Schema sanity tests for the executive intelligence pipeline."""
from payer_intel.schema import (
    EXECUTIVE_EXCEL_COLUMNS,
    EXECUTIVE_TITLE_MAP,
    ConfidenceScore,
    ExecutivePayerRecord,
    ExecutiveProfile,
    ExecutiveRole,
    PastJob,
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


def test_excel_columns_have_15_in_spec_order():
    assert len(EXECUTIVE_EXCEL_COLUMNS) == 15
    assert EXECUTIVE_EXCEL_COLUMNS[0] == "Payer Name"
    assert EXECUTIVE_EXCEL_COLUMNS[1] == "Payer Type"
    assert EXECUTIVE_EXCEL_COLUMNS[2] == "Persona"
    assert EXECUTIVE_EXCEL_COLUMNS[3] == "Executive Name"
    assert EXECUTIVE_EXCEL_COLUMNS[5] == "LinkedIn"
    assert EXECUTIVE_EXCEL_COLUMNS[-1] == "BD Notes"
    assert EXECUTIVE_EXCEL_COLUMNS[-2] == "Confidence Score"
    assert EXECUTIVE_EXCEL_COLUMNS[-3] == "Date Verified"
    assert "Past Job 1 Firm" in EXECUTIVE_EXCEL_COLUMNS
    assert "Past Job 2 Years" in EXECUTIVE_EXCEL_COLUMNS


def test_executive_profile_defaults():
    p = ExecutiveProfile()
    assert p.name is None
    assert p.past_jobs == []
    assert p.departure_risk is False
    assert p.departure_note is None
    assert p.confidence == ConfidenceScore.LOW


def test_aggregated_past_firms_dedupes_case_insensitively():
    rec = ExecutivePayerRecord(payer_name="Humana Inc.")
    rec.executives[ExecutiveRole.CEO] = ExecutiveProfile(
        name="Jane Doe",
        past_jobs=[
            PastJob(firm="Anthem", title="VP", years="2015-2018"),
            PastJob(firm="UnitedHealth Group", title="Director", years="2012-2015"),
        ],
    )
    rec.executives[ExecutiveRole.CIO] = ExecutiveProfile(
        name="John Smith",
        past_jobs=[
            PastJob(firm="anthem", title="Engineer", years=""),  # case-insensitive dupe
            PastJob(firm="Accenture", title="Manager", years=""),
        ],
    )
    out = rec.aggregated_past_firms
    assert len(out) == 3
    assert out[0] == "Anthem"
    assert "UnitedHealth Group" in out
    assert "Accenture" in out


def test_aggregated_past_firms_empty_when_no_execs():
    rec = ExecutivePayerRecord(payer_name="Test")
    assert rec.aggregated_past_firms == []
