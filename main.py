from __future__ import annotations

import argparse
import csv
import logging
import os
import sys
from pathlib import Path

import requests

# Allow `python main.py ...` without install
sys.path.insert(0, str(Path(__file__).parent / "src"))

from payer_intel.crew import run, run_executive  # noqa: E402

# Approximate SearchApi calls per payer in each mode (used to size the
# pre-run quota check). Product mode = ~7 calls (jobs + news + reviews +
# case study + community + CIO + LinkedIn). Executive mode = ~9 calls
# (5 LinkedIn personas + leadership + appointment news + wire + third-party).
_CALLS_PER_PAYER = {"product": 7, "executive": 9}


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Aarete Payer Intelligence Engine")
    p.add_argument("--seed", default="data/seed_payers_smoke.csv", help="CSV: payer_name,domain,payer_type")
    p.add_argument("--out", default="out", help="Output directory")
    p.add_argument("--log-level", default="INFO")
    p.add_argument(
        "--mode",
        choices=("product", "executive"),
        default="product",
        help="product = Salesforce product detection (default); executive = 5-persona executive intelligence.",
    )
    p.add_argument(
        "--workers",
        type=int,
        default=50,
        help=(
            "Number of parallel worker threads for payer processing. "
            "Default 50. Each worker runs one payer concurrently (SearchApi + LLM). "
            "Constrained by SearchApi rate limits (~10 req/s on paid plans) and "
            "AWS Bedrock TPS limits. Use --workers 5 for smoke tests, "
            "--workers 50 for production runs of 50-200 payers."
        ),
    )
    return p.parse_args()


def check_searchapi_quota(min_required: int) -> None:
    """Abort early if SearchApi quota is insufficient for the run."""
    key = os.environ.get("SEARCHAPI_API_KEY", "")
    if not key:
        print("SEARCHAPI_API_KEY not set - skipping quota check")
        return
    try:
        r = requests.get(
            "https://www.searchapi.io/api/v1/account",
            headers={"Authorization": f"Bearer {key}"},
            timeout=10,
        )
        if r.status_code != 200:
            print(f"Could not check SearchApi quota: HTTP {r.status_code} - proceeding anyway")
            return
        data = r.json()
        remaining = data.get("searches_remaining", data.get("remaining_searches"))
        if remaining is None:
            print("Could not read SearchApi quota from response - proceeding anyway")
            return
        if remaining < min_required:
            print(f"Insufficient SearchApi quota: need {min_required}, have {remaining}. Aborting.")
            sys.exit(1)
        print(f"SearchApi quota OK ({remaining} remaining, need {min_required})")
    except Exception as e:  # noqa: BLE001 — best-effort precheck
        print(f"Could not check SearchApi quota: {e} - proceeding anyway")


def main() -> int:
    args = parse_args()
    logging.basicConfig(level=args.log_level, format="%(asctime)s %(levelname)s %(name)s — %(message)s")
    with open(args.seed, encoding="utf-8-sig") as f:
        seed_count = sum(1 for _ in csv.DictReader(f))
    per_payer = _CALLS_PER_PAYER[args.mode]
    check_searchapi_quota(min_required=seed_count * per_payer)
    workers = args.workers
    print(f"Running in {args.mode!r} mode with {workers} parallel workers on {seed_count} payers.")
    if args.mode == "executive":
        out = run_executive(Path(args.seed), Path(args.out), workers=workers)
    else:
        out = run(Path(args.seed), Path(args.out), workers=workers)
    print(f"\nReport written: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
