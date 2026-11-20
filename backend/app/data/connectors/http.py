"""Shared HTTP plumbing for all connectors: timeouts, retries with backoff, file downloads.

Public government endpoints are slow and occasionally flaky; every connector goes
through this one place so resilience behaviour is consistent and testable.
"""

from __future__ import annotations

import logging
import random
import time
from pathlib import Path
from typing import Any

import httpx

log = logging.getLogger(__name__)

BROWSER_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) airport-investment-agent/1.0"
RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class SourceUnavailableError(RuntimeError):
    """Raised when a public source cannot be reached after retries."""


def build_client(timeout_s: float = 60.0, user_agent: str = BROWSER_UA) -> httpx.Client:
    return httpx.Client(
        timeout=httpx.Timeout(timeout_s, connect=15.0),
        headers={"User-Agent": user_agent},
        follow_redirects=True,
    )


def request_with_retry(
    client: httpx.Client,
    method: str,
    url: str,
    *,
    attempts: int = 4,
    base_delay_s: float = 1.5,
    **kwargs: Any,
) -> httpx.Response:
    """HTTP request with exponential backoff + jitter on network errors and 429/5xx."""
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            response = client.request(method, url, **kwargs)
            if response.status_code not in RETRYABLE_STATUS:
                response.raise_for_status()
                return response
            last_error = httpx.HTTPStatusError(
                f"HTTP {response.status_code}", request=response.request, response=response
            )
        except (httpx.TransportError, httpx.HTTPStatusError) as exc:
            if isinstance(exc, httpx.HTTPStatusError) and (exc.response.status_code not in RETRYABLE_STATUS):
                raise SourceUnavailableError(f"{method} {url} -> {exc}") from exc
            last_error = exc
        if attempt < attempts:
            delay = base_delay_s * 2 ** (attempt - 1) + random.uniform(0, 0.5)
            log.warning("retry %s/%s for %s in %.1fs (%s)", attempt, attempts, url, delay, last_error)
            time.sleep(delay)
    raise SourceUnavailableError(f"{method} {url} failed after {attempts} attempts: {last_error}")


def download_file(client: httpx.Client, url: str, dest: Path, *, method: str = "GET", **kwargs: Any) -> Path:
    """Stream a (possibly large) file to disk atomically. Skips the download if cached."""
    if dest.exists() and dest.stat().st_size > 0:
        log.info("cache hit %s", dest.name)
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    for attempt in range(1, 4):
        try:
            with client.stream(method, url, **kwargs) as response:
                response.raise_for_status()
                with tmp.open("wb") as fh:
                    for chunk in response.iter_bytes(1 << 20):
                        fh.write(chunk)
            tmp.replace(dest)
            return dest
        except (httpx.TransportError, httpx.HTTPStatusError) as exc:
            log.warning("download attempt %s failed for %s: %s", attempt, url, exc)
            time.sleep(2 * attempt)
    raise SourceUnavailableError(f"download failed: {url}")
