"""Smoke test for the executive pipeline with mocked search + LLM.

Verifies the run_executive() orchestration produces a valid Excel file
without making any network calls.
"""
from __future__ import annotations

import csv
from pathlib import Path
from unittest.mock import patch

from openpyxl import load_workbook

from payer_intel.schema import EXECUTIVE_EXCEL_COLUMNS


# Mock SearchApi responses keyed by query substring → list of results.
def _fake_google(query: str, num: int = 10, **kwargs):
    q = query.lower()
    if "site:linkedin.com" in q and "chief information officer" in q:
        return [{
            "title": "Jane Doe - Chief Information Officer at Humana",
            "link": "https://www.linkedin.com/in/jane-doe-humana/",
            "snippet": "Chief Information Officer at Humana · 2022 - Present. Previously VP Technology at Anthem.",
            "date": "2026-01-15",
        }]
    if "site:linkedin.com" in q and "chief executive officer" in q:
        return [{
            "title": "Bruce Broussard - CEO at Humana",
            "link": "https://www.linkedin.com/in/bruce-broussard/",
            "snippet": "President & CEO at Humana · 2013 - Present",
            "date": "2026-02-01",
        }]
    if "leadership" in q or "executive team" in q:
        return [{
            "title": "Humana Leadership",
            "link": "https://humana.com/about/leadership",
            "snippet": "Bruce Broussard, President & CEO. Jane Doe, Chief Information Officer.",
            "date": "2026-05-01",
        }]
    return []


def _fake_google_news(query: str, time_range: str = "qdr:y", num: int = 10, **kwargs):
    if "humana" in query.lower():
        return [{
            "title": "Humana appoints Jane Doe as Chief Information Officer",
            "link": "https://businesswire.com/humana-cio-jane-doe",
            "snippet": "Humana today announced the appointment of Jane Doe as CIO, effective immediately.",
            "date": "2026-04-10",
        }]
    return []


def _fake_classify(payer, evidence):
    """Stand-in for _classify_executives_with_llm — returns plausible output."""
    from payer_intel.schema import ExecutiveRole
    if not evidence:
        return {}, "", ""
    return (
        {
            ExecutiveRole.CEO: {
                "name": "Bruce Broussard",
                "title": "President & CEO",
                "linkedin_url": "https://www.linkedin.com/in/bruce-broussard/",
                "past_jobs": [],
                "departure_risk": False,
                "departure_note": "",
                "evidence_indices": [
                    i for i, e in enumerate(evidence)
                    if "broussard" in (e.url or "").lower()
                       or "broussard" in (e.snippet or "").lower()
                ],
            },
            ExecutiveRole.CIO: {
                "name": "Jane Doe",
                "title": "Chief Information Officer",
                "linkedin_url": "https://www.linkedin.com/in/jane-doe-humana/",
                "past_jobs": [
                    {"firm": "Anthem", "title": "VP Technology", "years": "2022-2024"},
                ],
                "departure_risk": False,
                "departure_note": "",
                "evidence_indices": [
                    i for i, e in enumerate(evidence)
                    if "jane-doe" in (e.url or "").lower()
                       or "jane doe" in (e.snippet or "").lower()
                ],
            },
        },
        "CIO Jane Doe joined recently from Anthem — warm-intro opportunity.",
        "Bruce Broussard remains CEO; Jane Doe confirmed as new CIO via April 2026 press release.",
    )


def test_executive_smoke_run(tmp_path: Path, monkeypatch):
    # Build a tiny 1-payer seed so we don't depend on data/ files.
    seed = tmp_path / "seed.csv"
    with open(seed, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["payer_name", "domain", "payer_type", "search_aliases", "search_excludes"])
        w.writerow(["Humana Inc.", "humana.com", "National", "Humana", ""])

    # Provide a fake API key so SearchApiClient can be instantiated.
    monkeypatch.setenv("SEARCHAPI_API_KEY", "test-key")

    out_dir = tmp_path / "out"

    with patch("payer_intel.crew.SearchApiClient") as mock_client_cls, \
         patch("payer_intel.crew._classify_executives_with_llm", side_effect=_fake_classify), \
         patch("payer_intel.crew._enrich_executive_pages", side_effect=lambda evs, dom: evs):
        client = mock_client_cls.return_value
        client.google.side_effect = _fake_google
        client.google_news.side_effect = _fake_google_news

        from payer_intel.crew import run_executive
        out = run_executive(seed, out_dir)

    assert out.exists()
    wb = load_workbook(out)
    assert "Executive Intelligence" in wb.sheetnames
    ws = wb["Executive Intelligence"]
    assert [c.value for c in ws[1]] == EXECUTIVE_EXCEL_COLUMNS
    # Header + 1 payer row
    assert ws.max_row == 2
    header = [c.value for c in ws[1]]
    ceo_col = header.index("CEO Name") + 1
    cio_col = header.index("CIO/CTO Name") + 1
    cmo_col = header.index("CMO/Growth Name") + 1
    assert ws.cell(row=2, column=ceo_col).value == "Bruce Broussard"
    assert ws.cell(row=2, column=cio_col).value == "Jane Doe"
    # Unidentified role → placeholder
    assert ws.cell(row=2, column=cmo_col).value == "\u2014"
