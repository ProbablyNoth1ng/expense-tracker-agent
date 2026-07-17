from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import httpx


class NbpClient:
    def __init__(self, *, transport: Any | None = None, store: Any | None = None):
        self.transport = transport or httpx.Client(timeout=20.0)
        self.store = store

    def rate_for(self, currency: str, requested: date) -> tuple[float, date]:
        if currency == "PLN":
            return 1.0, requested
        if self.store:
            cached = self.store.get_fx_rate(currency, requested)
            if cached:
                return cached[0], date.fromisoformat(cached[1])
        candidate = requested
        for _ in range(10):
            response = self.transport.get(
                f"https://api.nbp.pl/api/exchangerates/rates/a/{currency.lower()}/{candidate.isoformat()}/",
                headers={"Accept": "application/json"},
            )
            if response.status_code == 200:
                payload = response.json()
                rate_data = payload["rates"][0]
                rate = float(rate_data["mid"])
                effective = date.fromisoformat(rate_data["effectiveDate"])
                if self.store:
                    self.store.cache_fx_rate(currency, requested, effective, rate)
                return rate, effective
            if response.status_code != 404:
                response.raise_for_status()
            candidate -= timedelta(days=1)
        raise RuntimeError(f"No NBP rate for {currency} on or before {requested}")


def convert_to_pln(amount: float, currency: str, transaction_date: date, client: Any) -> tuple[float, float, date]:
    if currency == "PLN":
        return round(amount, 2), 1.0, transaction_date
    rate, effective = client.rate_for(currency, transaction_date)
    return round(amount * rate, 2), rate, effective

