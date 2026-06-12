# AArete Executive Intelligence Engine — Complete v2 Copilot Instruction

**Paste this entire prompt into Copilot. It covers every file that needs to change.**

---

## Context

This is the `amiiiirsaman/Payer-BD-2` repo. The executive pipeline runs with `python main.py --mode executive`. The first 10-payer run revealed three categories of problems:

1. **Cross-payer contamination** — the LLM assigned Sushma Akunuru (CIO of Independence Blue Cross) to Aetna because the payer-match check was too loose.
2. **Shallow past-job data** — the engine extracts `past_firms: ["Anthem"]` (company name only). We need structured `past_jobs: [{firm, title, years}]` for the top 2 prior roles per executive, to enable warm BD introductions.
3. **Missing data sources and stale data** — the engine missed that Blue KC CEO Erin Stucky announced her retirement. It also does not search Becker's Payer Issues, Modern Healthcare, or AHIP conference pages, which are the highest-signal sources for health plan executive movements.

Apply all of the following changes across **6 files**: `schema.py`, `crew.py`, `agents.py`, `crew_tools.py`, `qc_exec.py`, and `export.py`. Then update the 3 test files.

---

## FILE 1: `src/payer_intel/schema.py`

### Change 1a — Add `PastJob` model (insert before `ExecutiveProfile` class)
```python
class PastJob(BaseModel):
    """One prior role for a BD warm-intro trail."""
    firm: str
    title: str
    years: str  # e.g. "2018–2022" or "~3 years"
```

### Change 1b — Update `ExecutiveProfile`
Replace:
```python
    past_firms: List[str] = Field(default_factory=list)
```
With:
```python
    past_jobs: List[PastJob] = Field(default_factory=list)   # top 2 most recent prior roles
    departure_risk: bool = False
    departure_note: Optional[str] = None
```

### Change 1c — Update `aggregated_past_firms` property on `ExecutivePayerRecord`
Replace the inner loop body:
```python
            for firm in prof.past_firms:
                key = firm.strip().lower()
                if not key or key in seen:
                    continue
                seen.add(key)
                out.append(firm.strip())
```
With:
```python
            for job in prof.past_jobs:
                key = job.firm.strip().lower()
                if not key or key in seen:
                    continue
                seen.add(key)
                out.append(job.firm.strip())
```

### Change 1d — Replace `EXECUTIVE_EXCEL_COLUMNS` and `EXECUTIVE_ROLE_COLUMNS`

Delete the existing `EXECUTIVE_EXCEL_COLUMNS: list[str] = [...]` block and the existing `EXECUTIVE_ROLE_COLUMNS: list[tuple[...]] = [...]` block. Replace with:

```python
# Per-persona column groups (3 identity + 6 past-job = 9 cols each × 5 personas = 45)
EXECUTIVE_ROLE_COLUMNS: dict[ExecutiveRole, list[str]] = {
    ExecutiveRole.CEO: [
        "CEO Name", "CEO Title", "CEO LinkedIn",
        "CEO Past Job 1 Firm", "CEO Past Job 1 Title", "CEO Past Job 1 Years",
        "CEO Past Job 2 Firm", "CEO Past Job 2 Title", "CEO Past Job 2 Years",
    ],
    ExecutiveRole.CIO: [
        "CIO/CTO Name", "CIO/CTO Title", "CIO/CTO LinkedIn",
        "CIO Past Job 1 Firm", "CIO Past Job 1 Title", "CIO Past Job 1 Years",
        "CIO Past Job 2 Firm", "CIO Past Job 2 Title", "CIO Past Job 2 Years",
    ],
    ExecutiveRole.CMO: [
        "CMO/Growth Name", "CMO/Growth Title", "CMO/Growth LinkedIn",
        "CMO Past Job 1 Firm", "CMO Past Job 1 Title", "CMO Past Job 1 Years",
        "CMO Past Job 2 Firm", "CMO Past Job 2 Title", "CMO Past Job 2 Years",
    ],
    ExecutiveRole.CHIEF_MEDICAL: [
        "Chief Medical Name", "Chief Medical Title", "Chief Medical LinkedIn",
        "Chief Med Past Job 1 Firm", "Chief Med Past Job 1 Title", "Chief Med Past Job 1 Years",
        "Chief Med Past Job 2 Firm", "Chief Med Past Job 2 Title", "Chief Med Past Job 2 Years",
    ],
    ExecutiveRole.VP_EXPERIENCE: [
        "VP Experience Name", "VP Experience Title", "VP Experience LinkedIn",
        "VP Exp Past Job 1 Firm", "VP Exp Past Job 1 Title", "VP Exp Past Job 1 Years",
        "VP Exp Past Job 2 Firm", "VP Exp Past Job 2 Title", "VP Exp Past Job 2 Years",
    ],
}

# Build the flat 50-column list: 2 identity + 45 persona + 3 metadata
EXECUTIVE_EXCEL_COLUMNS: list[str] = ["Payer Name", "Payer Type"]
for _role in ExecutiveRole:
    EXECUTIVE_EXCEL_COLUMNS.extend(EXECUTIVE_ROLE_COLUMNS[_role])
EXECUTIVE_EXCEL_COLUMNS.extend(["Date Verified", "Confidence Score", "BD Notes"])
```

