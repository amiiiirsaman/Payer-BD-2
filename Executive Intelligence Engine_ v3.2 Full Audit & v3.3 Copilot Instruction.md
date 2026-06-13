# Executive Intelligence Engine: v3.2 Full Audit & v3.3 Copilot Instruction

## v3.2 Full Output Assessment — All 10 Payers

### What is Working Well

The structural wins from v3.0 through v3.2 are all holding. The 5-row layout is clean and readable. Cross-payer contamination is eliminated. The deceased filter is working. The BD Notes column is consistently rich and useful. Here is the full payer-by-payer verdict:

| Payer | CEO | CIO | CMO | Chief Medical | VP Experience | Past Jobs | Issues |
|---|---|---|---|---|---|---|---|
| Alameda Alliance | Matthew Woodruff ✅ | — (empty) | — (empty) | Donna White Carey ✅ | — (empty) | Woodruff only | Small payer — CIO/CMO/VPX likely don't exist publicly |
| BCBS Kansas | Matt All ✅ | Matt Langdon ✅ | Mike Gerrish ✅ | Raelene Knolla ✅ | — (empty) | Gerrish: Wellmark + Payless ✅ | Strong output overall |
| Blue KC | Erin Stucky ✅ | — (empty) | — (empty) | Greg Sweat ⚠️ | — (empty) | Stucky only | CIO/CMO empty; BD Note correctly flags Stucky retirement + Housley succession |
| BCBSLA | Bryan Camerlinck ✅ | Tina Bourgeois ⚠️ | Brian Keller ⚠️ | — (empty) | Jay Balden ✅ | Camerlinck empty ⚠️ | Tina Bourgeois flagged as departed in BD Note but still in CIO slot; Keller is Growth Officer not CMO; Camerlinck past jobs regressed to empty |
| BCBST | JD Hickey ✅ | Jennifer Weaver ✅ | Henry Smith ⚠️ | Andrea Willis ✅ | — (empty) | All empty | Henry Smith is SVP Operations — not a true CMO |
| CalViva Health | Jeffrey Nkansah ✅ | — (empty) | — (empty) | Patrick Marabella ✅ | — (empty) | Nkansah only | Very small payer — expected empties |
| CareOregon | Eric C. Hunter ✅ | — (empty) | — (empty) | Amit Shah ✅ | — (empty) | Hunter: Cambia + CareOregon ✅ | CIO/CMO/VPX empty — consistent with prior runs |
| IBX | Kelly Munson ✅ | Sushma Akunuru ✅ | Koleen Cavanaugh ✅ | Dr. Rodrigo Cerdá ✅ | — (empty) | Munson: AmeriHealth + Aetna ✅ | Best output of all 10 payers |
| Medical Mutual OH | Tony Helton ✅ | Christopher Donovan ✅ | Andrea Hogben ⚠️ | Tere Koenig ✅ | Betsy Williamson ⚠️ | Helton: Medical Mutual + Cleveland Clinic ✅ | Hogben's past job is "The Plain Dealer" (a newspaper) — likely wrong; Williamson is a Quality VP not a CX/Experience VP |
| UnitedHealthcare | Tim Noel ✅ | Jennifer Zmuda ✅ | Dan Fine ✅ | Gerald Hautman ✅ | — (empty) | Noel: UHC Medicare + UHG ✅ | Hautman is "Chief Medical Officer, National Accounts" — a division CMO, not the enterprise CMO; confidence Low is appropriate |

### Remaining Issues to Fix in v3.3

**Issue 1 — Departed executive still in slot (BCBSLA CIO Tina Bourgeois)**
The BD Note correctly says "CIO Tina Bourgeois appears to have departed in March 2026" but the engine still puts her in the CIO slot. The departure detection logic is flagging her in the notes but not clearing the slot. The slot should be empty (—) with the departure note preserved.

**Issue 2 — Title misclassification (BCBSLA CMO = Brian Keller, Growth Officer)**
Brian Keller's title is "EVP, Chief Growth Officer." He is not a CMO. The engine is shoehorning a Growth Officer into the CMO slot because "growth" is adjacent to marketing. The title-match regex needs to be tightened.

**Issue 3 — Camerlinck past jobs regression (LLM nondeterminism)**
Copilot correctly identified this as LLM nondeterminism, not a code bug. However, the fix is to add a `past_jobs` retry: if the LLM returns a non-empty name but empty past_jobs, run one targeted follow-up query for that executive's career history before giving up.

**Issue 4 — Wrong persona (MMOH VP Experience = Betsy Williamson, Quality VP)**
Betsy Williamson's title is "VP, Quality Performance and Population Health." She is not a Customer Experience executive. The VP Experience persona needs a tighter definition in the prompt.

**Issue 5 — Past jobs sparsity**
Of the 50 rows, only 12 have any Past Job 1 data, and only 4 have Past Job 2 data. This is the biggest remaining gap for BD value. The engine is not trying hard enough to find past employment.

---

## v3.3 Copilot Instruction

Copy and paste everything below this line into Copilot:

---

We are applying engine v3.3. Please make the following targeted fixes to `src/payer_intel/crew.py` and related files. Do not change anything that is working.

