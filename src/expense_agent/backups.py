from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Callable


class BackupService:
    def __init__(self, *, directory: Path, exporter: Callable[[str], bytes]):
        self.directory = directory
        self.exporter = exporter

    def create(self, spreadsheet_id: str, timestamp: datetime | None = None) -> Path:
        timestamp = timestamp or datetime.now().astimezone()
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.directory / f"expenses-{timestamp.strftime('%Y%m%d-%H%M%S')}.xlsx"
        path.write_bytes(self.exporter(spreadsheet_id))
        self.rotate(keep=10)
        return path

    def rotate(self, *, keep: int) -> None:
        files = sorted(self.directory.glob("expenses-*.xlsx"), key=lambda item: item.name)
        for path in files[:-keep]:
            path.unlink()


def make_drive_exporter(drive_service: object) -> Callable[[str], bytes]:
    def export(spreadsheet_id: str) -> bytes:
        request = drive_service.files().export_media(
            fileId=spreadsheet_id,
            mimeType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        return bytes(request.execute())

    return export