---

## FILE 2: `src/payer_intel/crew.py`

### Change 2a — Add `beckerspayer.com` and `ahip.org` to `_FETCH_DOMAINS` (around line 326)
In the `_FETCH_DOMAINS` frozenset, add these two entries in the "Trade press" section:
```python
    "beckerspayer.com",
    "ahip.org",
```

### Change 2b — Expand `_APPOINTMENT_TERMS` (around line 1086)
Replace:
```python
    '"appointed" OR "named" OR "joins as" OR "promoted to" '
    'OR "elected" OR "announces" OR "new Chief"'
```
With:
```python
    '"appointed" OR "named" OR "joins as" OR "promoted to" '
    'OR "elected" OR "announces" OR "new Chief" '
    'OR "retire" OR "retirement" OR "successor" OR "departure" OR "steps down"'
```

### Change 2c — Add Becker's Payer + AHIP search call to `gather_executive_evidence` (after the existing third-party call, around line 1185)
Add a new search block after the third-party block:
```python
    # ── Healthcare trade press: Becker's Payer + AHIP (1 call) ────────────
    trade_query = (
        f"(site:beckerspayer.com OR site:ahip.org OR site:modernhealthcare.com) "
        f"{name_clause} (\"Chief\" OR \"President\" OR \"VP\" OR \"appointed\" OR \"named\")"
    )
    for r in _safe_search(client.google, trade_query, num=10):
        evidence.append(
            Evidence(
                source_type="executive_news",
                url=r.get("link", "") or "",
                snippet=(r.get("snippet") or "")[:1500],
                date=r.get("date"),
            )
        )
```

### Change 2d — Strengthen the LLM prompt in `_classify_executives_with_llm` (around line 1395)

In the `description` f-string, replace the existing `Rules:` block with the following expanded version:

```
Rules:
- CRITICAL PAYER-MATCH RULE: You MUST verify that the executive CURRENTLY works at
  **{payer_name}** (or one of its known aliases). If the payer name does not appear
  in the CURRENT role section of the evidence — i.e. the LinkedIn snippet shows
  "Present" at a DIFFERENT organization, or the press release names a different
  health plan — you MUST omit that executive entirely. Do NOT assign an executive
  from Independence Blue Cross to Aetna, or from Humana to UnitedHealth, etc.
  When in doubt, omit rather than guess.
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
- If two candidates both claim the same role, pick the one with (a) the most
  recent corroborating press release, OR (b) the LinkedIn "Present" tenure.
- Disambiguate CMO carefully: "Chief Marketing Officer" → CMO persona;
  "Chief Medical Officer" → Chief Medical persona. Never put a marketing
  executive in Chief Medical or vice versa.
- VP Member Experience covers: Chief Experience Officer, Chief Patient
  Engagement Officer, Chief Member/Customer Experience Officer, VP Member
  Experience. Do not put a generic Chief Operating Officer here.
- For each chosen executive, extract the 2 most recent prior roles (NOT the
  current role at {payer_name}) from the evidence. Format as a list of objects:
  [{{"firm": "Anthem", "title": "VP Technology", "years": "2018-2022"}}, ...].
  If years are not mentioned, use an empty string for "years". Omit "past_jobs"
  or use an empty list if no prior roles are found.
- DEPARTURE RISK: If any evidence mentions that the executive is retiring,
  stepping down, has announced a departure, or that a successor has been named,
  set "departure_risk": true and provide a short "departure_note" (e.g.
  "Announced retirement by end of 2026; successor Jenny Housley named President
  Apr 2026"). Otherwise set "departure_risk": false.
- The `linkedin_url` must be a real linkedin.com/in/ or linkedin.com/pub/ URL
  taken VERBATIM from one of the evidence items — do not fabricate URLs.
```

