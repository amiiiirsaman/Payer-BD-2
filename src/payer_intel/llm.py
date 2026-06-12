from __future__ import annotations

from functools import lru_cache

from crewai import LLM

from .config import get_settings


@lru_cache(maxsize=1)
def get_llm() -> LLM:
    s = get_settings()
    return LLM(
        model=f"bedrock/{s.bedrock_model_id}",
        aws_region_name=s.aws_region,
        aws_access_key_id=s.aws_access_key_id,
        aws_secret_access_key=s.aws_secret_access_key,
        temperature=0.2,
        max_tokens=4096,
    )
