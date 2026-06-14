"""Probe the v3.8 direct leadership-path fetches for the 15-payer seed.

For each (payer, path) pair, attempt the fetch and report:
- HTTP status (200 / 404 / other / fail)
- Body length after BS4 cleanup
- Whether a known executive name appears in the body
"""
import csv
from pathlib import Path
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from src.payer_intel.crew import _LEADERSHIP_PATHS, _WS_RE
from src.payer_intel.tools.fetcher import fetch

SEED = Path("data/seed_payers_15_v38.csv")

# Known CEO surnames per payer (used as a "does the page mention an exec" probe)
KNOWN_CEO = {
    "aetna.com": "Joyner",
    "bcbsri.com": "Talbert",
    "careoregon.org": "Carlson",
    "elevancehealth.com": "Boudreaux",
    "emblemhealth.com": "Palmateer",
    "excellusbcbs.com": "Reed",
    "fallonhealth.org": "Welch",
    "thehealthplan.com": "Gaskill",  # Geisinger Health Plan
    "geisinger.org": "Gilliland",
    "horizonblue.com": "Hilaire",
    "ibx.com": "Hilferty",
    "massgeneralbrighamhealthplan.org": "Hochman",
    "mvphealthcare.com": "Del Vecchio",
    "uhc.com": "Noel",
    "upmchealthplan.com": "Holder",  # legacy; current is Jenkins
    "wellsense.org": "Thiltgen",
}

with SEED.open(encoding="utf-8-sig") as f:
    reader = csv.DictReader(f)
    payers = list(reader)

total_attempts = 0
total_200 = 0
total_with_body = 0
total_mentions_ceo = 0
print(f"{'PAYER':<45} {'PATH':<22} {'STATUS':<8} {'BODY':>7}  CEO?")
print("-" * 100)
for p in payers:
    domain = (p.get("domain") or "").strip().lower()
    ceo_surname = KNOWN_CEO.get(domain, "")
    payer_label = p["payer_name"][:43]
    for path in _LEADERSHIP_PATHS:
        url = f"https://{domain}/{path}"
        total_attempts += 1
        resp = fetch(url, timeout=12.0)
        if resp is None:
            print(f"{payer_label:<45} {path:<22} {'FAIL':<8} {'-':>7}  -")
            continue
        status = resp.status_code
        body_len = 0
        has_ceo = False
        if status == 200:
            total_200 += 1
            soup = BeautifulSoup(resp.text, "html.parser")
            for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
                tag.decompose()
            text = soup.get_text(separator=" ", strip=True)
            cleaned = _WS_RE.sub(" ", text).strip()
            body_len = len(cleaned)
            if body_len > 200:
                total_with_body += 1
            if ceo_surname and ceo_surname.lower() in cleaned.lower():
                has_ceo = True
                total_mentions_ceo += 1
        ceo_mark = "YES" if has_ceo else ("-" if ceo_surname else "?")
        print(f"{payer_label:<45} {path:<22} {status:<8} {body_len:>7}  {ceo_mark}")

print("-" * 100)
print(
    f"SUMMARY: {total_attempts} attempts | "
    f"{total_200} HTTP 200 | "
    f"{total_with_body} with usable body | "
    f"{total_mentions_ceo} mention known CEO surname"
)
