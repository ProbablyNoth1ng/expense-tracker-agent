from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from .backups import BackupService, make_drive_exporter
from .categorization import Categorizer, LangChainCategoryModel
from .config import Settings
from .fx import NbpClient, convert_to_pln
from .monobank import MonobankClient
from .notifications import default_notifier
from .sheets import SheetsGateway, build_google_services
from .storage import StateStore


class FxService:
    def __init__(self, client: NbpClient):
        self.client = client

    def convert(self, amount: float, currency: str, transaction_date: date) -> tuple[float, float, date]:
        return convert_to_pln(amount, currency, transaction_date, self.client)


@dataclass(slots=True)
class Services:
    settings: Settings
    store: StateStore
    monobank: MonobankClient
    fx: FxService
    categorizer: Categorizer
    sheets: SheetsGateway
    notifier: Any
    backup: BackupService
    account_config_path: Path

    def selected_accounts(self) -> list[str]:
        if not self.account_config_path.exists():
            raise RuntimeError("No Monobank accounts selected. Run: expense-agent setup")
        payload = json.loads(self.account_config_path.read_text(encoding="utf-8"))
        accounts = payload.get("selected_account_ids", [])
        if not accounts:
            raise RuntimeError("No Monobank accounts selected. Run: expense-agent setup")
        return [str(account) for account in accounts]

    def save_selected_accounts(self, account_ids: list[str]) -> None:
        self.account_config_path.parent.mkdir(parents=True, exist_ok=True)
        self.account_config_path.write_text(
            json.dumps({"selected_account_ids": account_ids}, indent=2), encoding="utf-8"
        )

    def close(self) -> None:
        self.store.close()


def build_services(settings: Settings) -> Services:
    missing = settings.missing_live_settings()
    if missing:
        raise RuntimeError(f"Missing configuration: {', '.join(missing)}")
    sheets_service, drive_service = build_google_services(settings.credentials_path, settings.token_path)
    store = StateStore(settings.database_path)
    store.initialize()
    sheets = SheetsGateway(service=sheets_service, spreadsheet_id=settings.spreadsheet_id, store=store)
    fx = FxService(NbpClient(store=store))
    categorizer = Categorizer(
        store=store,
        model=LangChainCategoryModel(api_key=settings.openai_api_key, model=settings.openai_model),
    )
    return Services(
        settings=settings,
        store=store,
        monobank=MonobankClient(token=settings.monobank_token),
        fx=fx,
        categorizer=categorizer,
        sheets=sheets,
        notifier=default_notifier(),
        backup=BackupService(directory=settings.backup_dir, exporter=make_drive_exporter(drive_service)),
        account_config_path=settings.database_path.parent / "accounts.json",
    )
