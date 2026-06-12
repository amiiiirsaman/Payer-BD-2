# AArete BD Executive Intelligence Engine
**Copilot Instruction Set: Engine Iteration 2 (Bug Fixes & Enhancements)**

**Purpose:** This document provides the instructions to fix critical bugs and enhance the **Executive Intelligence Engine** (GitHub repo: `amiiiirsaman/Payer-BD-2`). The first run of the engine revealed a critical cross-payer contamination bug and missing data granularity. This iteration addresses those issues, adds richer data sources, and implements a stale data guard.

---

## 1. Critical Bug Fix: Cross-Payer Contamination (HIGHEST PRIORITY)

**The Issue:** The LLM classifier is misattributing executives to the wrong payer. For example, Sushma Akunuru (CIO of Independence Blue Cross) was misattributed to Aetna because her profile appeared in the search results and the LLM did not strictly enforce a current employer match.

**The Fix:**
Update the prompt in `_classify_executives_with_llm` (in `crew.py`) and add a strict post-classification validation step.

1. **Update LLM Prompt:**
   - Add explicit instruction: "You MUST verify that the executive CURRENTLY works at the target payer (`payer_name`). If the payer name (or its known aliases/subsidiaries) does not appear in the CURRENT role section of the evidence, DO NOT assign them to this payer. Reject any profile where the executive works for a different health plan."
2. **Add Post-Classification Validation (`crew.py`):**
   - After the LLM returns the JSON, add a Python validation step that checks the `current_title` and `evidence` fields for the target payer name (case-insensitive, including subsidiaries). If the payer name is missing, drop the executive to prevent false positives.

---

## 2. Enhancement: Past 2 Jobs Requirement

**The Issue:** The current engine extracts up to 3 past firms as company names only. The user requires the past 2 jobs with specific details (Firm, Title, and Approximate Years) to build warm BD introductions.

**The Fix:**
1. **Update Schema (`schema.py`):**
   - Replace `past_firms: List[str]` with a new structured field:
     ```python
     class PastJob(BaseModel):
         firm: str
         title: str
         years: str # e.g., "2018-2022" or "4 years"

     class ExecutiveProfile(BaseModel):
         # ... existing fields ...
         past_jobs: List[PastJob] # Limit to top 2 most recent prior jobs
     ```
2. **Update LLM Prompt (`crew.py`):**
   - Instruct the LLM to extract the 2 most recent prior roles (excluding the current role at the target payer) and format them as `[{firm, title, years}]`.
3. **Update Excel Export (`export.py`):**
   - In `EXECUTIVE_EXCEL_COLUMNS`, replace the single "Past Firms (Aggregated)" column with 30 new columns (6 columns per persona × 5 personas) OR create a separate "Executive Detail" sheet.
   - **Recommended approach (Main Sheet Expansion):** For each persona (e.g., CEO), add:
     - `[Persona] Past Job 1 Firm`
     - `[Persona] Past Job 1 Title`
     - `[Persona] Past Job 1 Years`
     - `[Persona] Past Job 2 Firm`
     - `[Persona] Past Job 2 Title`
     - `[Persona] Past Job 2 Years`
   - Update `write_excel_executive` to map the `past_jobs` array to these new columns.

---

## 3. Enhancement: Richer Data Sources

**The Issue:** Relying primarily on LinkedIn and generic press releases misses key executive movements, especially in the healthcare payer space.

**The Fix:**
Update the search tools (`crew_tools.py`) and agent instructions (`agents.py`) to specifically target high-signal healthcare leadership sources.

1. **Update `executive_news_agent` and `executive_third_party_agent` to query:**
   - **Becker's Payer Issues:** `site:beckerspayer.com "[Payer Name]" (CEO OR CIO OR CMO OR "Chief Medical" OR "VP")`
   - **Modern Healthcare:** `site:modernhealthcare.com "[Payer Name]" (appointed OR named OR steps down)`
   - **Health Plan Week / AIS Health:** Search for executive announcements.
   - **AHIP Conference Speakers:** `site:ahip.org/conferences "[Payer Name]"` (speakers are confirmed current executives).
2. **Update `executive_linkedin_agent`:**
   - In addition to `/in/` profiles, instruct the agent to look for LinkedIn Company pages (`/company/payer-name/people/`) to verify current employee rosters.
3. **Payer Investor Relations / Leadership Pages:**
   - Prioritize scraping `site:[payer_domain]/about/leadership` or `site:[payer_domain]/investors` for the official source of truth.

---

## 4. Enhancement: Stale Data Guard & Departure Risk

**The Issue:** Executive data becomes stale quickly (e.g., Blue KC CEO Erin Stucky announced retirement for 2026, but the engine just listed her without context).

**The Fix:**
1. **Recency Check & Retirement Detection (`crew.py` / `agents.py`):**
   - Instruct the `executive_news_agent` to explicitly search for: `"[Payer Name]" "[Executive Name]" (retire OR departure OR steps down OR successor)`.
   - If evidence of retirement or departure is found, flag the executive.
2. **Update Output Data Model (`schema.py` & `export.py`):**
   - Add a `departure_risk` boolean or string flag to `ExecutiveProfile`.
   - Add a `Last Verified Date` column per executive (or globally for the row, updated to the current run date).
   - Update the **BD Notes** generation:
     - If `departure_risk` is true, prepend `[DEPARTURE RISK/RETIRING]` to the BD Notes.
     - If the only evidence found is older than 12 months, append `[Verify - evidence >12 months old]` to the BD Notes.

---

## 5. Summary of Excel Column Updates

The final Excel export (`export.py`) must be updated to include the following structure for **each** of the 5 personas (CEO, CIO, CMO, Chief Medical, VP Experience):

- `[Persona] Name`
- `[Persona] Title` (Exact title extracted)
- `[Persona] LinkedIn`
- `[Persona] Past Job 1 Firm`
- `[Persona] Past Job 1 Title`
- `[Persona] Past Job 1 Years`
- `[Persona] Past Job 2 Firm`
- `[Persona] Past Job 2 Title`
- `[Persona] Past Job 2 Years`

Plus global columns:
- `Payer Name`
- `Payer Type`
- `Date Verified`
- `Confidence Score`
- `BD Notes` (Now including Departure Risk and Stale Data warnings)

---

## Instructions for Copilot Implementation

1. **Apply the Cross-Payer Contamination Fix:** Modify `crew.py` -> `_classify_executives_with_llm` to add the strict payer-matching rule in the prompt and the post-validation Python logic.
2. **Update Schema & Export:** Modify `schema.py` to add the `PastJob` model and update `export.py` to generate the expanded columns for past jobs.
3. **Enhance Search Tools:** Update `crew_tools.py` to include queries for Becker's Payer, Modern Healthcare, and AHIP.
4. **Implement Stale Data Guard:** Update the LLM prompt in `crew.py` to analyze the recency of evidence and search for retirement/departure keywords, updating the BD Notes accordingly.
