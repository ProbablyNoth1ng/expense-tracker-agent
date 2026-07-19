from __future__ import annotations

import uuid
from collections import Counter
from datetime import UTC, datetime, timedelta
from typing import Any, TypedDict
from zoneinfo import ZoneInfo

from .models import ChangeProposal, DetectedExpense
from .normalization import normalize_transaction


class SyncState(TypedDict, total=False):
    account_ids: list[str]
    now: datetime
    applied: dict[str, int]
    proposals: list[ChangeProposal]
    cursor_updates: list[tuple[str, datetime]]
    reconciliation_updates: list[tuple[str, datetime, datetime]]
    reserved: list[tuple[str, str]]
    new_count: int
    matched_existing_count: int
    already_in_database_count: int
    detected_expenses: list[DetectedExpense]
    incoming_count: int
    included_held_count: int


def _apply_node(services: Any, state: SyncState) -> SyncState:
    return {**state, "applied": services.sheets.apply_approved()}


def _fetch_node(services: Any, state: SyncState) -> SyncState:
    now = state.get("now") or datetime.now(tz=UTC)
    proposals: list[ChangeProposal] = []
    reserved: list[tuple[str, str]] = []
    cursor_updates: list[tuple[str, datetime]] = []
    reconciliation_updates: list[tuple[str, datetime, datetime]] = []
    zone = ZoneInfo(services.settings.timezone)
    statement_start = now - timedelta(days=31)
    account_starts: list[tuple[str, datetime, bool]] = []
    reconciliation_completed = getattr(services.store, "reconciliation_completed", None)
    for account_id in state["account_ids"]:
        completed = bool(reconciliation_completed(account_id)) if callable(reconciliation_completed) else True
        if not completed:
            reconciliation_updates.append((account_id, statement_start, now))
        account_starts.append((account_id, statement_start, completed))

    existing_expenses: Counter[tuple[str, str, int]] = Counter()
    if reconciliation_updates:
        earliest_start = min(start for _, start, _ in account_starts)
        existing_expenses = services.sheets.monthly_expense_counts(
            start=earliest_start.astimezone(zone).date(), end=now.astimezone(zone).date()
        )

    matched_existing_count = 0
    already_in_database_count = 0
    detected_expenses: list[DetectedExpense] = []
    incoming_count = 0
    included_held_count = 0
    try:
        for account_id, start, _ in account_starts:
            for raw in services.monobank.iter_statements([account_id], start, now):
                if raw.amount >= 0:
                    incoming_count += 1
                    continue
                if raw.hold:
                    included_held_count += 1
                normalized = normalize_transaction(raw, timezone=services.settings.timezone)
                if normalized is None:
                    continue
                if not services.store.reserve_transaction(
                    normalized.source_transaction_id, normalized.fingerprint
                ):
                    already_in_database_count += 1
                    detected_expenses.append(
                        DetectedExpense(
                            outcome="ALREADY_IN_DATABASE",
                            date=normalized.date,
                            merchant=normalized.merchant,
                            original_amount=normalized.original_amount,
                            original_currency=normalized.original_currency,
                        )
                    )
                    continue
                reserved.append((normalized.source_transaction_id, normalized.fingerprint))
                amount_pln, rate, effective_date = services.fx.convert(
                    normalized.original_amount,
                    normalized.original_currency,
                    normalized.date,
                )
                expense_key = services.sheets.expense_key(
                    normalized.date, normalized.merchant, amount_pln
                )
                if existing_expenses[expense_key] > 0:
                    existing_expenses[expense_key] -= 1
                    matched_existing_count += 1
                    detected_expenses.append(
                        DetectedExpense(
                            outcome="ALREADY_IN_SHEET",
                            date=normalized.date,
                            merchant=normalized.merchant,
                            original_amount=normalized.original_amount,
                            original_currency=normalized.original_currency,
                            amount_pln=amount_pln,
                        )
                    )
                    continue
                decision = services.categorizer.categorize(
                    normalized.merchant, normalized.mcc, amount_pln
                )
                proposals.append(
                    ChangeProposal(
                        id=str(uuid.uuid4()),
                        action="ADD",
                        transaction_date=normalized.date,
                        category=decision.category,
                        merchant=normalized.merchant,
                        amount_pln=amount_pln,
                        source="monobank",
                        source_transaction_id=normalized.source_transaction_id,
                        confidence=decision.confidence,
                        reason=decision.reason,
                        metadata={
                            "fingerprint": normalized.fingerprint,
                            "original_currency": normalized.original_currency,
                            "original_amount": normalized.original_amount,
                            "fx_rate": rate,
                            "fx_effective_date": effective_date.isoformat(),
                            "mcc": normalized.mcc,
                            "bank_hold": raw.hold,
                            "suggested_category": decision.category,
                        },
                    )
                )
                detected_expenses.append(
                    DetectedExpense(
                        outcome="NEW",
                        date=normalized.date,
                        merchant=normalized.merchant,
                        original_amount=normalized.original_amount,
                        original_currency=normalized.original_currency,
                        amount_pln=amount_pln,
                        suggested_category=decision.category,
                    )
                )
            cursor_updates.append((account_id, now))
    except Exception:
        release = getattr(services.store, "release_transaction", None)
        if callable(release):
            for source_id, _ in reserved:
                release(source_id)
        raise
    return {
        **state,
        "proposals": proposals,
        "reserved": reserved,
        "cursor_updates": cursor_updates,
        "reconciliation_updates": reconciliation_updates,
        "new_count": len(proposals),
        "matched_existing_count": matched_existing_count,
        "already_in_database_count": already_in_database_count,
        "detected_expenses": detected_expenses,
        "incoming_count": incoming_count,
        "included_held_count": included_held_count,
    }


