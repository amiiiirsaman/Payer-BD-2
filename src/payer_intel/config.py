from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(_ROOT / ".env", override=False)


@dataclass(frozen=True)
class Settings:
    aws_region: str
    aws_access_key_id: str
    aws_secret_access_key: str
    bedrock_model_id: str
    searchapi_key: str
    search_provider: str
    searchapi_endpoint: str = "https://www.searchapi.io/api/v1/search"
    max_calls_per_run: int = 200
    http_timeout_seconds: float = 12.0


def _require(name: str) -> str:
    val = os.getenv(name)
    if not val:
        raise RuntimeError(f"Missing required env var: {name}")
    return val.strip()


def get_settings() -> Settings:
    return Settings(
        aws_region=os.getenv("AWS_REGION", "us-east-1").strip(),
        aws_access_key_id=_require("AWS_ACCESS_KEY_ID"),
        aws_secret_access_key=_require("AWS_SECRET_ACCESS_KEY"),
        bedrock_model_id=os.getenv(
            "BEDROCK_MODEL_ID",
            "us.anthropic.claude-sonnet-4-5-20250929-v1:0",
        ).strip(),
        searchapi_key=_require("SEARCHAPI_API_KEY"),
        search_provider=os.getenv("SEARCH_PROVIDER", "searchapi").strip().lower(),
    )


PROJECT_ROOT: Path = _ROOT
