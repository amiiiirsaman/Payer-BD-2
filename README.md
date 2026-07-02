# Payer Intelligence Engine

A multi-agent research pipeline for U.S. healthcare payers. Given a CSV of
target health plans, it produces an Excel workbook with cited, scored, and
QC-gated evidence in one of two modes:

- **Product mode** — for each payer, which Salesforce products are in use
  (`Yes` / `Likely` / `No` / `Unknown`) with per-product confidence and
  source URLs.
- **Executive mode** — for each payer, the five BD personas (CEO, CIO/CTO,
  CMO/Growth, Chief Medical, VP Member Experience) with current title,
  LinkedIn URL, last two prior roles, and departure-risk flags.

The engine is built on [CrewAI](https://docs.crewai.com/) agents driven by
AWS Bedrock (Anthropic Claude Sonnet), with deterministic sourcing
([SearchApi.io](https://www.searchapi.io/)) and deterministic QC layers on
either side of the LLM to suppress fabrication and stale data.

---

## Table of contents

- [Setup](#setup)
- [Configuration](#configuration)
- [Seed CSV format](#seed-csv-format)
- [Running](#running)
- [Output](#output)
- [Pipeline architecture](#pipeline-architecture)
- [The agents](#the-agents)
  - [Product mode agents](#product-mode-agents)
  - [Executive mode agents](#executive-mode-agents)
- [Step-by-step: what gets checked, in what order](#step-by-step-what-gets-checked-in-what-order)
- [Source coverage](#source-coverage)
- [QC and confidence scoring](#qc-and-confidence-scoring)
- [Tests](#tests)
- [License](#license)

---

## Setup

Requirements: Python 3.11+, Windows / macOS / Linux. The examples below use
Windows PowerShell.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## Configuration

Create a `.env` file at the repo root:

```ini
AWS_REGION=us-east-1
AWS_ACCESS_KEY_ID=...
AWS_SECRET_ACCESS_KEY=...
BEDROCK_MODEL_ID=us.anthropic.claude-sonnet-4-5-20250929-v1:0
SEARCHAPI_API_KEY=...
```

The AWS credentials must have Bedrock `InvokeModel` access for the chosen
model. SearchApi.io is paid (each payer consumes ~7 calls in product mode
and ~9 calls in executive mode); the engine performs a pre-run quota check
and aborts if your remaining quota is insufficient for the seed size.

## Seed CSV format

UTF-8 (with optional BOM). One row per payer, header required.

| column            | required | example                                  |
|-------------------|----------|------------------------------------------|
| `payer_name`      | yes      | `Humana Inc.`                            |
| `domain`          | yes      | `humana.com`                             |
| `payer_type`      | yes      | `National`, `Blues Plan`, `Medicaid MCO`, `Regional` |
| `search_aliases`  | no       | `Humana\|Humana Inc` (pipe-separated)    |
| `search_excludes` | no       | `AmeriHealth Caritas\|AmeriHealth NJ` (sibling entities to reject during attribution) |

Provided seeds:

- [data/seed_payers.csv](data/seed_payers.csv) — 60-payer sample.
- [data/seed_payers_smoke.csv](data/seed_payers_smoke.csv) — 3-payer smoke test.

## Running

```powershell
# Product mode (default)
python main.py --mode product   --seed data/seed_payers_smoke.csv --out out/smoke

# Executive mode
python main.py --mode executive --seed data/seed_payers_smoke.csv --out out/exec_smoke
```

Flags:

- `--mode {product,executive}` — pipeline to run (default `product`).
- `--seed PATH` — input CSV (default `data/seed_payers_smoke.csv`).
- `--out DIR`  — output directory (default `out`).
- `--log-level LEVEL` — `DEBUG`, `INFO` (default), `WARNING`, etc.

## Output

### Product mode

`enterprise_BD_Salesforce_Payer_Intelligence_YYYYMMDD.xlsx` — one row per payer
with verdict columns (`Yes` / `Likely` / `No` / `Unknown`) for each Salesforce
product (Sales Cloud, Service Cloud, Experience Cloud, Marketing Cloud,
Pardot, Health Cloud, Agentforce for Healthcare, Life Sciences Cloud,
Financial Services Cloud, Revenue Cloud (CPQ), Data Cloud), plus consolidated
source URLs, identification date, confidence score, BD notes, and a key
evidence summary.

### Executive mode

`enterprise_BD_Executive_Intelligence_YYYYMMDD.xlsx` — three sheets:

1. **Executive Intelligence** — flat 15-column layout, five rows per payer
   (one per persona):
   `Payer Name | Payer Type | Persona | Executive Name | Exact Title |
   LinkedIn | Past Job 1 Firm | Past Job 1 Title | Past Job 1 Years |
   Past Job 2 Firm | Past Job 2 Title | Past Job 2 Years | Date Verified |
   Confidence Score | BD Notes`.
2. **Coverage Dashboard** — fill rate per persona across the seed.
3. **Past Firms Index** — every prior employer surfaced, with the executive
   it came from (for warm-intro routing).

Empty cells are rendered as em dashes (—) rather than blanks, so the
distinction between "found and empty" and "not yet checked" is unambiguous.

---

## Pipeline architecture

```
            seed CSV
                │
                ▼
   ┌──────────────────────────────┐
   │  Sourcing layer              │  SearchApi.io (Google / News / Jobs)
   │  (deterministic, no LLM)     │  + httpx page-body fetch
   └──────────────┬───────────────┘     (allow-listed hosts only)
                  │ Evidence[]
                  ▼
   ┌──────────────────────────────┐
   │  CrewAI agents on Bedrock    │  Claude Sonnet 4.5 via litellm
   │  (LLM classification)        │  - product verdicts OR personas
   └──────────────┬───────────────┘
                  │ PayerRecord / ExecutivePayerRecord
                  ▼
   ┌──────────────────────────────┐
   │  QC layer                    │  recency, persona reject filters,
   │  (deterministic)             │  deceased / departure / successor
   │                              │  guards, fabrication checks
   └──────────────┬───────────────┘
                  │
                  ▼
            Excel workbook
```

Source files under [src/payer_intel/](src/payer_intel/):

- [crew.py](src/payer_intel/crew.py) — pipeline orchestration, evidence
  gathering, retry logic, page-body enrichment.
- [agents.py](src/payer_intel/agents.py) — CrewAI `Agent` definitions
  (one per role described below).
- [crew_tools.py](src/payer_intel/crew_tools.py) — tools the agents call
  (`GoogleSearchTool`, `GoogleNewsTool`, `GoogleJobsTool`,
  `TechFingerprintTool`, `ExecLinkedInSearchTool`,
  `ExecLeadershipPageTool`, `ExecThirdPartyDirectoryTool`).
- [schema.py](src/payer_intel/schema.py) — Pydantic models, Excel column
  contracts, persona title maps.
- [qc.py](src/payer_intel/qc.py) — product-mode confidence scoring.
- [qc_exec.py](src/payer_intel/qc_exec.py) — executive-mode confidence
  scoring (`High` / `Medium` / `Low` per executive, aggregated per payer).
- [export.py](src/payer_intel/export.py) — Excel rendering (formatting,
  conditional fills on verdict cells, persona-grouping for executives).
- [tools/](src/payer_intel/tools/) — `search_api.py` (SearchApi.io client
  with quota tracking), `fetcher.py` (httpx with browser headers and
  redirect following), `tech_fingerprint.py` (lightweight HTTP probe for
  Salesforce-managed infrastructure).

---

## The agents

### Product mode agents

| Agent (`agents.py`)        | Role                              | Tool(s)                                          | What it does |
|----------------------------|-----------------------------------|--------------------------------------------------|--------------|
| `orchestrator_agent`       | BD Intelligence Orchestrator      | (delegation only)                                | Coordinates the sub-agents below; only agent allowed to delegate. |
| `target_identification_agent` | Target List Curator            | —                                                | Canonicalizes payer names + domains from the seed. |
| `jobs_agent`               | Job Posting Analyst               | `GoogleJobsTool`                                 | Mines `Salesforce` / `Health Cloud` / `Marketing Cloud` / `Agentforce` / `Vlocity` mentions in current job postings (last 12 months). |
| `news_agent`               | PR & News Intelligence Analyst    | `GoogleNewsTool`                                 | Press releases and trade-press stories about payer Salesforce implementations. |
| `reviews_agent`            | Software Review Analyst           | `GoogleSearchTool`                               | G2 / Capterra / TrustRadius reviews authored by the payer. |
| `case_study_agent`         | Case Study & Partner Researcher   | `GoogleSearchTool`                               | Official Salesforce case studies + SI-partner success stories (Silverline, Penrod, Slalom, Deloitte, Accenture, Cognizant, IBM). |
| `technographic_agent`      | Technographic Fingerprint Analyst | `TechFingerprintTool`                            | HTTP-probes the payer's public web properties for Salesforce-managed infrastructure (`force.com`, `my.salesforce-sites.com`, Marketing Cloud beacons). |
| `classifier_agent`         | Salesforce Product Taxonomy Classifier | —                                            | Maps raw evidence snippets to specific Salesforce Clouds and emits a `Yes` / `Likely` / `No` / `Unknown` verdict per product. Never confuses Marketing Cloud with Pardot, Service Cloud with Health Cloud, etc. |
| `recency_agent`            | Temporal & Recency Auditor        | —                                                | Normalizes dates and flags anything older than 18 months. |
| `qc_agent`                 | Quality Control Analyst           | —                                                | Reconciles conflicting signals; gatekeeper before export. |
| `export_agent`             | Excel Export Specialist           | —                                                | Renders the final workbook. |

### Executive mode agents

| Agent (`agents.py`)         | Role                              | Tool(s)                                                                 | What it does |
|-----------------------------|-----------------------------------|-------------------------------------------------------------------------|--------------|
| `executive_linkedin_agent`  | Executive Profile Hunter          | `ExecLinkedInSearchTool`                                                | Finds LinkedIn profiles for each of the five personas at the target payer. Captures name, current title, LinkedIn URL, and the two most recent prior roles. Strictly enforces payer-name matching — never assigns a profile from a different health plan. |
| `executive_news_agent`      | Leadership Change Tracker         | `GoogleNewsTool`, `ExecLeadershipPageTool`                              | Press releases announcing executive appointments **and** departures. Flags retirements, planned departures, and named successors. Sources include payer newsrooms, business wire services, Becker's Payer Issues, Modern Healthcare, AHIP. |
| `executive_third_party_agent` | Executive Directory Cross-Referencer | `ExecThirdPartyDirectoryTool`                                       | Triangulates tenure and career history via ZoomInfo, RocketReach, Becker's, Modern Healthcare, and AHIP speaker pages — used to elevate confidence to `High` when corroborated. |
| `executive_classifier_agent` | Executive Name Resolver          | —                                                                        | From all gathered evidence, identifies the single current holder of each persona. Rejects any executive whose current employer does not match the target payer. Resolves collisions by preferring `Present` LinkedIn tenure or the most recent press release. Sets `departure_risk=true` when a retirement or successor signal is present. |

---

## Step-by-step: what gets checked, in what order

### Product mode (per payer)

1. **Seed normalization** — strip whitespace, build a name clause that ORs
   the canonical name with every `search_aliases` entry.
2. **Known case-study injection** — for payers with a hand-verified
   Salesforce case study URL (e.g. UnitedHealthcare, Humana), the URL is
   pre-seeded so the body enricher always fetches it (search engines
   routinely rank these outside the top 20).
3. **Jobs query** — `GoogleJobsTool` searches for the payer name + an OR
   clause of Salesforce product names (`Salesforce`, `Sales Cloud`,
   `Service Cloud`, `Health Cloud`, `Marketing Cloud`, `Experience Cloud`,
   `Data Cloud`, `Pardot`, `ExactTarget`, `CRM Analytics`, `Agentforce`,
   `Vlocity`); up to 20 postings.
4. **News query** — `GoogleNewsTool` searches for the payer name + product
   terms (`Salesforce`, `Health Cloud`, `Data Cloud`, `Marketing Cloud`,
   `Agentforce`); up to 20 stories.
5. **Salesforce.com keynote sweep** — `site:salesforce.com` for the payer
   name combined with `Agentforce`, `Dreamforce`, `Einstein Copilot`
   (catches conference sessions and customer-story pages not indexed as
   news).
6. **Reviews query** — `GoogleSearchTool` on G2 / Capterra / TrustRadius
   for the payer name + Salesforce product names.
7. **Case-study & partner query** — `site:` searches across Salesforce
   customer-stories, Salesforce blog, and the major SI partners.
8. **CIO/executive interview query** — Deloitte Insights, HBR, Healthcare
   IT News, Modern Healthcare, HealthTech Magazine for the payer's CIO
   talking about Salesforce.
9. **LinkedIn employee snippet query** — `site:linkedin.com/in/` for the
   payer name + specialist titles (`Salesforce Marketing Cloud Specialist`,
   `Health Cloud Administrator`, `Salesforce Developer`,
   `Agentforce Developer`, `Vlocity`).
10. **Technographic probe** — `TechFingerprintTool` HTTP-probes the payer
    domain and common subdomains for Salesforce-managed infrastructure.
11. **Page-body enrichment** — for any URL whose host is on the
    `_FETCH_DOMAINS` allow-list (see [Source coverage](#source-coverage)),
    fetch the page body with browser headers, strip nav / footer / scripts,
    cap at the configured length, and attach as `Evidence.full_text` so the
    classifier sees the full prose, not just the snippet.
12. **Classification (LLM)** — `classifier_agent` ingests all evidence and
    emits a per-product `Yes` / `Likely` / `No` / `Unknown` verdict in
    strict JSON.
13. **QC** — `qc.score()` applies the deterministic rule table (see
    [QC and confidence scoring](#qc-and-confidence-scoring)) to override
    the LLM verdict where rules dictate (e.g. an official case study
    always promotes to `Yes` / `High`).
14. **Export** — `export.write_excel()` writes the workbook with
    color-coded verdict cells and a frozen header row.

### Executive mode (per payer)

1. **Seed normalization** — same as product mode.
2. **Per-persona LinkedIn snippets (5 calls)** — one Google query per
   persona (`CEO`, `CIO`, `CMO`, `Chief Medical`, `VP Experience`)
   restricted to `site:linkedin.com/in/` OR `site:linkedin.com/pub/`,
   combined with a persona-specific title clause built from
   `EXECUTIVE_TITLE_MAP` in [schema.py](src/payer_intel/schema.py).
3. **Payer-domain leadership page search** — `site:{domain}` search
   combined with `leadership` / `executive team` / `our leaders` /
   `our team`; returns the payer's real leadership slug regardless of
   path convention.
4. **Direct leadership-path fetch** — for a small set of common slugs
   (`leadership`, `about/leadership`, `our-team`, `executive-team`,
   `about-us/leadership`, `about/our-leaders`, `leadership-team`),
   construct `https://{domain}/{path}` directly and let the body enricher
   pull whatever resolves (HTTP 200 bodies are surfaced; 404s are
   harmless).
5. **Anchor-discovery fetch** — fetch the payer homepage once and follow
   anchors whose href or text matches `/(leadership|executive.team|our.team|about.us)/i`,
   so payers with non-standard leadership URLs are still captured.
6. **Executive-appointment news (2-year window)** — `GoogleNewsTool` with
   `qdr:2y`, combining the payer name with appointment / retirement /
   successor / obituary terms and C-level titles.
7. **Leadership news** — general leadership coverage on healthcare trade
   press.
8. **Third-party directory cross-reference** — top executives are
   re-queried on ZoomInfo / RocketReach / Becker's for triangulation.
9. **Page-body enrichment** — same fetcher as product mode, plus a
   special-case 12 000-char cap for `leadership_page` evidence (one page
   can list many execs).
10. **Persona classification (LLM)** — `executive_classifier_agent`
    identifies the current holder of each persona, extracts past two
    roles, and sets `departure_risk` when retirement / successor signals
    are present.
11. **Persona reject filters** — deterministic title-pattern guards in
    [crew.py](src/payer_intel/crew.py) drop misclassifications (e.g. a
    `Chief Operating Officer` accidentally extracted for the `Chief
    Medical` slot is dropped, not promoted).
12. **Employer match guard** — any candidate whose extracted current
    employer does not match the payer name (or one of its aliases) is
    dropped, with the dropped name logged.
13. **Deceased / departure guard** — obituary and "passed away" signals
    cause the executive to be dropped, and the slot retried.
14. **Empty-slot retry** — if a persona slot is empty after the first
    pass, a broader retry query is issued combining LinkedIn / Becker's /
    Modern Healthcare with the payer's own domain.
15. **Executive QC** — `qc_exec.score_executive()` assigns `High` /
    `Medium` / `Low` per executive; payer-level confidence is the max.
16. **Export** — `export.write_excel_executive()` writes the three-sheet
    workbook.

---

## Source coverage

The HTTP body enricher will fetch and parse the body of any URL whose host
matches the allow-list in `_FETCH_DOMAINS` ([crew.py](src/payer_intel/crew.py)).
LinkedIn is intentionally excluded — its pages are auth-walled and httpx
gets a login redirect, so the engine relies on SearchApi snippets alone for
LinkedIn evidence. In executive mode the payer's own domain is **always**
allowed regardless of the list, so leadership pages on any payer site are
fetched.

| Category | Hosts |
|---|---|
| Salesforce-owned        | `salesforce.com`, `trailhead.salesforce.com` |
| Payer newsrooms / IR    | `news.blueshieldca.com`, `newsroom.humana.com`, `newsroom.cigna.com`, `newsroom.elevancehealth.com`, `ir.molinahealthcare.com`, `newsroom.kaiserpermanente.org`, `newsroom.highmark.com` |
| Wire services           | `businesswire.com`, `prnewswire.com`, `globenewswire.com` |
| Trade press             | `fiercehealthcare.com`, `healthcaredive.com`, `mobihealthnews.com`, `medcitynews.com`, `beckershospitalreview.com`, `beckerspayer.com`, `ahip.org` |
| CIO / exec interviews   | `deloitte.wsj.com`, `deloitte.com`, `hbr.org`, `healthcareitnews.com`, `modernhealthcare.com`, `healthtechmagazine.net` |
| Executive directories   | `zoominfo.com`, `rocketreach.co` |
| **Organization website** | Any `{domain}` from the seed CSV (executive mode only) |

Search-only sources (snippets are used, page body is not fetched): LinkedIn
(`site:linkedin.com/in/`, `/pub/`, `/posts/`, `/pulse/`), G2, Capterra,
TrustRadius.

---

## QC and confidence scoring

### Product mode ([qc.py](src/payer_intel/qc.py))

Verdict + confidence is selected by the first matching rule:

| # | Rule                                                            | Verdict  | Confidence |
|---|------------------------------------------------------------------|----------|------------|
| 1 | Official Salesforce case study present                           | `Yes`    | `High`     |
| 2 | LinkedIn employee + technographic hit                            | `Yes`    | `High`     |
| 3 | LinkedIn employee + recent (≤ 12 mo) job posting                 | `Yes`    | `High`     |
| 4 | LinkedIn employee + recent (≤ 12 mo) news                        | `Yes`    | `High`     |
| 5 | ≥ 2 distinct LinkedIn employees                                  | `Yes`    | `High`     |
| 6 | Recent job + recent review / news / technographic                | `Yes`    | `High`     |
| 7 | Single LinkedIn employee                                         | `Likely` | `Medium`   |
| 8 | Single recent job / review / news / technographic                | `Likely` | `Medium`   |
| 9 | Only stale signals (> 18 mo)                                     | `Unknown`| `Low`      |
| 10| No qualifying signals                                            | `Unknown`| `Low`      |

### Executive mode ([qc_exec.py](src/payer_intel/qc_exec.py))

Per-executive confidence, selected by the first matching rule:

| # | Rule                                                             | Confidence |
|---|------------------------------------------------------------------|------------|
| 1 | Official leadership page hit + LinkedIn profile                  | `High`     |
| 2 | Recent (≤ 12 mo) press release + LinkedIn profile                | `High`     |
| 3 | Official leadership page alone (authoritative source)            | `High`     |
| 4 | Active LinkedIn only (`Present` tenure on profile)               | `Medium`   |
| 5 | Recent press release only (no LinkedIn / leadership)             | `Medium`   |
| 6 | Third-party directory only (ZoomInfo / RocketReach / Becker's)   | `Low`      |
| 7 | LinkedIn snippet without `Present` indicator (possibly stale)    | `Low`      |
| 8 | No qualifying signals                                            | `Low`      |

Payer-level confidence = max(High > Medium > Low) over identified executives.

---

## Tests

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

`tests/test_search_api.py` and `tests/test_tech_fingerprint.py` make live
network calls and need a valid `SEARCHAPI_API_KEY`. To skip them in CI:

```powershell
.\.venv\Scripts\python.exe -m pytest --ignore=tests/test_search_api.py --ignore=tests/test_tech_fingerprint.py -q
```

---

## License

[MIT](LICENSE).
