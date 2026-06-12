from pathlib import Path

from payer_intel.crew import build_excludes_set, build_name_clause, load_seed


def test_alias_name_clause_single():
    assert build_name_clause("UnitedHealthcare", "") == '"UnitedHealthcare"'
    assert build_name_clause("UnitedHealthcare", None) == '"UnitedHealthcare"'


def test_alias_name_clause_multi():
    clause = build_name_clause(
        "UnitedHealthcare", "UnitedHealth Group|UHG|Optum"
    )
    assert clause == '("UnitedHealthcare" OR "UnitedHealth Group" OR "UHG" OR "Optum")'


def test_alias_name_clause_dedup_case_insensitive():
    clause = build_name_clause("UnitedHealthcare", "unitedhealthcare|UHG")
    assert clause == '("UnitedHealthcare" OR "UHG")'


def test_seed_csv_has_aliases_column():
    rows = load_seed(Path("data/seed_payers.csv"))
    for r in rows:
        assert "search_aliases" in r
    uhc = next(r for r in rows if r["payer_name"] == "UnitedHealthcare")
    assert "UnitedHealth Group" in uhc["search_aliases"]


def test_seed_csv_has_excludes_column():
    """v6: every seed row exposes the search_excludes column (may be empty)."""
    rows = load_seed(Path("data/seed_payers_62.csv"))
    for r in rows:
        assert "search_excludes" in r


def test_ibx_excludes_amerihealth_siblings():
    """Aarete MS-05: Independence Blue Cross must list AmeriHealth siblings
    as excludes, not aliases."""
    rows = load_seed(Path("data/seed_payers_62.csv"))
    ibx = next(r for r in rows if r["payer_name"] == "Independence Blue Cross")
    assert "AmeriHealth Caritas" in ibx["search_excludes"]
    # And NOT in aliases (the bug we just fixed).
    assert "AmeriHealth Caritas" not in ibx["search_aliases"]
    # The bare "AmeriHealth" alias was also removed.
    aliases_parts = {a.strip().lower() for a in ibx["search_aliases"].split("|")}
    assert "amerihealth" not in aliases_parts


def test_build_excludes_set_from_loaded_row():
    rows = load_seed(Path("data/seed_payers_62.csv"))
    ibx = next(r for r in rows if r["payer_name"] == "Independence Blue Cross")
    excludes = build_excludes_set(ibx)
    assert "amerihealth caritas" in excludes

