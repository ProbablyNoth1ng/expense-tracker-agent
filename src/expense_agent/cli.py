from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Sequence
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from googleapiclient.errors import HttpError  # type: ignore[import-untyped]

from .bootstrap import build_services
from .chat import ChatService, LangChainCommandParser
from .config import Settings
from .locking import ProcessLock
from .logging_utils import configure_logging
from .scheduler import install_task
from .workflows import SyncState, build_sync_graph


def _configure_output_encoding() -> None:
    """Ensure transaction descriptions and categories print on legacy Windows consoles."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="backslashreplace")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="expense-agent", description="Review-first Monobank expense agent")
    subparsers = parser.add_subparsers(dest="command", required=True)
    setup = subparsers.add_parser("setup", help="Authorize services and migrate the spreadsheet template")
    setup.add_argument("--dry-run", action="store_true")
    subparsers.add_parser("sync", help="Apply approved proposals, then fetch new transactions")
    subparsers.add_parser("apply", help="Apply currently approved Review rows")
    chat = subparsers.add_parser("chat", help="Create an ADD or EDIT proposal from natural language")
    chat.add_argument("text", nargs="+")
    subparsers.add_parser("doctor", help="Check local configuration without changing data")
    subparsers.add_parser("install-schedule", help="Install the daily 23:59 Windows task")
    return parser


def _doctor(settings: Settings) -> int:
    missing = settings.missing_live_settings()
    if missing:
        print("Configuration incomplete:")
        for name in missing:
            print(f"- {name}")
        print("Copy .env.example to .env and follow docs/google-setup.md.")
        return 2
    try:
        ZoneInfo(settings.timezone)
    except ZoneInfoNotFoundError:
        print(f"Timezone data unavailable for {settings.timezone}.")
        print("Install project dependencies with: python -m pip install -e .")
        return 2
    print("Local configuration files and required values are present.")
    print("Run `expense-agent setup --dry-run` to test authenticated access without migration.")
    return 0


def _setup(settings: Settings, *, dry_run: bool) -> int:
    services = build_services(settings)
    try:
        services.sheets.validate_template()
        accounts = services.monobank.list_accounts()
        print("Available Monobank accounts:")
        for index, account in enumerate(accounts, start=1):
            masked = ", ".join(account["masked_pan"]) or account["type"]
            print(f"{index}. {masked} (currency code {account['currency_code']})")
        if dry_run:
            print("Dry run successful; no spreadsheet changes made.")
            return 0
        selection = input("Select account numbers separated by commas: ")
        selected_indexes = {int(item.strip()) for item in selection.split(",") if item.strip()}
        selected = [account["id"] for index, account in enumerate(accounts, start=1) if index in selected_indexes]
        if not selected:
            raise RuntimeError("No valid account selected")
        services.backup.create(settings.spreadsheet_id)
        services.sheets.migrate_template()
        services.save_selected_accounts(selected)
        print(f"Setup complete. Selected {len(selected)} account(s).")
        return 0
    finally:
        services.close()


def _sync(settings: Settings) -> int:
    with ProcessLock(settings.database_path.parent / "agent.lock"):
        services = build_services(settings)
        try:
            services.backup.create(settings.spreadsheet_id)
            graph = build_sync_graph(services)
            result = graph.invoke({"account_ids": services.selected_accounts(), "now": datetime.now(tz=UTC)})
            _print_detected_expenses(result)
            return 0
        finally:
            services.close()


def _print_detected_expenses(result: SyncState) -> None:
    expenses = result.get("detected_expenses", [])
    print("Detected expenses:")
    for expense in expenses:
        original = f"{expense.original_amount:.2f} {expense.original_currency}"
        converted = (
            f" ({expense.amount_pln:.2f} PLN)"
            if expense.amount_pln is not None and expense.original_currency != "PLN"
            else ""
        )
        category = f"  {expense.suggested_category}" if expense.suggested_category else ""
        print(
            f"{expense.outcome:<21} {expense.date.isoformat()}  {expense.merchant:<20} "
            f"{original}{converted}{category}"
        )
    print()
    print(f"Detected: {len(expenses)}")
    print(f"New proposals: {result.get('new_count', 0)}")
    print(f"Already in database: {result.get('already_in_database_count', 0)}")
    print(f"Already in monthly sheets: {result.get('matched_existing_count', 0)}")
    if result.get("incoming_count", 0) or result.get("included_held_count", 0):
        print(
            f"Ignored incoming: {result.get('incoming_count', 0)}; "
            f"included held outgoing: {result.get('included_held_count', 0)}"
        )


def _apply(settings: Settings) -> int:
    with ProcessLock(settings.database_path.parent / "agent.lock"):
        services = build_services(settings)
        try:
            services.backup.create(settings.spreadsheet_id)
            result = services.sheets.apply_approved()
            print(f"Synced: {result['synced']}; conflicts: {result['conflicts']}; errors: {result['errors']}")
            return 0 if result["errors"] == 0 else 1
        finally:
            services.close()


def _chat(settings: Settings, text: str) -> int:
    services = build_services(settings)
    try:
        parser = LangChainCommandParser(api_key=settings.openai_api_key, model=settings.openai_model)
        def select(candidates: list[dict[str, object]]) -> int:
            print("Multiple matching expenses:")
            for index, candidate in enumerate(candidates, start=1):
                print(f"{index}. {candidate['sheet']} row {candidate['row']}: {candidate['values']}")
            chosen = int(input("Choose a row number: "))
            if not 1 <= chosen <= len(candidates):
                raise ValueError("Invalid candidate selection")
            return chosen - 1

        proposal = ChatService(parser=parser, sheets=services.sheets, selector=select).create_proposal(text)
        print(f"Proposal {proposal.id} added to Review with status Pending.")
        return 0
    finally:
        services.close()


def run(argv: Sequence[str] | None = None) -> int:
    _configure_output_encoding()
    args = build_parser().parse_args(argv)
    settings = Settings.from_env()
    logger = configure_logging(
        settings.log_dir,
        secrets=[settings.monobank_token, settings.openai_api_key],
    )
    logger.info("command=%s", args.command)
    try:
        if args.command == "doctor":
            return _doctor(settings)
        if args.command == "setup":
            return _setup(settings, dry_run=args.dry_run)
        if args.command == "sync":
            return _sync(settings)
        if args.command == "apply":
            return _apply(settings)
        if args.command == "chat":
            return _chat(settings, " ".join(args.text))
        if args.command == "install-schedule":
            install_task(project_dir=Path.cwd(), schedule_time=settings.schedule_time)
            print("Windows task installed for 23:59 Europe/Warsaw.")
            return 0
        raise RuntimeError(f"Unknown command: {args.command}")
    except (RuntimeError, ValueError, OSError, HttpError) as exc:
        logger.error("command=%s failed: %s", args.command, exc)
        print(f"Error: {exc}", file=sys.stderr)
        return 2


def main() -> None:
    raise SystemExit(run())