### Fix 1 — Clear the slot when departure is detected (crew.py)

Currently, when the BD Notes mention that an executive has departed, the executive is still placed in their slot. The slot should be cleared to "—" if the departure detection fires.

In `_default_exec_bd_notes` (or wherever the departure/retirement flag is evaluated), if the function determines that an executive has **already departed** (as opposed to *announced future retirement*), set the executive's `name`, `title`, and `linkedin_url` to `None` so the export writes "—" in those cells. Keep the BD Note intact so the user knows who previously held the role.

Add a helper distinction:
- **Future retirement** (e.g., "retiring end of 2026"): Keep the executive in the slot, prepend `[RETIRING]` to BD Notes.
- **Already departed** (e.g., "departed March 2026", "left the company", "no longer with"): Clear the slot to "—", prepend `[DEPARTED — slot vacant]` to BD Notes.

Add these additional departure-signal phrases to the detection logic:
```python
_ALREADY_DEPARTED_SIGNALS = frozenset({
    "has departed", "have departed", "left the company", "no longer with",
    "stepped down", "has left", "have left", "departed in", "departed from",
    "is no longer", "has since left", "recently departed",
})
```

### Fix 2 — Tighten CMO title matching (crew.py)

The CMO persona is incorrectly matching "Chief Growth Officer" and "Chief Revenue Officer" titles. Update the CMO title-match regex or the LLM prompt's CMO definition to explicitly exclude these titles.

In the LLM prompt's persona definitions section, update the CMO definition to:

```
CMO (Chief Marketing Officer): The executive responsible for marketing, brand, and member/customer acquisition strategy. 
ACCEPT titles containing: Chief Marketing Officer, VP Marketing, SVP Marketing, Chief Brand Officer.
REJECT titles containing: Chief Growth Officer, Chief Revenue Officer, Chief Commercial Officer, Chief Strategy Officer, Chief Operating Officer. 
If no true CMO exists, return null for this persona.
```

### Fix 3 — Tighten VP Experience persona definition (crew.py)

The VP Experience persona is incorrectly matching Quality VPs and Population Health VPs. Update the prompt definition:

```
VP Experience (VP of Customer/Member Experience): The executive responsible for member satisfaction, customer experience, NPS, or digital engagement.
ACCEPT titles containing: Chief Experience Officer, VP Customer Experience, VP Member Experience, VP Digital Experience, Chief Digital Officer, VP Engagement.
REJECT titles containing: Quality, Population Health, Clinical, Operations, Finance, Strategy, Growth, Revenue.
If no true VP Experience exists, return null for this persona.
```

### Fix 4 — Past jobs retry for empty results (crew.py)

When the LLM returns a non-empty executive name but empty `past_jobs` list, add a targeted follow-up search before giving up. Add this logic in `assemble_executive_record` (or the equivalent post-LLM assembly function):

```python
# If we got a name but no past jobs, run a targeted career history query
if exec_record.name and not exec_record.past_jobs:
    career_query = f'"{exec_record.name}" "{payer_name}" career history OR "previously" OR "prior to" OR "before joining" OR "formerly"'
    career_results = list(_safe_search(client.google, career_query, num=5))
    career_results += list(_safe_search(client.linkedin_search, exec_record.name, company=payer_name, num=3))
    if career_results:
        # Re-ask the LLM with the additional career evidence
        past_jobs = _extract_past_jobs_from_evidence(exec_record.name, career_results)
        if past_jobs:
            exec_record.past_jobs = past_jobs[:2]
```

### Fix 5 — Improve past jobs extraction prompt (crew.py)

In the main LLM classification prompt, strengthen the past_jobs instruction to increase fill rate. Replace the current past_jobs instruction with:

```
"past_jobs": Extract the 2 most recent positions held BEFORE the current role. For each:
  - "firm": The name of the employer (not the current payer)
  - "title": The exact job title held
  - "years": The approximate years (e.g., "2019-2022" or "~3 years")
  
IMPORTANT: Search the evidence carefully for phrases like "previously", "prior to joining", "before joining", "formerly", "joined from", "came from", "background includes", "career includes". 
If the executive has spent their entire career at the current payer, use their two most recent INTERNAL roles (different titles/departments) as past_jobs, and note "internal promotion" in the years field.
Do NOT return an empty list unless you have absolutely no career history evidence whatsoever.
```

### Tests

After making these changes:
1. Add a test in `tests/test_executive_smoke.py` that verifies a "departed" executive (with "has departed" in evidence) results in a "—" name cell rather than the executive's name.
2. Add a test that verifies "Chief Growth Officer" is NOT placed in the CMO slot.
3. Run `pytest tests/ -x -q` — expect all tests to pass.
4. Run the 10-payer live run: `python main.py --mode executive --seed data/seed_payers_10random.csv --out out/exec10_v3_3`
5. Verify: BCBSLA CIO slot is empty (Tina Bourgeois departed); BCBSLA CMO slot is empty or has a true CMO (not Brian Keller); past jobs fill rate improves above 50%.
6. Commit: `fix(v3.3): departed-slot clearing, CMO/VPX title tightening, past-jobs retry + stronger prompt`
7. Push to `origin/main`.
