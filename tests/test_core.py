import tempfile
import unittest
from datetime import UTC, date, datetime
from pathlib import Path
from unittest.mock import Mock

from expense_agent.categorization import Categorizer, CategoryDecision, LangChainCategoryModel
from expense_agent.config import Settings
from expense_agent.constants import CATEGORIES, MONTH_SHEETS
from expense_agent.fx import NbpClient, convert_to_pln
from expense_agent.models import RawTransaction
from expense_agent.normalization import normalize_transaction
from expense_agent.storage import StateStore


class ConfigTests(unittest.TestCase):
    def test_defaults_lock_project_constraints(self):
        settings = Settings.from_mapping({})
        self.assertEqual(settings.year, 2026)
        self.assertEqual(settings.initial_sync_date, date(2026, 7, 1))
        self.assertEqual(settings.timezone, "Europe/Warsaw")
        self.assertEqual(settings.openai_model, "gpt-5.4-mini")
        self.assertEqual(settings.schedule_time, "23:59")

    def test_category_order_and_months_are_stable(self):
        self.assertEqual(len(CATEGORIES), 14)
        self.assertEqual(CATEGORIES[-4:], ("Прочее", "Для дома", "Переводы", "Игры"))
        self.assertEqual(MONTH_SHEETS[7], "Lipiec")


class NormalizationTests(unittest.TestCase):
    def test_settled_outgoing_pln_transaction_is_normalized(self):
        raw = RawTransaction(
            id="mono-1",
            account_id="acc-1",
            time=int(datetime(2026, 7, 2, 12, tzinfo=UTC).timestamp()),
            description="LIDL POLSKA SKLEP 0123",
            mcc=5411,
            amount=-12_345,
            operation_amount=-12_345,
            currency_code=985,
            hold=False,
        )
        result = normalize_transaction(raw)
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.date, date(2026, 7, 2))
        self.assertEqual(result.original_amount, 123.45)
        self.assertEqual(result.original_currency, "PLN")
        self.assertEqual(result.merchant, "Lidl")

    def test_held_outgoing_is_normalized_but_incoming_is_ignored(self):
        base = dict(
            id="x",
            account_id="a",
            time=int(datetime(2026, 7, 2, tzinfo=UTC).timestamp()),
            description="Shop",
            mcc=5411,
            operation_amount=-100,
            currency_code=985,
        )
        self.assertIsNotNone(normalize_transaction(RawTransaction(**base, amount=-100, hold=True)))
        self.assertIsNone(normalize_transaction(RawTransaction(**base, amount=100, hold=False)))

    def test_other_year_is_rejected(self):
        raw = RawTransaction(
            id="old", account_id="a", time=int(datetime(2025, 1, 1, tzinfo=UTC).timestamp()),
            description="Shop", mcc=1, amount=-100, operation_amount=-100,
            currency_code=985, hold=False,
        )
        with self.assertRaisesRegex(ValueError, "2026"):
            normalize_transaction(raw)


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = StateStore(Path(self.tmp.name) / "state.db")
        self.store.initialize()

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_transactions_are_reserved_idempotently(self):
        self.assertTrue(self.store.reserve_transaction("mono-1", "fp-1"))
        self.assertFalse(self.store.reserve_transaction("mono-1", "fp-1"))

    def test_cursor_and_merchant_rules_persist(self):
        cursor = datetime(2026, 7, 3, tzinfo=UTC)
        self.store.set_cursor("acc", cursor)
        self.assertEqual(self.store.get_cursor("acc"), cursor)
        self.store.upsert_merchant_rule("lidl", 5411, "Еда и продукты")
        self.assertEqual(self.store.find_merchant_rule("lidl", 5411), "Еда и продукты")

    def test_reconciliation_marker_persists_per_account(self):
        start = datetime(2026, 6, 17, tzinfo=UTC)
        end = datetime(2026, 7, 18, tzinfo=UTC)
        self.assertFalse(self.store.reconciliation_completed("acc"))
        self.store.mark_reconciliation_complete("acc", start=start, end=end)
        self.assertTrue(self.store.reconciliation_completed("acc"))

    def test_proposal_lifecycle_is_auditable(self):
        proposal_id = self.store.create_proposal(
            action="ADD", source_transaction_id="mono-1", payload={"amount_pln": 10.0}
        )
        self.store.set_proposal_status(proposal_id, "Approved")
        row = self.store.get_proposal(proposal_id)
        self.assertEqual(row["status"], "Approved")
        self.assertEqual(row["payload"]["amount_pln"], 10.0)

    def test_existing_proposal_id_can_be_recorded(self):
        self.store.record_proposal(
            proposal_id="p-fixed", action="ADD", source_transaction_id="mono-2", payload={"merchant": "Lidl"}
        )
        self.assertEqual(self.store.get_proposal("p-fixed")["payload"]["merchant"], "Lidl")


