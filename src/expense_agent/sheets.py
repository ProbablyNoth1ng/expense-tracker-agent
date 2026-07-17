from __future__ import annotations

import copy
import json
import logging
from datetime import date
from pathlib import Path
from typing import AbstractSet, Any, Iterable

from .constants import CATEGORIES, MONTH_SHEETS, REVIEW_STATUSES
from .models import ChangeProposal

logger = logging.getLogger(__name__)

REVIEW_HEADERS = (
    "Status",
    "Action",
    "Date",
    "Category",
    "Shop",
    "Amount PLN",
    "Source",
    "Confidence",
    "Reason",
    "User Note",
    "Proposal ID",
    "Source Transaction ID",
    "Fingerprint",
    "Target JSON",
)

AGENT_LOG_HEADERS = (
    "Timestamp",
    "Proposal ID",
    "Action",
    "Source Transaction ID",
    "Target Sheet",
    "Target Row",
    "Result",
)


def validate_template_metadata(titles: Iterable[str]) -> None:
    available = set(titles)
    required = {"Podsumowanie", *MONTH_SHEETS.values()}
    missing = sorted(required - available)
    if missing:
        raise ValueError(f"Template is missing sheets: {', '.join(missing)}")


def proposal_to_review_row(proposal: ChangeProposal) -> list[Any]:
    target_payload = dict(proposal.target or {})
    target_payload["_metadata"] = proposal.metadata
    return [
        proposal.status,
        proposal.action,
        proposal.transaction_date.isoformat(),
        proposal.category,
        proposal.merchant,
        proposal.amount_pln,
        proposal.source,
        proposal.confidence,
        proposal.reason,
        "",
        proposal.id,
        proposal.source_transaction_id or "",
        proposal.metadata.get("fingerprint", ""),
        json.dumps(target_payload, ensure_ascii=False),
    ]


def extend_chart_ranges(
    spec: dict[str, Any],
    *,
    sheet_id: int,
    category_end_row: int,
    allowed_columns: AbstractSet[int],
) -> dict[str, Any]:
    result = copy.deepcopy(spec)

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            start_column = value.get("startColumnIndex")
            end_column = value.get("endColumnIndex")
            start_row = value.get("startRowIndex")
            end_row = value.get("endRowIndex")
            if (
                value.get("sheetId") == sheet_id
                and isinstance(start_column, int)
                and start_column in allowed_columns
                and end_column == start_column + 1
                and start_row == 3
                and isinstance(end_row, int)
                and 3 < end_row < category_end_row
            ):
                value["endRowIndex"] = category_end_row
            for nested in value.values():
                visit(nested)
        elif isinstance(value, list):
            for nested in value:
                visit(nested)

    visit(result)
    return result


