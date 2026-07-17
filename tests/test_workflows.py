import unittest
from datetime import UTC, date, datetime
from unittest.mock import Mock

from expense_agent.chat import ChatService
from expense_agent.workflows import build_sync_graph


class WorkflowTests(unittest.TestCase):
    def test_sync_applies_before_fetch_and_commits_cursor_after_stage(self):
        events = []
        services = Mock()
        services.settings.initial_sync_date = date(2026, 7, 1)
        services.settings.timezone = "Europe/Warsaw"
        services.store.get_cursor.return_value = None
        services.store.reserve_transaction.return_value = True
        services.sheets.apply_approved.side_effect = lambda: events.append("apply") or {"synced": 0}
        raw = Mock(
            id="t1", account_id="a", time=int(datetime(2026, 7, 2, tzinfo=UTC).timestamp()),
            description="Lidl", counter_name="", comment="", mcc=5411, amount=-1000,
            operation_amount=-1000, currency_code=985, hold=False,
        )
        services.monobank.iter_statements.side_effect = lambda *args: events.append("fetch") or iter([raw])
        services.fx.convert.side_effect = lambda *args: (10.0, 1.0, date(2026, 7, 2))
        services.categorizer.categorize.return_value = Mock(category="Еда и продукты", confidence=0.95, reason="MCC")
        services.sheets.stage_proposals.side_effect = lambda proposals: events.append("stage")
        services.store.set_cursor.side_effect = lambda *args: events.append("cursor")
        services.notifier.notify.side_effect = lambda *args, **kwargs: events.append("notify")

        graph = build_sync_graph(services)
        result = graph.invoke({"account_ids": ["a"], "now": datetime(2026, 7, 3, tzinfo=UTC)})

        self.assertLess(events.index("apply"), events.index("fetch"))
        self.assertLess(events.index("stage"), events.index("cursor"))
        self.assertEqual(result["new_count"], 1)

    def test_cursor_is_not_committed_when_staging_fails(self):
        services = Mock()
        services.settings.initial_sync_date = date(2026, 7, 1)
        services.settings.timezone = "Europe/Warsaw"
        services.store.get_cursor.return_value = None
        services.store.reserve_transaction.return_value = True
        raw = Mock(
            id="t1", account_id="a", time=int(datetime(2026, 7, 2, tzinfo=UTC).timestamp()),
            description="Lidl", counter_name="", comment="", mcc=5411, amount=-1000,
            operation_amount=-1000, currency_code=985, hold=False,
        )
        services.monobank.iter_statements.return_value = iter([raw])
        services.fx.convert.return_value = (10.0, 1.0, date(2026, 7, 2))
        services.categorizer.categorize.return_value = Mock(category="Еда и продукты", confidence=0.9, reason="MCC")
        services.sheets.stage_proposals.side_effect = RuntimeError("Google unavailable")
        graph = build_sync_graph(services)
        with self.assertRaises(RuntimeError):
            graph.invoke({"account_ids": ["a"], "now": datetime(2026, 7, 3, tzinfo=UTC)})
        services.store.set_cursor.assert_not_called()


class ChatTests(unittest.TestCase):
    def test_chat_creates_proposal_but_never_applies_it(self):
        parser = Mock()
        parser.parse.return_value = {
            "action": "ADD", "date": "2026-07-16", "category": "Кафе и рестораны",
            "merchant": "McDonald's", "amount_pln": 24.0,
        }
        sheets = Mock()
        service = ChatService(parser=parser, sheets=sheets)
        proposal = service.create_proposal("add 24 zł McDonald's today")
        self.assertEqual(proposal.action, "ADD")
        self.assertIsNone(proposal.target)
        sheets.stage_proposals.assert_called_once()
        sheets.find_expense_candidates.assert_not_called()
        sheets.apply_approved.assert_not_called()

    def test_chat_rejects_delete_requests(self):
        parser = Mock()
        parser.parse.return_value = {"action": "DELETE"}
        with self.assertRaisesRegex(ValueError, "ADD and EDIT"):
            ChatService(parser=parser, sheets=Mock()).create_proposal("delete it")

    def test_edit_resolves_a_unique_sheet_candidate_optimistically(self):
        parser = Mock()
        parser.parse.return_value = {
            "action": "EDIT", "date": "2026-07-16", "category": "Кафе и рестораны",
            "merchant": "McDonald's", "amount_pln": 30.0,
        }
        sheets = Mock()
        sheets.find_expense_candidates.return_value = [
            {"sheet": "Lipiec", "row": 12, "values": ["2026-07-15", "Еда и продукты", "McDonald's", 24.0]}
        ]
        proposal = ChatService(parser=parser, sheets=sheets).create_proposal("change McDonald's to 30 zł")
        self.assertEqual(proposal.target["sheet"], "Lipiec")
        self.assertEqual(proposal.target["expected"][3], 24.0)


if __name__ == "__main__":
    unittest.main()
