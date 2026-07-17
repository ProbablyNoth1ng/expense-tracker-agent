from __future__ import annotations

import time
from datetime import datetime, timedelta
from typing import Any, Callable, Iterator

import httpx

from .models import RawTransaction


def statement_windows(start: datetime, end: datetime) -> Iterator[tuple[datetime, datetime]]:
    cursor = start
    maximum = timedelta(days=31)
    while cursor < end:
        boundary = min(cursor + maximum, end)
        yield cursor, boundary
        cursor = boundary


class MonobankClient:
    base_url = "https://api.monobank.ua"

    def __init__(
        self,
        *,
        token: str,
        transport: Any | None = None,
        sleeper: Callable[[float], None] = time.sleep,
        minimum_interval: float = 60.0,
        max_attempts: int = 3,
    ):
        if not token:
            raise ValueError("MONOBANK_TOKEN is required")
        self.token = token
        self.transport = transport or httpx.Client(timeout=30.0)
        self.sleeper = sleeper
        self.minimum_interval = minimum_interval
        self.max_attempts = max_attempts
        self._request_count = 0

    def _get(self, path: str) -> Any:
        for attempt in range(self.max_attempts):
            if self._request_count:
                self.sleeper(self.minimum_interval)
            response = self.transport.get(f"{self.base_url}{path}", headers={"X-Token": self.token})
            self._request_count += 1
            if response.status_code == 200:
                return response.json()
            retryable = response.status_code == 429 or 500 <= response.status_code < 600
            if retryable and attempt + 1 < self.max_attempts:
                headers = getattr(response, "headers", {})
                self.sleeper(float(headers.get("Retry-After", 2**attempt)))
                continue
            response.raise_for_status()
        raise RuntimeError("Monobank request retry loop exhausted")

    def list_accounts(self) -> list[dict[str, Any]]:
        payload = self._get("/personal/client-info")
        return [
            {
                "id": str(account["id"]),
                "type": str(account.get("type", "unknown")),
                "currency_code": int(account.get("currencyCode", 0)),
                "masked_pan": list(account.get("maskedPan", [])),
            }
            for account in payload.get("accounts", [])
        ]

    def iter_statements(
        self,
        account_ids: list[str],
        start: datetime,
        end: datetime,
    ) -> Iterator[RawTransaction]:
        for account_id in account_ids:
            for window_start, window_end in statement_windows(start, end):
                payload = self._get(
                    f"/personal/statement/{account_id}/{int(window_start.timestamp())}/{int(window_end.timestamp())}"
                )
                if not isinstance(payload, list):
                    raise ValueError("Monobank statement response must be a list")
                for item in payload:
                    yield RawTransaction.from_api(account_id, item)
