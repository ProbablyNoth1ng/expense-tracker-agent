from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Mapping


@dataclass(frozen=True, slots=True)
class Settings:
    spreadsheet_id: str = ""
    monobank_token: str = ""
    openai_api_key: str = ""
    openai_model: str = "gpt-5.4-mini"
    year: int = 2026
    initial_sync_date: date = date(2026, 7, 1)
    timezone: str = "Europe/Warsaw"
    schedule_time: str = "23:59"
    database_path: Path = Path("data/state.db")
    credentials_path: Path = Path("secrets/google_credentials.json")
    token_path: Path = Path("secrets/google_token.json")
    backup_dir: Path = Path("data/backups")
    log_dir: Path = Path("logs")

    @classmethod
    def from_mapping(cls, values: Mapping[str, str]) -> "Settings":
        def get(name: str, default: str) -> str:
            return values.get(name, default)

        return cls(
            spreadsheet_id=get("GOOGLE_SPREADSHEET_ID", ""),
            monobank_token=get("MONOBANK_TOKEN", ""),
            openai_api_key=get("OPENAI_API_KEY", ""),
            openai_model=get("OPENAI_MODEL", "gpt-5.4-mini"),
            database_path=Path(get("DATABASE_PATH", "data/state.db")),
            credentials_path=Path(get("GOOGLE_CREDENTIALS_PATH", "secrets/google_credentials.json")),
            token_path=Path(get("GOOGLE_TOKEN_PATH", "secrets/google_token.json")),
            backup_dir=Path(get("BACKUP_DIR", "data/backups")),
            log_dir=Path(get("LOG_DIR", "logs")),
        )

    @classmethod
    def from_env(cls) -> "Settings":
        try:
            from dotenv import load_dotenv
            load_dotenv()
        except ImportError:
            pass
        return cls.from_mapping(os.environ)

    def missing_live_settings(self) -> list[str]:
        missing: list[str] = []
        if not self.spreadsheet_id:
            missing.append("GOOGLE_SPREADSHEET_ID")
        if not self.monobank_token:
            missing.append("MONOBANK_TOKEN")
        if not self.openai_api_key:
            missing.append("OPENAI_API_KEY")
        if not self.credentials_path.exists():
            missing.append("GOOGLE_CREDENTIALS_PATH")
        return missing
