from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any


class StateStore:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path)
        self.connection.row_factory = sqlite3.Row

    def initialize(self) -> None:
        self.connection.executescript(
            """
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS sync_cursors (
                account_id TEXT PRIMARY KEY,
                cursor_iso TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS sync_reconciliations (
                account_id TEXT PRIMARY KEY,
                completed_at TEXT NOT NULL,
                start_iso TEXT NOT NULL,
                end_iso TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS transactions (
                source_transaction_id TEXT PRIMARY KEY,
                fingerprint TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS merchant_rules (
                merchant_key TEXT NOT NULL,
                mcc INTEGER NOT NULL,
                category TEXT NOT NULL,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (merchant_key, mcc)
            );
            CREATE TABLE IF NOT EXISTS proposals (
                id TEXT PRIMARY KEY,
                action TEXT NOT NULL,
                source_transaction_id TEXT,
                payload_json TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS fx_rates (
                currency TEXT NOT NULL,
                requested_date TEXT NOT NULL,
                effective_date TEXT NOT NULL,
                rate REAL NOT NULL,
                PRIMARY KEY (currency, requested_date)
            );
            """
        )
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()

    def reserve_transaction(self, source_transaction_id: str, fingerprint: str) -> bool:
        try:
            with self.connection:
                self.connection.execute(
                    "INSERT INTO transactions(source_transaction_id, fingerprint) VALUES (?, ?)",
                    (source_transaction_id, fingerprint),
                )
            return True
        except sqlite3.IntegrityError:
            return False

    def release_transaction(self, source_transaction_id: str) -> None:
        with self.connection:
            self.connection.execute(
                "DELETE FROM transactions WHERE source_transaction_id = ?",
                (source_transaction_id,),
            )

    def get_cursor(self, account_id: str) -> datetime | None:
        row = self.connection.execute(
            "SELECT cursor_iso FROM sync_cursors WHERE account_id = ?", (account_id,)
        ).fetchone()
        return datetime.fromisoformat(row[0]) if row else None

    def set_cursor(self, account_id: str, cursor: datetime) -> None:
        with self.connection:
            self.connection.execute(
                "INSERT INTO sync_cursors(account_id, cursor_iso) VALUES (?, ?) "
                "ON CONFLICT(account_id) DO UPDATE SET cursor_iso = excluded.cursor_iso",
                (account_id, cursor.isoformat()),
            )

    def reconciliation_completed(self, account_id: str) -> bool:
        row = self.connection.execute(
            "SELECT 1 FROM sync_reconciliations WHERE account_id = ?", (account_id,)
        ).fetchone()
        return row is not None

    def mark_reconciliation_complete(
        self,
        account_id: str,
        *,
        start: datetime,
        end: datetime,
    ) -> None:
        with self.connection:
            self.connection.execute(
                "INSERT INTO sync_reconciliations(account_id, completed_at, start_iso, end_iso) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT(account_id) DO UPDATE SET "
                "completed_at = excluded.completed_at, start_iso = excluded.start_iso, end_iso = excluded.end_iso",
                (account_id, end.isoformat(), start.isoformat(), end.isoformat()),
            )

    @staticmethod
    def _merchant_key(merchant: str) -> str:
        return " ".join(merchant.casefold().split())

    def upsert_merchant_rule(self, merchant: str, mcc: int, category: str) -> None:
        with self.connection:
            self.connection.execute(
                "INSERT INTO merchant_rules(merchant_key, mcc, category) VALUES (?, ?, ?) "
                "ON CONFLICT(merchant_key, mcc) DO UPDATE SET category = excluded.category, updated_at = CURRENT_TIMESTAMP",
                (self._merchant_key(merchant), mcc, category),
            )

    def find_merchant_rule(self, merchant: str, mcc: int) -> str | None:
        row = self.connection.execute(
            "SELECT category FROM merchant_rules WHERE merchant_key = ? AND mcc = ?",
            (self._merchant_key(merchant), mcc),
        ).fetchone()
        return str(row[0]) if row else None

    def create_proposal(self, *, action: str, source_transaction_id: str | None, payload: dict[str, Any]) -> str:
        proposal_id = str(uuid.uuid4())
        self.record_proposal(
            proposal_id=proposal_id,
            action=action,
            source_transaction_id=source_transaction_id,
            payload=payload,
        )
        return proposal_id

    def record_proposal(
        self,
        *,
        proposal_id: str,
        action: str,
        source_transaction_id: str | None,
        payload: dict[str, Any],
        status: str = "Pending",
    ) -> None:
        with self.connection:
            self.connection.execute(
                "INSERT INTO proposals(id, action, source_transaction_id, payload_json, status) VALUES (?, ?, ?, ?, ?)",
                (proposal_id, action, source_transaction_id, json.dumps(payload, ensure_ascii=False, default=str), status),
            )

    def update_proposal_status_if_exists(self, proposal_id: str, status: str) -> bool:
        with self.connection:
            cursor = self.connection.execute(
                "UPDATE proposals SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (status, proposal_id),
            )
        return cursor.rowcount == 1

    def delete_proposal(self, proposal_id: str) -> None:
        with self.connection:
            self.connection.execute("DELETE FROM proposals WHERE id = ?", (proposal_id,))

    def set_proposal_status(self, proposal_id: str, status: str) -> None:
        with self.connection:
            cursor = self.connection.execute(
                "UPDATE proposals SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (status, proposal_id),
            )
        if cursor.rowcount != 1:
            raise KeyError(proposal_id)

    def get_proposal(self, proposal_id: str) -> dict[str, Any]:
        row = self.connection.execute("SELECT * FROM proposals WHERE id = ?", (proposal_id,)).fetchone()
        if row is None:
            raise KeyError(proposal_id)
        result = dict(row)
        result["payload"] = json.loads(result.pop("payload_json"))
        return result

    def cache_fx_rate(self, currency: str, requested: object, effective: object, rate: float) -> None:
        with self.connection:
            self.connection.execute(
                "INSERT OR REPLACE INTO fx_rates(currency, requested_date, effective_date, rate) VALUES (?, ?, ?, ?)",
                (currency, str(requested), str(effective), rate),
            )

    def get_fx_rate(self, currency: str, requested: object) -> tuple[float, str] | None:
        row = self.connection.execute(
            "SELECT rate, effective_date FROM fx_rates WHERE currency = ? AND requested_date = ?",
            (currency, str(requested)),
        ).fetchone()
        return (float(row[0]), str(row[1])) if row else None
