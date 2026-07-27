from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Literal


@dataclass(frozen=True, slots=True)
class RawTransaction:
    id: str
    account_id: str
    time: int
    description: str
    mcc: int
    amount: int
    operation_amount: int
    currency_code: int
    hold: bool
    counter_name: str = ""
    comment: str = ""

    @classmethod
    def from_api(cls, account_id: str, payload: dict[str, Any]) -> "RawTransaction":
        return cls(
            id=str(payload["id"]),
            account_id=account_id,
            time=int(payload["time"]),
            description=str(payload.get("description", "")),
            mcc=int(payload.get("mcc", 0)),
            amount=int(payload["amount"]),
            operation_amount=int(payload.get("operationAmount", payload["amount"])),
            currency_code=int(payload.get("currencyCode", 0)),
            hold=bool(payload.get("hold", False)),
            counter_name=str(payload.get("counterName", "")),
            comment=str(payload.get("comment", "")),
        )


@dataclass(frozen=True, slots=True)
class NormalizedTransaction:
    source_transaction_id: str
    account_id: str
    timestamp: datetime
    date: date
    merchant: str
    merchant_raw: str
    mcc: int
    original_amount: float
    original_currency: str
    fingerprint: str


@dataclass(frozen=True, slots=True)
class DetectedExpense:
    """Safe, user-facing summary of an outgoing settled transaction."""

    outcome: Literal["NEW", "ALREADY_IN_DATABASE", "ALREADY_IN_SHEET"]
    date: date
    merchant: str
    original_amount: float
    original_currency: str
    amount_pln: float | None = None
    suggested_category: str | None = None


@dataclass(slots=True)
class ChangeProposal:
    id: str
    action: Literal["ADD", "EDIT"]
    transaction_date: date
    category: str
    merchant: str
    amount_pln: float
    status: str = "Pending"
    source: str = "manual"
    source_transaction_id: str | None = None
    target: dict[str, Any] | None = None
    reason: str = ""
    confidence: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)
