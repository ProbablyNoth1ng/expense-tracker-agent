import tempfile
import unittest
from datetime import UTC, date, datetime
from pathlib import Path
from unittest.mock import Mock

from expense_agent.backups import BackupService
from expense_agent.models import ChangeProposal
from expense_agent.monobank import MonobankClient, statement_windows
from expense_agent.constants import MONTH_SHEETS
from expense_agent.sheets import (
    REVIEW_HEADERS,
    SheetsGateway,
    extend_chart_ranges,
    proposal_to_review_row,
    validate_template_metadata,
)


class MonobankTests(unittest.TestCase):
    def test_statement_windows_never_exceed_31_days(self):
        start = datetime(2026, 7, 1, tzinfo=UTC)
        end = datetime(2026, 9, 15, tzinfo=UTC)
        windows = list(statement_windows(start, end))
        self.assertEqual(windows[0][0], start)
        self.assertEqual(windows[-1][1], end)
        self.assertTrue(all((to - frm).total_seconds() <= 31 * 86400 for frm, to in windows))

    def test_client_uses_token_and_spaces_statement_calls(self):
        transport = Mock()
        transport.get.side_effect = [
            Mock(status_code=200, json=lambda: {"accounts": [{"id": "a", "type": "black", "currencyCode": 980}]}),
            Mock(status_code=200, json=lambda: [{
                "id": "t1", "time": 1782907200, "description": "Lidl", "mcc": 5411,
                "amount": -1000, "operationAmount": -1000, "currencyCode": 985, "hold": False,
            }]),
            Mock(status_code=200, json=lambda: []),
        ]
        sleeper = Mock()
        client = MonobankClient(token="secret", transport=transport, sleeper=sleeper, minimum_interval=60)
        accounts = client.list_accounts()
        self.assertEqual(accounts[0]["id"], "a")
        list(client.iter_statements(["a"], datetime(2026, 7, 1, tzinfo=UTC), datetime(2026, 9, 1, tzinfo=UTC)))
        self.assertTrue(all(c.kwargs["headers"] == {"X-Token": "secret"} for c in transport.get.call_args_list))
        self.assertGreaterEqual(sleeper.call_count, 2)