### Change 2e — Update the expected JSON output block in the same prompt (around line 1441)
Replace the old `"past_firms"` shape with:
```json
{
  "executives": {
    "CEO": {
      "name": "...",
      "title": "...",
      "linkedin_url": "https://www.linkedin.com/in/...",
      "past_jobs": [
        {"firm": "...", "title": "...", "years": "..."},
        {"firm": "...", "title": "...", "years": "..."}
      ],
      "departure_risk": false,
      "departure_note": "",
      "evidence_indices": [0, 3]
    },
    "CIO": { "..." }
  },
  "bd_notes": "1-2 sentence strategic note",
  "key_evidence_summary": "2-3 sentence narrative of the strongest evidence"
}
```

Also update the `Task` `expected_output` string (around line 1465) to reference `past_jobs` and `departure_risk` instead of `past_firms`.

### Change 2f — Update the parsing block (around line 1514)
Replace:
```python
        past_firms_raw = profile.get("past_firms") or []
        past_firms = [
            str(f).strip() for f in past_firms_raw if str(f).strip()
        ][:3]
```
With:
```python
        past_jobs_raw = profile.get("past_jobs") or []
        past_jobs = []
        for job in past_jobs_raw[:2]:
            if isinstance(job, dict) and job.get("firm", "").strip():
                past_jobs.append({
                    "firm": str(job.get("firm", "")).strip(),
                    "title": str(job.get("title", "")).strip(),
                    "years": str(job.get("years", "")).strip(),
                })
        departure_risk = bool(profile.get("departure_risk", False))
        departure_note = str(profile.get("departure_note") or "").strip() or None
```

### Change 2g — Update the `out[role]` dict (around line 1522)
Replace:
```python
        out[role] = {
            "name": name,
            "title": (profile.get("title") or "").strip() or None,
            "linkedin_url": linkedin_url,
            "past_firms": past_firms,
            "evidence_indices": evidence_indices,
        }
```
With:
```python
        out[role] = {
            "name": name,
            "title": (profile.get("title") or "").strip() or None,
            "linkedin_url": linkedin_url,
            "past_jobs": past_jobs,
            "departure_risk": departure_risk,
            "departure_note": departure_note,
            "evidence_indices": evidence_indices,
        }
```

### Change 2h — Update `assemble_executive_record` (around line 1559)
Replace:
```python
        profile = ExecutiveProfile(
            name=info.get("name"),
            title=info.get("title"),
            linkedin_url=info.get("linkedin_url"),
            past_firms=info.get("past_firms", []),
            confidence=qc.confidence,
            confidence_note=qc.note,
            evidence=evs,
        )
```
With:
```python
        raw_jobs = info.get("past_jobs", [])
        profile = ExecutiveProfile(
            name=info.get("name"),
            title=info.get("title"),
            linkedin_url=info.get("linkedin_url"),
            past_jobs=[PastJob(**j) for j in raw_jobs if isinstance(j, dict)],
            departure_risk=info.get("departure_risk", False),
            departure_note=info.get("departure_note"),
            confidence=qc.confidence,
            confidence_note=qc.note,
            evidence=evs,
        )
```
Also add `from .schema import PastJob` to the imports at the top of `crew.py` if it is not already there.

### Change 2i — Update `_default_exec_bd_notes` (around line 1590)
Replace the return statement with:
```python
    departing = [r for r, p in rec.executives.items() if p.name and p.departure_risk]
    notes = (
        f"{len(identified)}/5 executive roles identified ({rec.confidence.value} confidence). "
        "Validate via direct outreach before referencing in BD pitch."
    )
    if departing:
        roles_str = ", ".join(r.value for r in departing)
        notes = f"[DEPARTURE RISK/RETIRING — {roles_str}] " + notes
    # Check for stale evidence (all evidence dates older than 365 days)
    all_evidence_dates = [
        e.date for p in rec.executives.values() if p.name
        for e in p.evidence if e.date
    ]
    if all_evidence_dates and not any(
        _within_days(d, 365) for d in all_evidence_dates
    ):
        notes += " [Verify — all evidence >12 months old]"
    return notes
```
Also import `_within_days` from `qc_exec` at the top of the function or inline it — it already exists in `qc_exec.py`.

