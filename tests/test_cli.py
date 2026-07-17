import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfoNotFoundError

from expense_agent.cli import build_parser, run
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
