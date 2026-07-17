from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from .constants import ISO_NUMERIC_CURRENCIES
from .models import NormalizedTransaction, RawTransaction


def clean_merchant(text: str) -> str:
    value = re.sub(r"\s+", " ", text).strip()
    value = re.sub(r"\b(?:SKLEP|STORE|SHOP)\s*\d+\b", "", value, flags=re.IGNORECASE)
    value = re.sub(r"\s+", " ", value).strip(" -_,.")
    upper = value.upper()
    aliases = {
        "LIDL POLSKA": "Lidl",
        "LIDL": "Lidl",
        "MCDONALDS": "McDonald's",
        "MCDONALD'S": "McDonald's",
    }
    for key, cleaned in aliases.items():
        if upper.startswith(key):
            return cleaned
    return value.title() if value else "Unknown"


def normalize_transaction(
    raw: RawTransaction,
    *,
    timezone: str = "Europe/Warsaw",
    allowed_year: int = 2026,
) -> NormalizedTransaction | None:
    if raw.hold or raw.amount >= 0:
        return None
    timestamp = datetime.fromtimestamp(raw.time, tz=UTC).astimezone(ZoneInfo(timezone))
    if timestamp.year != allowed_year:
        raise ValueError(f"Only {allowed_year} transactions are supported")
    currency = ISO_NUMERIC_CURRENCIES.get(raw.currency_code)
    if currency is None:
        raise ValueError(f"Unsupported ISO 4217 numeric code: {raw.currency_code}")
    merchant_raw = raw.counter_name.strip() or raw.description.strip() or raw.comment.strip()
    merchant = clean_merchant(merchant_raw)
    amount = round(abs(raw.operation_amount) / 100.0, 2)
    fingerprint_source = f"{raw.account_id}|{timestamp.isoformat()}|{merchant_raw}|{amount}|{currency}"
    fingerprint = hashlib.sha256(fingerprint_source.encode("utf-8")).hexdigest()
    return NormalizedTransaction(
        source_transaction_id=raw.id,
        account_id=raw.account_id,
        timestamp=timestamp,
        date=timestamp.date(),
        merchant=merchant,
        merchant_raw=merchant_raw,
        mcc=raw.mcc,
        original_amount=amount,
        original_currency=currency,
        fingerprint=fingerprint,
    )