---

## FILE 3: `src/payer_intel/agents.py`

### Change 3a — Update `executive_linkedin_agent` goal and backstory
Replace:
```python
        goal=(
            "Find LinkedIn profiles for the 5 BD personas (CEO, CIO/CTO, CMO/Growth, "
            "Chief Medical, VP Member Experience) at the target payer, capturing name, "
            "current title, LinkedIn URL, and 1-2 most recent prior firms."
        ),
        backstory=(
            "You specialize in mining LinkedIn snippet results for executive identity "
            "and tenure signals. You ignore former employees and resolve title aliases."
        ),
```
With:
```python
        goal=(
            "Find LinkedIn profiles for the 5 BD personas (CEO, CIO/CTO, CMO/Growth, "
            "Chief Medical, VP Member Experience) at the target payer. For each executive, "
            "capture: name, current title, LinkedIn URL, and the 2 most recent prior roles "
            "(firm, title, years) for BD warm-intro mapping. ONLY include executives who "
            "currently work at the target payer — never assign a profile from a different "
            "health plan."
        ),
        backstory=(
            "You specialize in mining LinkedIn snippet results for executive identity, "
            "tenure signals, and career history. You strictly enforce payer-name matching "
            "and never confuse executives from one health plan with another."
        ),
```

### Change 3b — Update `executive_news_agent` goal and backstory
Replace:
```python
        goal=(
            "Find press releases announcing executive appointments and official "
            "leadership / executive-team pages on the payer's own domain."
        ),
        backstory=(
            "You scan business wire services and payer newsrooms for 'appointed', "
            "'named', and 'joins as' announcements that confirm current C-suite roles."
        ),
```
With:
```python
        goal=(
            "Find press releases announcing executive appointments AND departures at the "
            "target payer. Search official leadership pages, wire services, Becker's Payer "
            "Issues, Modern Healthcare, and AHIP conference speaker lists. Flag any executive "
            "who has announced retirement, a planned departure, or whose successor has been named."
        ),
        backstory=(
            "You scan business wire services, payer newsrooms, and healthcare trade press "
            "(Becker's Payer, Modern Healthcare, AHIP) for 'appointed', 'named', 'joins as', "
            "'retire', 'steps down', and 'successor' signals to confirm current C-suite roles "
            "and surface departure risks before they become stale data."
        ),
```

### Change 3c — Update `executive_third_party_agent` goal and backstory
Replace:
```python
        goal=(
            "Triangulate executive tenure and past firms via third-party directories "
            "(ZoomInfo, RocketReach, Becker's Hospital Review)."
        ),
        backstory=(
            "You corroborate LinkedIn snippets with independent third-party sources "
            "so confidence can be elevated to High when triangulated."
        ),
```
With:
```python
        goal=(
            "Triangulate executive tenure, past roles (firm + title + years), and departure "
            "risk via third-party directories: ZoomInfo, RocketReach, Becker's Payer Issues, "
            "Modern Healthcare, and AHIP conference speaker pages."
        ),
        backstory=(
            "You corroborate LinkedIn snippets with independent third-party sources to elevate "
            "confidence to High when triangulated, and to surface structured career history "
            "(past firm, title, years) for BD warm-intro mapping."
        ),
```

### Change 3d — Update `executive_classifier_agent` goal
Replace:
```python
        goal=(
            "From the gathered evidence, identify the single current holder of each of "
            "the 5 BD personas. Resolve name collisions by preferring 'Present' tenure "
            "on LinkedIn or the most recent press release. Extract 1-2 past firms per exec."
        ),
```
With:
```python
        goal=(
            "From the gathered evidence, identify the single current holder of each of "
            "the 5 BD personas at the target payer. CRITICAL: reject any executive whose "
            "current employer does not match the target payer. Resolve name collisions by "
            "preferring 'Present' LinkedIn tenure or the most recent press release. "
            "Extract the 2 most recent prior roles (firm, title, years) per exec. "
            "Flag departure_risk=true if any executive is retiring or has a named successor."
        ),
```

---

## FILE 4: `src/payer_intel/crew_tools.py`