class FxTests(unittest.TestCase):
    def test_pln_is_passthrough(self):
        self.assertEqual(convert_to_pln(12.34, "PLN", date(2026, 7, 1), Mock()), (12.34, 1.0, date(2026, 7, 1)))

    def test_nbp_falls_back_to_previous_publication(self):
        transport = Mock()
        transport.get.side_effect = [
            Mock(status_code=404),
            Mock(status_code=200, json=lambda: {"rates": [{"mid": 0.09, "effectiveDate": "2026-07-03"}]}),
        ]
        client = NbpClient(transport=transport)
        rate, effective = client.rate_for("UAH", date(2026, 7, 5))
        self.assertEqual((rate, effective), (0.09, date(2026, 7, 3)))
        self.assertEqual(transport.get.call_count, 2)


class CategorizationTests(unittest.TestCase):
    def test_transfer_mcc_wins_before_merchant_rules_and_model(self):
        store = Mock()
        model = Mock()
        result = Categorizer(store=store, model=model).categorize("Steam recipient", 4829, 50.0)
        self.assertEqual(result, CategoryDecision("Переводы", 1.0, "MCC 4829", "mcc_rule"))
        store.find_merchant_rule.assert_not_called()
        model.classify.assert_not_called()

    def test_builtin_merchant_rules_normalize_variants(self):
        store = Mock()
        store.find_merchant_rule.return_value = None
        model = Mock()
        games = "Игры"
        subscriptions = "Подписки"
        food = "Еда и продукты"
        health = "Здоровье / аптека"
        entertainment = "Развлечения"
        cases = (
            ("Steam Games", games),
            ("EPIC-games", games),
            ("G.O.G.", games),
            ("Battle . net", games),
            ("Blizzard Entertainment", games),
            ("EA.COM", games),
            ("EA Games Store", games),
            ("EA.COM FIFA", games),
            ("UBISOFT STORE", games),
            ("Riot-Game Store", games),
            ("Xbox Game Pass", subscriptions),
            ("PlayStation Plus", subscriptions),
            ("PS Plus", subscriptions),
            ("Nintendo Switch Online", subscriptions),
            ("OpenAI / ChatGPT", subscriptions),
            ("GOOGLE*SERVICE", subscriptions),
            ("Żabka 12", food),
            ("ZABKA-12", food),
            ("Rossmann", health),
            ("HEBE Polska", health),
            ("Kwiaciarnia Róża", entertainment),
            ("FLOWER SHOP", entertainment),
        )
        categorizer = Categorizer(store=store, model=model)
        for merchant, category in cases:
            with self.subTest(merchant=merchant):
                self.assertEqual(categorizer.categorize(merchant, 9999, 50.0).category, category)
        model.classify.assert_not_called()

    def test_subscription_rules_win_over_broader_game_rules(self):
        store = Mock()
        store.find_merchant_rule.return_value = None
        model = Mock()
        result = Categorizer(store=store, model=model).categorize("Xbox Game Pass", 9999, 50.0)
        self.assertEqual(result.category, "Подписки")
        model.classify.assert_not_called()

    def test_ea_rule_does_not_match_unrelated_merchant_prefixes(self):
        store = Mock()
        store.find_merchant_rule.return_value = None
        model = Mock()
        model.classify.return_value = CategoryDecision("Кафе и рестораны", 0.9, "model result")
        categorizer = Categorizer(store=store, model=model)
        for merchant in ("Eataly Warszawa", "EasyJet"):
            with self.subTest(merchant=merchant):
                self.assertEqual(
                    categorizer.categorize(merchant, 9999, 50.0).category,
                    "Кафе и рестораны",
                )
        self.assertEqual(model.classify.call_count, 2)

    def test_builtin_merchant_rules_do_not_match_compact_substrings(self):
        store = Mock()
        store.find_merchant_rule.return_value = None
        model = Mock()
        expected = CategoryDecision(CATEGORIES[0], 0.9, "model result")
        model.classify.return_value = expected
        categorizer = Categorizer(store=store, model=model)

        for merchant in ("GOGO Sushi", "The Best Coffee"):
            with self.subTest(merchant=merchant):
                self.assertEqual(categorizer.categorize(merchant, 9999, 50.0), expected)

        self.assertEqual(model.classify.call_count, 2)

    def test_builtin_merchant_rules_match_bounded_ea_and_riot_variants(self):
        store = Mock()
        store.find_merchant_rule.return_value = None
        model = Mock()
        categorizer = Categorizer(store=store, model=model)

        for merchant in ("Riot Games", "RIOTGAMES", "EAGAMES", "EASPORTS", "EACOM"):
            with self.subTest(merchant=merchant):
                self.assertEqual(categorizer.categorize(merchant, 9999, 50.0).category, CATEGORIES[-1])

        model.classify.assert_not_called()

    def test_builtin_merchant_rules_keep_lookalikes_as_model_candidates(self):
        store = Mock()
        store.find_merchant_rule.return_value = None
        model = Mock()
        expected = CategoryDecision(CATEGORIES[6], 0.9, "model result")
        model.classify.return_value = expected
        categorizer = Categorizer(store=store, model=model)

        for merchant in ("GOGO Sushi", "The Best Coffee", "EasyJet", "Eataly"):
            with self.subTest(merchant=merchant):
                self.assertEqual(categorizer.categorize(merchant, 9999, 50.0), expected)

        self.assertEqual(model.classify.call_count, 4)

    def test_builtin_merchant_rule_wins_over_learned_rule(self):
        store = Mock()
        store.find_merchant_rule.return_value = "Прочее"
        model = Mock()
        result = Categorizer(store=store, model=model).categorize("Steam", 9999, 50.0)
        self.assertEqual(result.category, "Игры")
        self.assertEqual(result.source, "merchant_rule")
        model.classify.assert_not_called()

    def test_known_merchant_rule_wins_without_model_call(self):
        store = Mock()
        store.find_merchant_rule.return_value = "Еда и продукты"
        model = Mock()
        result = Categorizer(store=store, model=model).categorize("Lidl", 5411, 50.0)
        self.assertEqual(result.category, "Еда и продукты")
        self.assertEqual(result.source, "merchant_rule")
        model.classify.assert_not_called()

    def test_model_receives_only_sanitized_fields(self):
        store = Mock()
        store.find_merchant_rule.return_value = None
        model = Mock()
        model.classify.return_value = CategoryDecision("Кафе и рестораны", 0.8, "restaurant")
        result = Categorizer(store=store, model=model).categorize("Ambiguous Shop", 9999, 32.0)
        self.assertEqual(result.category, "Кафе и рестораны")
        kwargs = model.classify.call_args.kwargs
        self.assertEqual(set(kwargs), {"merchant", "mcc", "amount_band", "categories"})

    def test_model_failure_falls_back_to_other(self):
        store = Mock()
        store.find_merchant_rule.return_value = None
        model = Mock()
        model.classify.side_effect = RuntimeError("offline")
        result = Categorizer(store=store, model=model).categorize("Unknown", 9999, 7.0)
        self.assertEqual(result.category, "Прочее")
        self.assertEqual(result.confidence, 0.0)

    def test_model_unknown_or_low_confidence_falls_back_to_other(self):
        store = Mock()
        store.find_merchant_rule.return_value = None
        model = Mock()
        categorizer = Categorizer(store=store, model=model)
        for decision in (
            CategoryDecision("Unknown", 0.9, "bad category"),
            CategoryDecision("Подписки", 0.64, "not confident"),
        ):
            with self.subTest(decision=decision):
                model.classify.return_value = decision
                result = categorizer.categorize("Ambiguous", 9999, 50.0)
                self.assertEqual(result.category, "Прочее")
                self.assertEqual(result.confidence, 0.0)
    def test_model_non_finite_or_out_of_range_confidence_falls_back_to_other(self):
        store = Mock()
        store.find_merchant_rule.return_value = None
        model = Mock()
        categorizer = Categorizer(store=store, model=model)
        for confidence in (float("nan"), float("inf"), -0.1, 1.1):
            with self.subTest(confidence=confidence):
                model.classify.return_value = CategoryDecision(CATEGORIES[0], confidence, "invalid confidence")
                result = categorizer.categorize("Ambiguous", 9999, 50.0)
                self.assertEqual(result.category, CATEGORIES[-4])
                self.assertEqual(result.confidence, 0.0)


class LangChainCategoryModelTests(unittest.TestCase):
    def test_classify_converts_mapping_result_to_category_decision(self):
        model = object.__new__(LangChainCategoryModel)
        model.structured = Mock()
        model.structured.invoke.return_value = {
            "category": "Подписки",
            "confidence": 0.8,
            "reason": "recurring service",
            "source": "structured",
        }
        result = model.classify(merchant="Google", mcc=9999, amount_band="small", categories=CATEGORIES)
        self.assertEqual(
            result,
            CategoryDecision("Подписки", 0.8, "recurring service", "structured"),
        )

    def test_classify_keeps_category_decision_result(self):
        model = object.__new__(LangChainCategoryModel)
        model.structured = Mock()
        expected = CategoryDecision("Игры", 0.9, "game store")
        model.structured.invoke.return_value = expected
        result = model.classify(merchant="Steam", mcc=9999, amount_band="small", categories=CATEGORIES)
        self.assertIs(result, expected)


if __name__ == "__main__":
    unittest.main()
