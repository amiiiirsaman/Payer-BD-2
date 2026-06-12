from __future__ import annotations

from typing import Optional

import httpx

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0 Safari/537.36 AarateBDBot/1.0"
)


def get_client(timeout: float = 12.0) -> httpx.Client:
    return httpx.Client(
        headers={"User-Agent": _UA, "Accept": "text/html,application/xhtml+xml"},
        timeout=timeout,
        follow_redirects=True,
    )


def fetch(url: str, timeout: float = 12.0) -> Optional[httpx.Response]:
    try:
        with get_client(timeout=timeout) as c:
            r = c.get(url)
            if r.status_code >= 400:
                return None
            return r
    except (httpx.HTTPError, OSError):
        return None