### Change 4a — Update `ExecThirdPartyDirectoryTool` to include richer sources
Replace:
```python
    description: str = (
        "Cross-reference an executive against third-party directories (ZoomInfo, "
        "RocketReach, Becker's Hospital Review) for tenure and past-firm validation. "
        "Input: '\"<payer>\" \"<title or name>\"'. Returns JSON list."
    )
```
And:
```python
        scoped = (
            f"(site:rocketreach.co OR site:zoominfo.com OR site:beckershospitalreview.com) {query}"
        )
```
With:
```python
    description: str = (
        "Cross-reference an executive against third-party directories and healthcare trade "
        "press for tenure, past roles (firm/title/years), and departure risk. Sources: "
        "ZoomInfo, RocketReach, Becker's Payer Issues, Modern Healthcare, AHIP conference "
        "speaker lists. Input: '\"<payer>\" \"<title or name>\"'. Returns JSON list."
    )
```
And:
```python
        scoped = (
            f"(site:rocketreach.co OR site:zoominfo.com OR site:beckershospitalreview.com "
            f"OR site:beckerspayer.com OR site:modernhealthcare.com OR site:ahip.org) {query}"
        )
```

---

## FILE 5: `src/payer_intel/qc_exec.py`

### Change 5a — Add `beckerspayer.com` to `_THIRD_PARTY_HOSTS`
Replace:
```python
_THIRD_PARTY_HOSTS = ("rocketreach.co", "zoominfo.com", "beckershospitalreview.com")
```
With:
```python
_THIRD_PARTY_HOSTS = (
    "rocketreach.co", "zoominfo.com", "beckershospitalreview.com",
    "beckerspayer.com", "modernhealthcare.com", "ahip.org",
)
```

---

## FILE 6: `src/payer_intel/export.py`

### Change 6a — Rewrite `_exec_record_to_row`
Replace the entire `_exec_record_to_row` function with:
```python
def _exec_record_to_row(rec: ExecutivePayerRecord) -> dict[str, str]:
    row: dict[str, str] = {
        "Payer Name": rec.payer_name,
        "Payer Type": rec.payer_type,
        "Date Verified": rec.date_verified,
        "Confidence Score": rec.confidence.value,
        "BD Notes": rec.bd_notes,
    }
    for role in ExecutiveRole:
        cols = EXECUTIVE_ROLE_COLUMNS[role]
        profile = rec.executives.get(role)
        if not profile or not profile.name:
            for c in cols:
                row[c] = ""
            continue
        # Identity columns (index 0, 1, 2)
        row[cols[0]] = profile.name or ""
        row[cols[1]] = profile.title or ""
        row[cols[2]] = profile.linkedin_url or ""
        # Past job columns (index 3-8, two jobs × 3 fields)
        for i in range(2):
            firm_col, title_col, years_col = cols[3 + i * 3], cols[4 + i * 3], cols[5 + i * 3]
            if i < len(profile.past_jobs):
                job = profile.past_jobs[i]
                row[firm_col] = job.firm
                row[title_col] = job.title
                row[years_col] = job.years
            else:
                row[firm_col] = row[title_col] = row[years_col] = ""
    return row
```

### Change 6b — Update `write_excel_executive` column-width dict and index lookups
Replace the `widths` dict with:
```python
    widths: dict[str, int] = {
        "Payer Name": 26, "Payer Type": 14,
        "Date Verified": 14, "Confidence Score": 16, "BD Notes": 55,
    }
    for role in ExecutiveRole:
        cols = EXECUTIVE_ROLE_COLUMNS[role]
        widths[cols[0]] = 22   # Name
        widths[cols[1]] = 28   # Title
        widths[cols[2]] = 36   # LinkedIn
        for i in range(2):
            widths[cols[3 + i * 3]] = 22   # Past Firm
            widths[cols[4 + i * 3]] = 26   # Past Title
            widths[cols[5 + i * 3]] = 14   # Past Years
```

Replace the `linkedin_col_indices` and `name_col_indices` lookups with:
```python
    linkedin_col_indices = {
        EXECUTIVE_ROLE_COLUMNS[role][2]: EXECUTIVE_EXCEL_COLUMNS.index(EXECUTIVE_ROLE_COLUMNS[role][2]) + 1
        for role in ExecutiveRole
    }
    name_col_indices = {
        EXECUTIVE_ROLE_COLUMNS[role][0]: EXECUTIVE_EXCEL_COLUMNS.index(EXECUTIVE_ROLE_COLUMNS[role][0]) + 1
        for role in ExecutiveRole
    }
```

