from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Iterable


class RedactingJsonFormatter(logging.Formatter):
    def __init__(self, secrets: Iterable[str]):
        super().__init__()
        self.secrets = tuple(secret for secret in secrets if secret)

    def format(self, record: logging.LogRecord) -> str:
        message = record.getMessage()
        for secret in self.secrets:
            message = message.replace(secret, "[REDACTED]")
        return json.dumps(
            {
                "timestamp": datetime.now(tz=UTC).isoformat(),
                "level": record.levelname,
                "logger": record.name,
                "message": message,
            },
            ensure_ascii=False,
        )


def configure_logging(directory: Path, *, secrets: Iterable[str]) -> logging.Logger:
    directory.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("expense_agent")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    for handler in logger.handlers[:]:
        handler.close()
        logger.removeHandler(handler)
    handler = RotatingFileHandler(
        directory / "expense-agent.log",
        maxBytes=1_000_000,
        backupCount=5,
        encoding="utf-8",
    )
    handler.setFormatter(RedactingJsonFormatter(secrets))
    logger.addHandler(handler)
    return logger