def _stage_node(services: Any, state: SyncState) -> SyncState:
    recorded_proposal_ids: list[str] = []
    try:
        record = getattr(services.store, "record_proposal", None)
        if callable(record):
            for proposal in state.get("proposals", []):
                record(
                    proposal_id=proposal.id,
                    action=proposal.action,
                    source_transaction_id=proposal.source_transaction_id,
                    payload={
                        "date": proposal.transaction_date.isoformat(),
                        "category": proposal.category,
                        "merchant": proposal.merchant,
                        "amount_pln": proposal.amount_pln,
                        "metadata": proposal.metadata,
                    },
                )
                recorded_proposal_ids.append(proposal.id)
        services.sheets.stage_proposals(state.get("proposals", []))
    except Exception:
        delete_proposal = getattr(services.store, "delete_proposal", None)
        if callable(delete_proposal):
            for proposal_id in recorded_proposal_ids:
                delete_proposal(proposal_id)
        release = getattr(services.store, "release_transaction", None)
        if callable(release):
            for source_id, _ in state.get("reserved", []):
                release(source_id)
        raise
    for account_id, cursor in state.get("cursor_updates", []):
        services.store.set_cursor(account_id, cursor)
    mark_reconciliation = getattr(services.store, "mark_reconciliation_complete", None)
    if callable(mark_reconciliation):
        for account_id, start, end in state.get("reconciliation_updates", []):
            mark_reconciliation(account_id, start=start, end=end)
    services.notifier.notify(
        "Expense agent sync",
        "New: "
        f"{state.get('new_count', 0)}; existing: {state.get('matched_existing_count', 0)}; "
        f"synced: {state.get('applied', {}).get('synced', 0)}",
    )
    return state


class _FallbackGraph:
    def __init__(self, services: Any):
        self.services = services

    def invoke(self, state: SyncState) -> SyncState:
        state = _apply_node(self.services, state)
        state = _fetch_node(self.services, state)
        return _stage_node(self.services, state)


def build_sync_graph(services: Any) -> Any:
    try:
        from langgraph.graph import END, START, StateGraph
    except ImportError:
        return _FallbackGraph(services)
    graph = StateGraph(SyncState)
    graph.add_node("apply_approved", lambda state: _apply_node(services, state))
    graph.add_node("fetch_prepare", lambda state: _fetch_node(services, state))
    graph.add_node("stage_commit", lambda state: _stage_node(services, state))
    graph.add_edge(START, "apply_approved")
    graph.add_edge("apply_approved", "fetch_prepare")
    graph.add_edge("fetch_prepare", "stage_commit")
    graph.add_edge("stage_commit", END)
    return graph.compile()