### Change 6c — Update "Past Firms Index" sheet (around line 300)
Replace the sheet header and row-building loop:
```python
    firms_sheet.append([
        "Past Firm", "Past Title", "Past Years",
        "Executive Name", "Current Role", "Current Payer", "LinkedIn",
    ])
    ...
    for rec in records_list:
        for role in ExecutiveRole:
            profile = rec.executives.get(role)
            if not (profile and profile.name and profile.past_jobs):
                continue
            for job in profile.past_jobs:
                firm_rows.append((
                    job.firm,
                    job.title,
                    job.years,
                    profile.name,
                    role.value,
                    rec.payer_name,
                    profile.linkedin_url or "",
                ))
```
Update the hyperlink column index from 5 to 7 (LinkedIn is now column 7 on this sheet).
Update column widths: A=26, B=28, C=14, D=24, E=16, F=28, G=36.

---

## TESTS — Update 3 files

### `tests/test_executive_schema.py`
- Change `assert len(EXECUTIVE_EXCEL_COLUMNS) == 16` → `== 50`
- Remove assertions for `EXECUTIVE_EXCEL_COLUMNS[12] == "Past Firms"` and `EXECUTIVE_EXCEL_COLUMNS[14] == "Confidence Score"`
- Add: `assert EXECUTIVE_EXCEL_COLUMNS[-1] == "BD Notes"` and `assert EXECUTIVE_EXCEL_COLUMNS[-2] == "Confidence Score"`
- In `test_executive_profile_defaults`, change `assert p.past_firms == []` → `assert p.past_jobs == []` and add `assert p.departure_risk == False`
- In `test_aggregated_past_firms_dedupes_case_insensitively`, replace `past_firms=[...]` with `past_jobs=[PastJob(firm="Anthem", title="VP", years=""), PastJob(firm="UnitedHealth Group", title="Director", years="")]` etc.

### `tests/test_export_executive.py`
- In `_sample_record()`, replace `past_firms=["Anthem", "UnitedHealth Group"]` with `past_jobs=[PastJob(firm="Anthem", title="VP Technology", years="2018-2022"), PastJob(firm="UnitedHealth Group", title="Director", years="2015-2018")]`
- Replace `past_firms=["Accenture"]` with `past_jobs=[PastJob(firm="Accenture", title="Manager", years="2012-2015")]`
- Change `test_export_has_16_column_header_in_spec_order` → assert `len(header) == 50`
- Remove `test_export_aggregates_past_firms_into_column_m` (that column no longer exists)
- Add a new test that asserts `ws.cell(row=2, column=header.index("CEO Past Job 1 Firm") + 1).value == "Anthem"`
- In `test_past_firms_index_lists_each_firm_executive_pair`, update header assertion to `["Past Firm", "Past Title", "Past Years", "Executive Name", "Current Role", "Current Payer", "LinkedIn"]`

### `tests/test_executive_smoke.py`
- In `_fake_classify`, replace `"past_firms": ["Anthem"]` with `"past_jobs": [{"firm": "Anthem", "title": "VP Technology", "years": "2022-2024"}]` and add `"departure_risk": False, "departure_note": ""`
- Update the `header` assertions to use the new column names (e.g. `"CEO Name"` still exists, `"CIO/CTO Name"` still exists — these are unchanged)

---

## After all edits, run:
```bash
cd /home/ubuntu/payer-bd-2
python -m pytest tests/test_executive_schema.py tests/test_export_executive.py tests/test_executive_smoke.py -v
python main.py --mode executive --seed data/seed_payers_10random.csv --out out/exec10_v2
```
Then inspect `out/exec10_v2/Aarete_BD_Executive_Intelligence_*.xlsx` and confirm:
- 50 columns in the "Executive Intelligence" sheet
- Past Job 1 / Past Job 2 populated for executives where LinkedIn snippets included career history
- `[DEPARTURE RISK/RETIRING]` prefix in BD Notes for any payer where a departure was detected
- No cross-payer contamination (each executive's current employer must match the payer row)

Then commit:
```bash
git add -A && git commit -m "Engine v2: cross-payer fix, structured past_jobs, departure risk, richer sources (Becker's Payer, AHIP, Modern Healthcare)" && git push
```
