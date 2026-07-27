import os
import io
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfoNotFoundError

from expense_agent.cli import _configure_output_encoding, _print_detected_expenses, build_parser, run
from expense_agent.models import DetectedExpense
from expense_agent.notifications import WindowsNotifier
from expense_agent.scheduler import build_task_xml


class SchedulerTests(unittest.TestCase):
    def test_task_xml_uses_2359_and_runs_after_missed_start(self):
        xml = build_task_xml(
            executable=r"C:\project\.venv\Scripts\expense-agent.exe",
            working_directory=r"C:\project",
            schedule_time="23:59",
        )
        self.assertIn("T23:59:00", xml)
        self.assertIn("<StartWhenAvailable>true</StartWhenAvailable>", xml)
        self.assertIn("expense-agent.exe", xml)
        self.assertIn("sync", xml)


class NotificationTests(unittest.TestCase):
    def test_windows_notifier_uses_hidden_powershell(self):
        runner = Mock()
        notifier = WindowsNotifier(runner=runner)
        notifier.notify("Expense agent", "New: 2")
        args = runner.call_args.args[0]
        self.assertEqual(args[0], "powershell.exe")
        self.assertIn("-WindowStyle", args)
        self.assertIn("Hidden", args)


class CliTests(unittest.TestCase):
    def test_utf8_output_prevents_charmap_errors_for_transaction_text(self):
        output = io.BytesIO()
        error = io.BytesIO()
        stdout = io.TextIOWrapper(output, encoding="cp1252", write_through=True)
        stderr = io.TextIOWrapper(error, encoding="cp1252", write_through=True)
        result = {
            "detected_expenses": [
                DetectedExpense(
                    "NEW",
                    date(2026, 7, 19),
                    "\u017babka",
                    42.5,
                    "PLN",
                    42.5,
                    "\u0407\u0436\u0430 \u0456 \u043f\u0440\u043e\u0434\u0443\u043a\u0442\u0438",
                )
            ]
        }
        with (
            patch("expense_agent.cli.sys.stdout", stdout),
            patch("expense_agent.cli.sys.stderr", stderr),
        ):
            _configure_output_encoding()
            _print_detected_expenses(result)
            stdout.flush()

        rendered = output.getvalue().decode("utf-8")
        self.assertIn("\u017babka", rendered)
        self.assertIn("\u0407\u0436\u0430 \u0456 \u043f\u0440\u043e\u0434\u0443\u043a\u0442\u0438", rendered)

    def test_sync_output_is_readable_and_contains_no_sensitive_identifiers(self):
        result = {
            "detected_expenses": [
                DetectedExpense("NEW", date(2026, 7, 18), "Żabka", 42.5, "PLN", 42.5, "Їжа і продукти"),
                DetectedExpense("ALREADY_IN_DATABASE", date(2026, 7, 17), "Steam", 39.99, "USD"),
                DetectedExpense("ALREADY_IN_SHEET", date(2026, 7, 16), "Lidl", 10.0, "PLN", 10.0),
            ],
            "new_count": 1,
            "already_in_database_count": 1,
            "matched_existing_count": 1,
        }
        with patch("builtins.print") as printer:
            _print_detected_expenses(result)

        rendered = "\n".join(str(call.args[0]) if call.args else "" for call in printer.call_args_list)
        self.assertIn("Żabka", rendered)
        self.assertIn("Їжа і продукти", rendered)
        self.assertIn("39.99 USD", rendered)
        self.assertIn("Detected: 3", rendered)
        self.assertNotIn("account-secret", rendered)
        self.assertNotIn("transaction-secret", rendered)
        self.assertNotIn("fingerprint", rendered)
        self.assertNotIn("token", rendered.casefold())

    def test_parser_exposes_all_public_commands(self):
        parser = build_parser()
        for command in ("setup", "sync", "apply", "chat", "doctor", "install-schedule"):
            namespace = parser.parse_args([command] + (["hello"] if command == "chat" else []))
            self.assertEqual(namespace.command, command)

    def test_doctor_returns_actionable_missing_configuration(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = {
                "DATABASE_PATH": str(Path(tmp) / "state.db"),
                "GOOGLE_CREDENTIALS_PATH": str(Path(tmp) / "missing.json"),
            }
            with (
                patch.dict(os.environ, env, clear=True),
                patch("dotenv.load_dotenv", return_value=False),
                patch("builtins.print") as printer,
            ):
                code = run(["doctor"])
            self.assertEqual(code, 2)
            rendered = " ".join(str(call) for call in printer.call_args_list)
            self.assertIn("MONOBANK_TOKEN", rendered)
            self.assertIn("GOOGLE_SPREADSHEET_ID", rendered)

    def test_doctor_returns_actionable_error_when_timezone_data_is_unavailable(self):
        with tempfile.TemporaryDirectory() as tmp:
            credentials = Path(tmp) / "credentials.json"
            credentials.touch()
            env = {
                "GOOGLE_SPREADSHEET_ID": "spreadsheet-id",
                "MONOBANK_TOKEN": "monobank-token",
                "OPENAI_API_KEY": "openai-key",
                "GOOGLE_CREDENTIALS_PATH": str(credentials),
            }
            with (
                patch.dict(os.environ, env, clear=True),
                patch("dotenv.load_dotenv", return_value=False),
                patch(
                    "expense_agent.cli.ZoneInfo",
                    side_effect=ZoneInfoNotFoundError("Europe/Warsaw"),
                ),
                patch("builtins.print") as printer,
            ):
                code = run(["doctor"])

            self.assertEqual(code, 2)
            rendered = " ".join(str(call) for call in printer.call_args_list)
            self.assertIn("Europe/Warsaw", rendered)
            self.assertIn("pip install -e", rendered)

    def test_google_api_error_returns_code_two_without_traceback(self):
        class FakeHttpError(Exception):
            pass

        settings = Mock(log_dir=Path("logs"), monobank_token="", openai_api_key="")
        logger = Mock()
        with (
            patch("expense_agent.cli.HttpError", FakeHttpError),
            patch("expense_agent.cli.Settings.from_env", return_value=settings),
            patch("expense_agent.cli.configure_logging", return_value=logger),
            patch("expense_agent.cli._setup", side_effect=FakeHttpError("bad chart")),
            patch("builtins.print") as printer,
        ):
            code = run(["setup"])

        self.assertEqual(code, 2)
        self.assertIn("Error: bad chart", " ".join(str(call) for call in printer.call_args_list))
        logger.error.assert_called_once()


if __name__ == "__main__":
    unittest.main()