class SheetsTests(unittest.TestCase):
    def test_template_requires_summary_and_all_months(self):
        titles = ["Podsumowanie", "Styczeń"]
        with self.assertRaisesRegex(ValueError, "missing sheets"):
            validate_template_metadata(titles)

    def test_review_row_contains_visible_and_idempotency_fields(self):
        proposal = ChangeProposal(
            id="p1", action="ADD", transaction_date=date(2026, 7, 2), category="Еда и продукты",
            merchant="Lidl", amount_pln=12.34, source="monobank", source_transaction_id="t1",
            confidence=0.95, reason="MCC 5411", metadata={"fingerprint": "fp", "mcc": 5411},
        )
        row = proposal_to_review_row(proposal)
        self.assertEqual(len(row), len(REVIEW_HEADERS))
        self.assertEqual(row[0], "Pending")
        self.assertIn("t1", row)
        self.assertIn("fp", row)
        self.assertIn("5411", row[-1])

    def test_staging_appends_then_sorts_review_by_date_ascending(self):
        service = Mock()
        spreadsheets = service.spreadsheets.return_value
        values = spreadsheets.values.return_value
        events = []
        values.append.return_value.execute.side_effect = lambda: events.append("append")
        spreadsheets.get.return_value.execute.side_effect = lambda: (
            events.append("metadata") or {"sheets": [{"properties": {"title": "Review", "sheetId": 20}}]}
        )
        spreadsheets.batchUpdate.return_value.execute.side_effect = lambda: events.append("sort")
        proposal = ChangeProposal(
            id="p1", action="ADD", transaction_date=date(2026, 7, 17),
            category="Ð•Ð´Ð° Ð¸ Ð¿Ñ€Ð¾Ð´ÑƒÐºÑ‚Ñ‹", merchant="Lidl", amount_pln=12.34,
            source="chat", confidence=0.9, reason="chat",
        )

        SheetsGateway(service=service, spreadsheet_id="sheet-id").stage_proposals([proposal])

        self.assertEqual(events, ["append", "metadata", "sort"])
        request = spreadsheets.batchUpdate.call_args.kwargs["body"]["requests"][0]["sortRange"]
        self.assertEqual(
            request["range"],
            {"sheetId": 20, "startRowIndex": 1, "startColumnIndex": 0, "endColumnIndex": 14},
        )
        self.assertEqual(
            request["sortSpecs"],
            [{"dimensionIndex": 2, "sortOrder": "ASCENDING"}],
        )

    def test_empty_staging_retries_review_sort_without_appending(self):
        service = Mock()
        spreadsheets = service.spreadsheets.return_value
        spreadsheets.get.return_value.execute.return_value = {
            "sheets": [{"properties": {"title": "Review", "sheetId": 20}}]
        }

        SheetsGateway(service=service, spreadsheet_id="sheet-id").stage_proposals([])

        spreadsheets.values.return_value.append.assert_not_called()
        spreadsheets.batchUpdate.assert_called_once()

    def test_sort_failure_does_not_report_staged_proposal_as_failed(self):
        service = Mock()
        spreadsheets = service.spreadsheets.return_value
        spreadsheets.get.return_value.execute.return_value = {
            "sheets": [{"properties": {"title": "Review", "sheetId": 20}}]
        }
        spreadsheets.batchUpdate.return_value.execute.side_effect = RuntimeError("sort unavailable")
        proposal = ChangeProposal(
            id="p1", action="ADD", transaction_date=date(2026, 7, 17),
            category="Ð•Ð´Ð° Ð¸ Ð¿Ñ€Ð¾Ð´ÑƒÐºÑ‚Ñ‹", merchant="Lidl", amount_pln=12.34,
            source="chat", confidence=0.9, reason="chat",
        )

        with self.assertLogs("expense_agent.sheets", level="WARNING"):
            SheetsGateway(service=service, spreadsheet_id="sheet-id").stage_proposals([proposal])

        spreadsheets.values.return_value.append.assert_called_once()

    def test_chart_ranges_extend_only_eligible_single_columns_from_row_four(self):
        spec = {
            "basicChart": {
                "domains": [{"domain": {"sourceRange": {"sources": [
                    {"sheetId": 1, "startRowIndex": 3, "endRowIndex": 11, "startColumnIndex": 5, "endColumnIndex": 6},
                    {"sheetId": 1, "startRowIndex": 2, "endRowIndex": 3, "startColumnIndex": 0, "endColumnIndex": 13},
                ]}}}],
                "series": [{"series": {"sourceRange": {"sources": [
                    {"sheetId": 1, "startRowIndex": 3, "endRowIndex": 11, "startColumnIndex": 6, "endColumnIndex": 7},
                    {"sheetId": 1, "startRowIndex": 3, "endRowIndex": 34, "startColumnIndex": 8, "endColumnIndex": 9},
                    {"sheetId": 2, "startRowIndex": 3, "endRowIndex": 11, "startColumnIndex": 5, "endColumnIndex": 6},
                ]}}}],
            }
        }
        original = {"basicChart": spec["basicChart"].copy()}

        result = extend_chart_ranges(
            spec,
            sheet_id=1,
            category_end_row=15,
            allowed_columns={5, 6},
        )
        domains = result["basicChart"]["domains"][0]["domain"]["sourceRange"]["sources"]
        series = result["basicChart"]["series"][0]["series"]["sourceRange"]["sources"]
        self.assertEqual(domains[0]["endRowIndex"], 15)
        self.assertEqual(series[0]["endRowIndex"], 15)
        self.assertEqual(domains[1]["endRowIndex"], 3)
        self.assertEqual(series[1]["endRowIndex"], 34)
        self.assertEqual(series[2]["endRowIndex"], 11)
        self.assertEqual(spec["basicChart"]["domains"][0]["domain"]["sourceRange"]["sources"][0]["endRowIndex"], 11)
        self.assertEqual(original["basicChart"], spec["basicChart"])

    def test_summary_chart_extends_both_category_columns_but_not_horizontal_range(self):
        spec = {
            "ranges": [
                {"sheetId": 1, "startRowIndex": 2, "endRowIndex": 3, "startColumnIndex": 0, "endColumnIndex": 13},
                {"sheetId": 1, "startRowIndex": 3, "endRowIndex": 13, "startColumnIndex": 0, "endColumnIndex": 1},
                {"sheetId": 1, "startRowIndex": 3, "endRowIndex": 13, "startColumnIndex": 13, "endColumnIndex": 14},
            ]
        }
        result = extend_chart_ranges(
            spec,
            sheet_id=1,
            category_end_row=15,
            allowed_columns={0, 13},
        )
        self.assertEqual([item["endRowIndex"] for item in result["ranges"]], [3, 15, 15])

    def test_migration_updates_only_intended_chart_specs(self):
        summary_horizontal = {"basicChart": {"domains": [{"domain": {"sourceRange": {"sources": [
            {"sheetId": 1, "startRowIndex": 2, "endRowIndex": 3, "startColumnIndex": 0, "endColumnIndex": 13}
        ]}}}]}}
        summary_categories = {"pieChart": {
            "domain": {"sourceRange": {"sources": [
                {"sheetId": 1, "startRowIndex": 3, "endRowIndex": 13, "startColumnIndex": 0, "endColumnIndex": 1}
            ]}},
            "series": {"sourceRange": {"sources": [
                {"sheetId": 1, "startRowIndex": 3, "endRowIndex": 13, "startColumnIndex": 13, "endColumnIndex": 14}
            ]}},
        }}
        monthly_categories = {"basicChart": {
            "domains": [{"domain": {"sourceRange": {"sources": [
                {"sheetId": 2, "startRowIndex": 3, "endRowIndex": 11, "startColumnIndex": 5, "endColumnIndex": 6}
            ]}}}],
            "series": [{"series": {"sourceRange": {"sources": [
                {"sheetId": 2, "startRowIndex": 3, "endRowIndex": 11, "startColumnIndex": 6, "endColumnIndex": 7}
            ]}}}],
        }}
        monthly_unrelated = {"basicChart": {
            "domains": [{"domain": {"sourceRange": {"sources": [
                {"sheetId": 2, "startRowIndex": 3, "endRowIndex": 34, "startColumnIndex": 8, "endColumnIndex": 9}
            ]}}}],
        }}
        sheets = [
            {"properties": {"title": "Podsumowanie", "sheetId": 1}, "charts": [
                {"chartId": 10, "spec": summary_horizontal},
                {"chartId": 11, "spec": summary_categories},
            ]},
            {"properties": {"title": MONTH_SHEETS[1], "sheetId": 2}, "charts": [
                {"chartId": 20, "spec": monthly_categories},
                {"chartId": 21, "spec": monthly_unrelated},
            ]},
        ]
        sheets.extend(
            {"properties": {"title": title, "sheetId": index}}
            for index, title in enumerate(list(MONTH_SHEETS.values())[1:], start=3)
        )
        sheets.extend([
            {"properties": {"title": "Review", "sheetId": 20}},
            {"properties": {"title": "Agent Log", "sheetId": 21}},
        ])
        metadata = {"sheets": sheets}
        service = Mock()
        spreadsheets = service.spreadsheets.return_value
        spreadsheets.get.return_value.execute.side_effect = [metadata, metadata]

        SheetsGateway(service=service, spreadsheet_id="sheet-id").migrate_template()

        requests = spreadsheets.batchUpdate.call_args.kwargs["body"]["requests"]
        sort_requests = [request["sortRange"] for request in requests if "sortRange" in request]
        self.assertEqual(len(sort_requests), 1)
        self.assertEqual(sort_requests[0]["range"]["startRowIndex"], 1)
        self.assertEqual(sort_requests[0]["range"]["endColumnIndex"], 14)
        self.assertEqual(sort_requests[0]["sortSpecs"], [{"dimensionIndex": 2, "sortOrder": "ASCENDING"}])
        chart_requests = [request["updateChartSpec"] for request in requests if "updateChartSpec" in request]
        self.assertEqual([request["chartId"] for request in chart_requests], [11, 20])
        summary_ranges = chart_requests[0]["spec"]["pieChart"]
        self.assertEqual(summary_ranges["domain"]["sourceRange"]["sources"][0]["endRowIndex"], 15)
        self.assertEqual(summary_ranges["series"]["sourceRange"]["sources"][0]["endRowIndex"], 15)
        monthly = chart_requests[1]["spec"]["basicChart"]
        self.assertEqual(monthly["domains"][0]["domain"]["sourceRange"]["sources"][0]["endRowIndex"], 15)
        self.assertEqual(monthly["series"][0]["series"]["sourceRange"]["sources"][0]["endRowIndex"], 15)


class BackupTests(unittest.TestCase):
    def test_backup_rotation_keeps_newest_ten(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            for index in range(12):
                path = directory / f"expenses-20260701-0000{index:02d}.xlsx"
                path.write_bytes(str(index).encode())
            service = BackupService(directory=directory, exporter=Mock())
            service.rotate(keep=10)
            self.assertEqual(len(list(directory.glob("*.xlsx"))), 10)
            self.assertFalse((directory / "expenses-20260701-000000.xlsx").exists())

    def test_backup_writes_exported_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            exporter = Mock(return_value=b"xlsx")
            service = BackupService(directory=Path(tmp), exporter=exporter)
            path = service.create("sheet-id", datetime(2026, 7, 16, 20, 30, tzinfo=UTC))
            self.assertEqual(path.read_bytes(), b"xlsx")
            exporter.assert_called_once_with("sheet-id")


if __name__ == "__main__":
    unittest.main()
