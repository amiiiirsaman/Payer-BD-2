# Payer Intelligence Engine

An evidence-driven research pipeline for U.S. healthcare payers. Given a CSV of
target payers, it produces an Excel workbook of either:

- **Product mode** — which Salesforce products each payer uses, with cited
  evidence and a per-product confidence verdict.
- **Executive mode** — five named executives per payer (CEO, CIO/CTO,
  CMO/Growth, Chief Medical, VP Member Experience) with LinkedIn URLs, past
  two roles, and BD-ready notes.

Both modes share the same plumbing: a deterministic web-search + page-fetch
sourcing layer, an LLM classification layer (CrewAI agents on AWS Bedrock
Claude), and a quality-control layer that scores confidence and rejects
fabricated outputs.

## Stack

- Python 3.11+
- [CrewAI](https://docs.crewai.com/) for multi-agent orchestration
- AWS Bedrock (Anthropic Claude Sonnet) via [litellm](https://docs.litellm.ai)
- [SearchApi.io](https://www.searchapi.io/) for Google / Google News / Google Jobs
- `httpx` + `beautifulsoup4` for page-body enrichment
- `pandas` + `openpyxl` for Excel output
- `pytest` for the test suite

## Setup

```powershell
# 1. Create and activate a virtualenv (Windows PowerShell)
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# 2. Install dependencies
pip install -r requirements.txt

# 3. Configure secrets in a .env file at the repo root
```

Required environment variables (place in `.env`):

```ini
AWS_REGION=us-east-1
AWS_ACCESS_KEY_ID=...
AWS_SECRET_ACCESS_KEY=...
BEDROCK_MODEL_ID=us.anthropic.claude-sonnet-4-5-20250929-v1:0
SEARCHAPI_API_KEY=...
```

The AWS credentials must have Bedrock `InvokeModel` access for the chosen
model. SearchApi.io uses a paid quota (each payer consumes ~7 calls in product
mode and ~9 in executive mode); the engine performs a pre-run quota check and
aborts if insufficient.

## Seed CSV format

Columns (header required):

| column | required | example |
|---|---|---|
| `payer_name`     | yes | `Humana Inc.` |
| `domain`         | yes | `humana.com` |
| `payer_type`     | yes | `National`, `Blues Plan`, `Medicaid MCO`, `Regional`, ... |
| `search_aliases` | no  | `Humana\|Humana Inc` (pipe-separated additional names) |
| `search_excludes`| no  | `AmeriHealth Caritas\|AmeriHealth NJ` (sibling entities to reject) |

A sample 60-payer seed is provided at [data/seed_payers.csv](data/seed_payers.csv),
and a 3-payer smoke seed at [data/seed_payers_smoke.csv](data/seed_payers_smoke.csv).

## Running

```powershell
# Product mode (Salesforce footprint per payer)
python main.py --mode product --seed data/seed_payers_smoke.csv --out out/smoke

# Executive mode (5-persona executive intelligence)
python main.py --mode executive --seed data/seed_payers_smoke.csv --out out/exec_smoke
```

Flags:

- `--mode {product,executive}` — pipeline to run (default: `product`)
- `--seed PATH` — input CSV (default: `data/seed_payers_smoke.csv`)
- `--out DIR` — output directory (default: `out`)
- `--log-level LEVEL` — `DEBUG`, `INFO` (default), `WARNING`, etc.

## Output

### Product mode
`out/<dir>/Aarete_BD_Salesforce_Payer_Intelligence_YYYYMMDD.xlsx` — one row per
payer with a verdict column (`Yes` / `Likely` / `No` / `Unknown`) for each
Salesforce product, plus source URLs, confidence, and a BD-ready notes column.

### Executive mode
`out/<dir>/Aarete_BD_Executive_Intelligence_YYYYMMDD.xlsx` — three sheets:

1. **Executive Intelligence** — flat 15-column layout, five rows per payer
   (one per persona) with name, exact title, LinkedIn URL, past two roles,
   verification date, confidence, and BD notes.
2. **Coverage Dashboard** — fill rate per persona.
3. **Past Firms Index** — every prior employer surfaced, with the executive
   it came from (for warm-intro routing).

## Architecture

```
seed CSV
   │
   ▼
┌─────────────────────────────┐
│  Sourcing layer             │   SearchApi.io (Google / News / Jobs)
│  (deterministic, no LLM)    │   + httpx page-body fetch (allow-listed hosts)
└──────────────┬──────────────┘
               │ evidence
               ▼
┌─────────────────────────────┐
│  Classification layer       │   CrewAI agents on Bedrock Claude
│  (LLM)                      │   - product verdicts OR executive personas
└──────────────┬──────────────┘
               │ records
               ▼
┌─────────────────────────────┐
│  QC layer                   │   recency, fabrication guards, persona
│  (deterministic)            │   filters, deceased/successor checks
└──────────────┬──────────────┘
               │
               ▼
        Excel workbook
```

Key modules under [src/payer_intel/](src/payer_intel/):

- [crew.py](src/payer_intel/crew.py) — pipeline orchestration, evidence
  gathering, persona classification, retry logic
- [agents.py](src/payer_intel/agents.py) — CrewAI agent definitions
- [schema.py](src/payer_intel/schema.py) — Pydantic models and Excel column
  contracts
- [qc.py](src/payer_intel/qc.py) / `qc_exec.py` — confidence scoring and
  guard rails
- [export.py](src/payer_intel/export.py) — Excel rendering
- [tools/](src/payer_intel/tools/) — `search_api.py`, `fetcher.py`,
  `tech_fingerprint.py`

## Tests

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

`tests/test_search_api.py` and `tests/test_tech_fingerprint.py` make live
network calls; skip them in CI with:

```powershell
.\.venv\Scripts\python.exe -m pytest --ignore=tests/test_search_api.py --ignore=tests/test_tech_fingerprint.py -q
```

## License

Internal project. No license granted.
