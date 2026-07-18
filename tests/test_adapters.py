import tempfile
import unittest
from datetime import UTC, date, datetime
from pathlib import Path
from unittest.mock import Mock

from expense_agent.backups import BackupService
from expense_agent.models import ChangeProposal
from expense_agent.monobank import MonobankClient, statement_windows
from expense_agent.constants import CATEGORIES, MONTH_SHEETS
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
    @staticmethod
    def _migration_metadata(*, total_row: int = 16):
        sheets = []
        for sheet_id, title in enumerate(MONTH_SHEETS.values(), start=2):
            sheets.append({
                "properties": {"title": title, "sheetId": sheet_id},
                "data": [{
                    "startRow": total_row - 1,
                    "startColumn": 5,
                    "rowData": [{"values": [{"userEnteredValue": {"stringValue": "RAZEM"}}]}],
                }],
            })
        sheets.insert(0, {
            "properties": {"title": "Podsumowanie", "sheetId": 1},
            "data": [{
                "startRow": total_row - 1,
                "startColumn": 0,
                "rowData": [{"values": [{"userEnteredValue": {"stringValue": "ŁĄCZNIE"}}]}],
            }],
        })
        sheets.extend([
            {"properties": {"title": "Review", "sheetId": 20}},
            {"properties": {"title": "Agent Log", "sheetId": 21}},
        ])
        return {"sheets": sheets}

    def _migrate_with_metadata(self, metadata):
        service = Mock()
        spreadsheets = service.spreadsheets.return_value
        spreadsheets.get.return_value.execute.side_effect = [metadata, metadata, metadata]
        SheetsGateway(service=service, spreadsheet_id="sheet-id").migrate_template()
        return spreadsheets

    def test_migration_uses_dynamic_category_ranges_and_locale_formulas(self):
        spreadsheets = self._migrate_with_metadata(self._migration_metadata())
        value_call = next(
            call for call in spreadsheets.values.return_value.batchUpdate.call_args_list
            if "data" in call.kwargs["body"]
        )
        updates = value_call.kwargs["body"]["data"]
        by_range = {update["range"]: update["values"] for update in updates}
        january = MONTH_SHEETS[1]

        self.assertEqual(by_range[f"'{january}'!F4:F17"], [[category] for category in CATEGORIES])
        self.assertEqual(
            by_range[f"'{january}'!G4:G17"],
            [[f"=IFERROR(SUMIF(B$4:B;F{row};D$4:D);0)"] for row in range(4, 18)],
        )
        self.assertEqual(by_range[f"'{january}'!F18:G18"][0][1], "=SUM(G4:G17)")
        self.assertEqual(by_range["Podsumowanie!A4:A17"], [[category] for category in CATEGORIES])
        self.assertEqual(
            by_range["Podsumowanie!B4:N4"][0],
            [f"=IFERROR('{MONTH_SHEETS[month]}'!G4;0)" for month in range(1, 13)]
            + ["=SUM(B4:M4)"],
        )
        self.assertEqual(
            by_range["Podsumowanie!A18:N18"][0],
            ["💰  ŁĄCZNIE W MIESIĄCU"]
            + [f"=SUM({column}4:{column}17)" for column in "BCDEFGHIJKLM"]
            + ["=SUM(N4:N17)"],
        )

        request_call = next(
            call for call in spreadsheets.batchUpdate.call_args_list
            if any(
                "setDataValidation" in request
                for request in call.kwargs["body"].get("requests", [])
            )
        )
        validations = [
            request["setDataValidation"] for request in request_call.kwargs["body"]["requests"]
            if "setDataValidation" in request
        ]
        category_values = [{"userEnteredValue": category} for category in CATEGORIES]
        self.assertEqual(
            next(item for item in validations if item["range"]["sheetId"] == 2 and item["range"]["startColumnIndex"] == 1)["rule"]["condition"]["values"],
            category_values,
        )
        self.assertEqual(
            next(item for item in validations if item["range"]["sheetId"] == 20 and item["range"]["startColumnIndex"] == 3)["rule"]["condition"]["values"],
            category_values,
        )

    def test_migration_copies_existing_formats_to_new_category_and_total_rows(self):
        spreadsheets = self._migrate_with_metadata(self._migration_metadata(total_row=16))
        request_call = next(
            call for call in spreadsheets.batchUpdate.call_args_list
            if "requests" in call.kwargs["body"]
        )
        copies = [request["copyPaste"] for request in request_call.kwargs["body"]["requests"] if "copyPaste" in request]

        self.assertEqual(len(copies), 39)
        january_copies = [copy for copy in copies if copy["source"]["sheetId"] == 2]
        self.assertEqual(
            january_copies,
            [
                {"source": {"sheetId": 2, "startRowIndex": 15, "endRowIndex": 16, "startColumnIndex": 5, "endColumnIndex": 7}, "destination": {"sheetId": 2, "startRowIndex": 17, "endRowIndex": 18, "startColumnIndex": 5, "endColumnIndex": 7}, "pasteType": "PASTE_FORMAT"},
                {"source": {"sheetId": 2, "startRowIndex": 14, "endRowIndex": 15, "startColumnIndex": 5, "endColumnIndex": 7}, "destination": {"sheetId": 2, "startRowIndex": 15, "endRowIndex": 16, "startColumnIndex": 5, "endColumnIndex": 7}, "pasteType": "PASTE_FORMAT"},
                {"source": {"sheetId": 2, "startRowIndex": 14, "endRowIndex": 15, "startColumnIndex": 5, "endColumnIndex": 7}, "destination": {"sheetId": 2, "startRowIndex": 16, "endRowIndex": 17, "startColumnIndex": 5, "endColumnIndex": 7}, "pasteType": "PASTE_FORMAT"},
            ],
        )

        total_copy_index = next(
            index for index, copy in enumerate(january_copies)
            if copy["source"]["startRowIndex"] == 15
        )
        source_overwrite_index = next(
            index for index, copy in enumerate(january_copies)
            if copy["destination"]["startRowIndex"] == 15
        )
        self.assertLess(total_copy_index, source_overwrite_index)

    def test_migration_applies_format_preservation_before_values(self):
        metadata = self._migration_metadata(total_row=16)
        service = Mock()
        spreadsheets = service.spreadsheets.return_value
        spreadsheets.get.return_value.execute.side_effect = [metadata, metadata, metadata]
        events = []

        def execute_sheet_batch():
            requests = spreadsheets.batchUpdate.call_args.kwargs["body"]["requests"]
            events.append("format" if any("copyPaste" in request for request in requests) else "post")

        spreadsheets.batchUpdate.return_value.execute.side_effect = execute_sheet_batch
        spreadsheets.values.return_value.batchUpdate.return_value.execute.side_effect = (
            lambda: events.append("values")
        )

        SheetsGateway(service=service, spreadsheet_id="sheet-id").migrate_template()

        self.assertEqual(events, ["format", "values", "post"])

    def test_migration_retries_format_preservation_after_values_failure(self):
        metadata = self._migration_metadata(total_row=16)
        service = Mock()
        spreadsheets = service.spreadsheets.return_value
        spreadsheets.get.return_value.execute.side_effect = [metadata] * 6
        events = []

        def execute_sheet_batch():
            requests = spreadsheets.batchUpdate.call_args.kwargs["body"]["requests"]
            events.append("format" if any("copyPaste" in request for request in requests) else "post")

        def execute_values():
            if "values-failed" not in events:
                events.append("values-failed")
                raise RuntimeError("values unavailable")
            events.append("values")

        spreadsheets.batchUpdate.return_value.execute.side_effect = execute_sheet_batch
        spreadsheets.values.return_value.batchUpdate.return_value.execute.side_effect = execute_values
        gateway = SheetsGateway(service=service, spreadsheet_id="sheet-id")

        with self.assertRaisesRegex(RuntimeError, "values unavailable"):
            gateway.migrate_template()
        gateway.migrate_template()

        self.assertEqual(events, ["format", "values-failed", "format", "values", "post"])

    def test_migration_retries_post_requests_without_overwriting_correct_formats(self):
        legacy = self._migration_metadata(total_row=16)
        corrected = self._migration_metadata(total_row=18)
        service = Mock()
        spreadsheets = service.spreadsheets.return_value
        spreadsheets.get.return_value.execute.side_effect = [legacy, legacy, legacy, corrected, corrected, corrected]
        events = []

        def execute_sheet_batch():
            requests = spreadsheets.batchUpdate.call_args.kwargs["body"]["requests"]
            if any("copyPaste" in request for request in requests):
                events.append("format")
            elif "post-failed" not in events:
                events.append("post-failed")
                raise RuntimeError("post migration unavailable")
            else:
                events.append("post")

        spreadsheets.batchUpdate.return_value.execute.side_effect = execute_sheet_batch
        spreadsheets.values.return_value.batchUpdate.return_value.execute.side_effect = (
            lambda: events.append("values")
        )
        gateway = SheetsGateway(service=service, spreadsheet_id="sheet-id")

        with self.assertRaisesRegex(RuntimeError, "post migration unavailable"):
            gateway.migrate_template()
        gateway.migrate_template()

        self.assertEqual(events, ["format", "values", "post-failed", "values", "post"])

    def test_migration_reads_only_bounded_category_layout_ranges_for_formats(self):
        spreadsheets = self._migrate_with_metadata(self._migration_metadata())
        format_calls = [
            call for call in spreadsheets.get.call_args_list
            if "ranges" in call.kwargs
        ]

        self.assertEqual(len(format_calls), 1)
        self.assertEqual(
            format_calls[0].kwargs["ranges"],
            [
                *[f"'{title}'!F4:G18" for title in MONTH_SHEETS.values()],
                "Podsumowanie!A4:N18",
            ],
        )
        self.assertEqual(
            format_calls[0].kwargs["fields"],
            "sheets(properties(sheetId,title),data(startRow,startColumn,rowData(values(formattedValue,userEnteredValue))))",
        )

    def test_migration_skips_format_copies_when_total_is_already_in_target_row(self):
        spreadsheets = self._migrate_with_metadata(self._migration_metadata(total_row=18))
        request_call = next(
            call for call in spreadsheets.batchUpdate.call_args_list
            if "requests" in call.kwargs["body"]
        )
        self.assertFalse(any("copyPaste" in request for request in request_call.kwargs["body"]["requests"]))

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

    def test_approved_add_overwrites_next_table_row_without_shifting_summary(self):
        service = Mock()
        gateway = SheetsGateway(service=service, spreadsheet_id="sheet-id")
        gateway.read_review_rows = Mock(
            return_value=[
                [
                    "Approved",
                    "ADD",
                    "2026-07-17",
                    "Еда и продукты",
                    "Lidl",
                    12.34,
                    "monobank",
                    0.95,
                    "MCC 5411",
                    "",
                    "p1",
                    "t1",
                    "fp",
                    "{}",
                ]
            ]
        )

        result = gateway.apply_approved()

        self.assertEqual(result, {"synced": 1, "conflicts": 0, "errors": 0})
        monthly_append = next(
            call
            for call in service.spreadsheets.return_value.values.return_value.append.call_args_list
            if call.kwargs["range"] == "'Lipiec'!A4:D"
        )
        self.assertEqual(monthly_append.kwargs["insertDataOption"], "OVERWRITE")

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

    def test_chart_ranges_recover_after_transaction_rows_shift_summary(self):
        spec = {
            "ranges": [
                {
                    "sheetId": 1,
                    "startRowIndex": 5,
                    "endRowIndex": 19,
                    "startColumnIndex": 5,
                    "endColumnIndex": 6,
                },
                {
                    "sheetId": 1,
                    "startRowIndex": 5,
                    "endRowIndex": 19,
                    "startColumnIndex": 6,
                    "endColumnIndex": 7,
                },
            ]
        }

        result = extend_chart_ranges(
            spec,
            sheet_id=1,
            category_end_row=17,
            allowed_columns={5, 6},
        )

        self.assertEqual(
            [
                (item["startRowIndex"], item["endRowIndex"])
                for item in result["ranges"]
            ],
            [(3, 17), (3, 17)],
        )

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
        monthly_shifted_daily = {"basicChart": {
            "domains": [{"domain": {"sourceRange": {"sources": [
                {"sheetId": 2, "startRowIndex": 5, "endRowIndex": 36, "startColumnIndex": 8, "endColumnIndex": 9}
            ]}}}],
            "series": [{"series": {"sourceRange": {"sources": [
                {"sheetId": 2, "startRowIndex": 5, "endRowIndex": 36, "startColumnIndex": 9, "endColumnIndex": 10}
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
                {"chartId": 22, "spec": monthly_shifted_daily},
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
        spreadsheets.get.return_value.execute.side_effect = [metadata, metadata, metadata]

        SheetsGateway(service=service, spreadsheet_id="sheet-id").migrate_template()

        requests = spreadsheets.batchUpdate.call_args.kwargs["body"]["requests"]
        sort_requests = [request["sortRange"] for request in requests if "sortRange" in request]
        self.assertEqual(len(sort_requests), 1)
        self.assertEqual(sort_requests[0]["range"]["startRowIndex"], 1)
        self.assertEqual(sort_requests[0]["range"]["endColumnIndex"], 14)
        self.assertEqual(sort_requests[0]["sortSpecs"], [{"dimensionIndex": 2, "sortOrder": "ASCENDING"}])
        chart_requests = [request["updateChartSpec"] for request in requests if "updateChartSpec" in request]
        self.assertEqual([request["chartId"] for request in chart_requests], [11, 20, 22])
        summary_ranges = chart_requests[0]["spec"]["pieChart"]
        self.assertEqual(summary_ranges["domain"]["sourceRange"]["sources"][0]["endRowIndex"], 17)
        self.assertEqual(summary_ranges["series"]["sourceRange"]["sources"][0]["endRowIndex"], 17)
        monthly = chart_requests[1]["spec"]["basicChart"]
        self.assertEqual(monthly["domains"][0]["domain"]["sourceRange"]["sources"][0]["endRowIndex"], 17)
        self.assertEqual(monthly["series"][0]["series"]["sourceRange"]["sources"][0]["endRowIndex"], 17)
        daily = chart_requests[2]["spec"]["basicChart"]
        daily_sources = [
            daily["domains"][0]["domain"]["sourceRange"]["sources"][0],
            daily["series"][0]["series"]["sourceRange"]["sources"][0],
        ]
        self.assertEqual(
            [(source["startRowIndex"], source["endRowIndex"]) for source in daily_sources],
            [(3, 34), (3, 34)],
        )


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