class SheetsGateway:
    def __init__(self, *, service: Any, spreadsheet_id: str, store: Any | None = None):
        self.service = service
        self.spreadsheet_id = spreadsheet_id
        self.store = store

    def metadata(self) -> dict[str, Any]:
        return self.service.spreadsheets().get(
            spreadsheetId=self.spreadsheet_id,
            includeGridData=False,
        ).execute()

    def validate_template(self) -> dict[str, Any]:
        metadata = self.metadata()
        validate_template_metadata(sheet["properties"]["title"] for sheet in metadata.get("sheets", []))
        return metadata

    def _values(self) -> Any:
        return self.service.spreadsheets().values()

    @staticmethod
    def _review_sort_request(sheet_id: int) -> dict[str, Any]:
        return {
            "sortRange": {
                "range": {
                    "sheetId": sheet_id,
                    "startRowIndex": 1,
                    "startColumnIndex": 0,
                    "endColumnIndex": len(REVIEW_HEADERS),
                },
                "sortSpecs": [{"dimensionIndex": 2, "sortOrder": "ASCENDING"}],
            }
        }

    def _sort_review_by_date(self, *, sheet_id: int | None = None) -> None:
        if sheet_id is None:
            metadata = self.metadata()
            review = next(
                (
                    sheet
                    for sheet in metadata.get("sheets", [])
                    if sheet["properties"]["title"] == "Review"
                ),
                None,
            )
            if review is None:
                raise ValueError("Template is missing sheet: Review")
            sheet_id = int(review["properties"]["sheetId"])
        self.service.spreadsheets().batchUpdate(
            spreadsheetId=self.spreadsheet_id,
            body={"requests": [self._review_sort_request(sheet_id)]},
        ).execute()

    def migrate_template(self) -> None:
        metadata = self.validate_template()
        title_to_sheet = {sheet["properties"]["title"]: sheet for sheet in metadata["sheets"]}
        requests: list[dict[str, Any]] = []
        if "Review" not in title_to_sheet:
            requests.append({"addSheet": {"properties": {"title": "Review", "gridProperties": {"rowCount": 2000, "columnCount": len(REVIEW_HEADERS)}}}})
        if "Agent Log" not in title_to_sheet:
            requests.append({"addSheet": {"properties": {"title": "Agent Log", "hidden": True, "gridProperties": {"rowCount": 5000, "columnCount": len(AGENT_LOG_HEADERS)}}}})
        if requests:
            self.service.spreadsheets().batchUpdate(
                spreadsheetId=self.spreadsheet_id, body={"requests": requests}
            ).execute()

        updates = [
            {"range": "Review!A1:N1", "values": [list(REVIEW_HEADERS)]},
            {"range": "'Agent Log'!A1:G1", "values": [list(AGENT_LOG_HEADERS)]},
        ]
        for title in MONTH_SHEETS.values():
            category_rows = [[category] for category in CATEGORIES]
            formulas = [[f'=IFERROR(SUMIF(B$4:B,F{row},D$4:D),0)'] for row in range(4, 16)]
            updates.extend(
                [
                    {"range": f"'{title}'!F4:F15", "values": category_rows},
                    {"range": f"'{title}'!G4:G15", "values": formulas},
                    {"range": f"'{title}'!F16:G16", "values": [["💰  RAZEM", "=SUM(G4:G15)"]]},
                    {"range": f"'{title}'!D2", "values": [["=SUM(D4:D)"]]},
                ]
            )
        summary_categories = [[category] for category in CATEGORIES]
        updates.append({"range": "Podsumowanie!A4:A15", "values": summary_categories})
        for row in range(4, 16):
            formulas = [f"=IFERROR('{MONTH_SHEETS[month]}'!G{row},0)" for month in range(1, 13)]
            formulas.append(f"=SUM(B{row}:M{row})")
            updates.append({"range": f"Podsumowanie!B{row}:N{row}", "values": [formulas]})
        updates.append({"range": "Podsumowanie!A16:N16", "values": [["💰  ŁĄCZNIE W MIESIĄCU", *[f"=SUM({column}4:{column}15)" for column in "BCDEFGHIJKLM"], "=SUM(N4:N15)"]]})
        self._values().batchUpdate(
            spreadsheetId=self.spreadsheet_id,
            body={"valueInputOption": "USER_ENTERED", "data": updates},
        ).execute()

        refreshed = self.metadata()
        requests = []
        for sheet in refreshed.get("sheets", []):
            title = sheet["properties"]["title"]
            sheet_id = sheet["properties"]["sheetId"]
            chart_columns: AbstractSet[int] = frozenset()
            if title in MONTH_SHEETS.values():
                chart_columns = frozenset({5, 6})
                requests.append(
                    {
                        "setDataValidation": {
                            "range": {"sheetId": sheet_id, "startRowIndex": 3, "startColumnIndex": 1, "endColumnIndex": 2},
                            "rule": {"condition": {"type": "ONE_OF_LIST", "values": [{"userEnteredValue": c} for c in CATEGORIES]}, "strict": True, "showCustomUi": True},
                        }
                    }
                )
            elif title == "Podsumowanie":
                chart_columns = frozenset({0, 13})
            if title == "Review":
                requests.extend(
                    [
                        {"setDataValidation": {"range": {"sheetId": sheet_id, "startRowIndex": 1, "startColumnIndex": 0, "endColumnIndex": 1}, "rule": {"condition": {"type": "ONE_OF_LIST", "values": [{"userEnteredValue": s} for s in REVIEW_STATUSES]}, "strict": True, "showCustomUi": True}}},
                        {"setDataValidation": {"range": {"sheetId": sheet_id, "startRowIndex": 1, "startColumnIndex": 3, "endColumnIndex": 4}, "rule": {"condition": {"type": "ONE_OF_LIST", "values": [{"userEnteredValue": c} for c in CATEGORIES]}, "strict": True, "showCustomUi": True}}},
                        {"updateDimensionProperties": {"range": {"sheetId": sheet_id, "dimension": "COLUMNS", "startIndex": 10, "endIndex": 14}, "properties": {"hiddenByUser": True}, "fields": "hiddenByUser"}},
                        {"updateSheetProperties": {"properties": {"sheetId": sheet_id, "gridProperties": {"frozenRowCount": 1}}, "fields": "gridProperties.frozenRowCount"}},
                        {"repeatCell": {"range": {"sheetId": sheet_id, "startRowIndex": 0, "endRowIndex": 1, "startColumnIndex": 0, "endColumnIndex": 14}, "cell": {"userEnteredFormat": {"backgroundColor": {"red": 0.08, "green": 0.34, "blue": 0.64}, "textFormat": {"foregroundColor": {"red": 1, "green": 1, "blue": 1}, "bold": True}}}, "fields": "userEnteredFormat(backgroundColor,textFormat)"}},
                        self._review_sort_request(sheet_id),
                    ]
                )
            for chart in sheet.get("charts", []):
                spec = extend_chart_ranges(
                    chart["spec"],
                    sheet_id=sheet_id,
                    category_end_row=15,
                    allowed_columns=chart_columns,
                )
                if spec != chart["spec"]:
                    requests.append({"updateChartSpec": {"chartId": chart["chartId"], "spec": spec}})
        if requests:
            self.service.spreadsheets().batchUpdate(
                spreadsheetId=self.spreadsheet_id, body={"requests": requests}
            ).execute()

    def stage_proposals(self, proposals: list[ChangeProposal]) -> None:
        if proposals:
            self._values().append(
                spreadsheetId=self.spreadsheet_id,
                range="Review!A:N",
                valueInputOption="USER_ENTERED",
                insertDataOption="INSERT_ROWS",
                body={"values": [proposal_to_review_row(proposal) for proposal in proposals]},
            ).execute()
        try:
            self._sort_review_by_date()
        except Exception:
            logger.warning(
                "Review date sorting failed; staged rows remain saved and the next staging run will retry",
                exc_info=True,
            )

    def read_review_rows(self) -> list[list[Any]]:
        result = self._values().get(
            spreadsheetId=self.spreadsheet_id, range="Review!A2:N"
        ).execute()
        return result.get("values", [])

    def apply_approved(self) -> dict[str, int]:
        synced = conflicts = errors = 0
        for index, row in enumerate(self.read_review_rows(), start=2):
            padded = list(row) + [""] * (len(REVIEW_HEADERS) - len(row))
            if padded[0] != "Approved":
                continue
            target_sheet = ""
            target_row: int | str = ""
            try:
                transaction_date = date.fromisoformat(str(padded[2]))
                if transaction_date.year != 2026:
                    raise ValueError("Only 2026 is supported")
                action = str(padded[1])
                values = [[transaction_date.isoformat(), padded[3], padded[4], float(padded[5])]]
                if action == "ADD":
                    target_sheet = MONTH_SHEETS[transaction_date.month]
                    self._values().append(
                        spreadsheetId=self.spreadsheet_id,
                        range=f"'{target_sheet}'!A4:D",
                        valueInputOption="USER_ENTERED",
                        insertDataOption="INSERT_ROWS",
                        body={"values": values},
                    ).execute()
                elif action == "EDIT":
                    target = json.loads(padded[13] or "{}")
                    target_sheet = str(target["sheet"])
                    target_row = int(target["row"])
                    current = self._values().get(
                        spreadsheetId=self.spreadsheet_id,
                        range=f"'{target_sheet}'!A{target_row}:D{target_row}",
                    ).execute().get("values", [[]])
                    expected = target.get("expected")
                    if expected is not None and (not current or current[0] != expected):
                        self._set_review_status(index, "Conflict")
                        self._append_agent_log(padded, target_sheet, target_row, "Conflict")
                        conflicts += 1
                        continue
                    self._values().update(
                        spreadsheetId=self.spreadsheet_id,
                        range=f"'{target_sheet}'!A{target_row}:D{target_row}",
                        valueInputOption="USER_ENTERED",
                        body={"values": values},
                    ).execute()
                else:
                    raise ValueError("Only ADD and EDIT are supported")
                self._set_review_status(index, "Synced")
                self._learn_from_review_row(padded)
                self._append_agent_log(padded, target_sheet, target_row, "Synced")
                synced += 1
            except Exception:
                self._set_review_status(index, "Error")
                self._append_agent_log(padded, target_sheet, target_row, "Error")
                errors += 1
        return {"synced": synced, "conflicts": conflicts, "errors": errors}

    def _learn_from_review_row(self, row: list[Any]) -> None:
        if self.store is None or str(row[6]) != "monobank":
            return
        payload = json.loads(row[13] or "{}")
        metadata = payload.get("_metadata", {})
        mcc = metadata.get("mcc")
        if mcc is not None:
            self.store.upsert_merchant_rule(str(row[4]), int(mcc), str(row[3]))

    def _append_agent_log(self, row: list[Any], target_sheet: str, target_row: object, result: str) -> None:
        try:
            from datetime import datetime

            self._values().append(
                spreadsheetId=self.spreadsheet_id,
                range="'Agent Log'!A:G",
                valueInputOption="RAW",
                insertDataOption="INSERT_ROWS",
                body={"values": [[datetime.now().astimezone().isoformat(), row[10], row[1], row[11], target_sheet, target_row, result]]},
            ).execute()
        except Exception:
            return

    def _set_review_status(self, row: int, status: str) -> None:
        self._values().update(
            spreadsheetId=self.spreadsheet_id,
            range=f"Review!A{row}",
            valueInputOption="RAW",
            body={"values": [[status]]},
        ).execute()

    def find_expense_candidates(self, query: str) -> list[dict[str, Any]]:
        candidates: list[dict[str, Any]] = []
        needle = query.casefold()
        for title in MONTH_SHEETS.values():
            values = self._values().get(
                spreadsheetId=self.spreadsheet_id, range=f"'{title}'!A4:D"
            ).execute().get("values", [])
            for offset, row in enumerate(values, start=4):
                if needle in " ".join(str(cell) for cell in row).casefold():
                    candidates.append({"sheet": title, "row": offset, "values": row})
        return candidates


def build_google_services(credentials_path: Path, token_path: Path) -> tuple[Any, Any]:
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build
    except ImportError as exc:
        raise RuntimeError("Install project dependencies with: pip install -e .") from exc
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive.readonly",
    ]
    credentials = None
    if token_path.exists():
        credentials = Credentials.from_authorized_user_file(str(token_path), scopes)
    if credentials is None or not credentials.valid:
        if credentials and credentials.expired and credentials.refresh_token:
            credentials.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(str(credentials_path), scopes)
            credentials = flow.run_local_server(port=0)
        token_path.parent.mkdir(parents=True, exist_ok=True)
        token_path.write_text(credentials.to_json(), encoding="utf-8")
    return build("sheets", "v4", credentials=credentials), build("drive", "v3", credentials=credentials)
